"""Web ingestor: .url file → ArchiveBox → archive_record.

Workflow per PRD §5.1.7:
1. Read URL from .url file (Windows-style INI or plain-text).
2. Shell out to `archivebox add "$URL#$timestamp"` — fragment is the documented
   workaround for ArchiveBox's "one snapshot per URL" limitation.
3. Locate the resulting snapshot folder under archivebox-data/archive/.
4. Symlink/copy singlefile.html → Archive/Rendered/{arc_id}/, readability content → Archive/Clean/{arc_id}/.
5. Write archive_record .md.
"""
from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from lib import paths
from lib.archive_record import make_artifact, write_archive_record
from lib.logging import log_event
from lib.protocol import CandidateRecord, Provenance

source_type = "web"
poll_interval_seconds = 0  # watcher mode only at this phase


def _parse_url_file(path: Path) -> str:
    """Accept both Windows .url (INI with [InternetShortcut] URL=...) and plain text."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if text.startswith("["):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(text)
        if cp.has_option("InternetShortcut", "URL"):
            return cp.get("InternetShortcut", "URL").strip()
    # Plain text: first non-empty line that looks like a URL.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("http://", "https://")):
            return line
    raise ValueError(f"no URL found in {path}")


def _list_snapshots() -> set[str]:
    if not paths.ARCHIVEBOX_ARCHIVE.exists():
        return set()
    return {p.name for p in paths.ARCHIVEBOX_ARCHIVE.iterdir() if p.is_dir()}


def _run_archivebox_add(url: str) -> Path:
    """Invoke `archivebox add` and return the new snapshot directory."""
    before = _list_snapshots()
    fragment = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    target = f"{url}#{fragment}"
    log_event("scribe.web", "archivebox.add.start", url=url, target=target)
    proc = subprocess.run(
        ["archivebox", "add", target],
        cwd=str(paths.ARCHIVEBOX_DATA),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        log_event("scribe.web", "archivebox.add.failed",
                  url=url, returncode=proc.returncode, stderr=proc.stderr[-1000:])
        raise RuntimeError(f"archivebox add failed: {proc.stderr[-500:]}")
    after = _list_snapshots()
    new = sorted(after - before)
    if not new:
        raise RuntimeError(f"archivebox produced no new snapshot for {url!r}")
    # Most recent timestamp directory (numeric, sortable).
    snapshot_dir = paths.ARCHIVEBOX_ARCHIVE / new[-1]
    log_event("scribe.web", "archivebox.add.ok", url=url, snapshot=str(snapshot_dir))
    return snapshot_dir


def _read_title(snapshot_dir: Path) -> str | None:
    index = snapshot_dir / "index.json"
    if not index.exists():
        return None
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        return data.get("title")
    except json.JSONDecodeError:
        return None


def ingest_path(path: Path) -> list[CandidateRecord]:
    url = _parse_url_file(path)
    snapshot_dir = _run_archivebox_add(url)
    title = _read_title(snapshot_dir)

    captured_at = datetime.now(timezone.utc)
    seed = url + "#" + captured_at.isoformat()
    arc_id_seed = url  # stable seed → reproducible short hash for same URL

    # Stage artifacts under Archive/{Rendered,Clean}/{arc_id}/
    # We compute arc_id ourselves so we can place files BEFORE writing the record.
    from lib.ids import archive_id
    arc_id = archive_id(arc_id_seed, captured_at)

    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    artifacts = []
    singlefile = snapshot_dir / "singlefile.html"
    if singlefile.exists():
        dst = rendered_dir / "singlefile.html"
        shutil.copy2(singlefile, dst)
        artifacts.append(make_artifact(arc_id, "rendered", "singlefile.html"))

    readability_html = snapshot_dir / "readability" / "content.html"
    readability_txt = snapshot_dir / "readability" / "content.txt"
    if readability_html.exists():
        dst = clean_dir / "content.html"
        shutil.copy2(readability_html, dst)
        artifacts.append(make_artifact(arc_id, "clean", "content.html"))
    if readability_txt.exists():
        dst = clean_dir / "content.txt"
        shutil.copy2(readability_txt, dst)
        artifacts.append(make_artifact(arc_id, "clean", "content.txt"))

    if not artifacts:
        raise RuntimeError(
            f"archivebox snapshot {snapshot_dir} produced no usable artifacts "
            f"(no singlefile.html or readability/content.{{html,txt}})"
        )

    candidate = CandidateRecord(
        source_type="web",
        captured_via="watcher",
        canonical_url=url,
        title=title,
        provenance=Provenance(
            shared_by="self",
            context=f"manual capture via {path.name}",
            shared_at=captured_at,
        ),
        artifacts=artifacts,
        extra={"archivebox_snapshot": snapshot_dir.name},
    )
    written_id, record_path = write_archive_record(
        candidate, captured_at=captured_at, seed=arc_id_seed,
    )
    # The writer recomputes arc_id from (seed, captured_at). It must match the one
    # we used to lay out the artifact dirs — assert equality so any drift is loud.
    assert written_id == arc_id, f"arc_id drift: layout={arc_id} writer={written_id}"

    log_event("scribe.web", "archive_record.written",
              arc_id=arc_id, url=url, record_path=str(record_path))
    return [candidate]
