"""Web ingestor: .url file → ArchiveBox → staged artifacts.

Workflow per PRD §5.1.7:
1. Read URL from .url file (Windows-style INI or plain-text).
2. Shell out to `archivebox add "$URL#$timestamp"` — fragment is the documented
   workaround for ArchiveBox's "one snapshot per URL" limitation.
3. Locate the resulting snapshot folder.
4. Copy singlefile.html → Archive/Rendered/{arc_id}/, readability content → Archive/Clean/{arc_id}/.
5. Return CandidateRecord; pipeline triages and writes the archive_record.
"""
from __future__ import annotations

import configparser
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib import paths
from lib.archive_record import make_artifact
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, Provenance

source_type = "web"
poll_interval_seconds = 0


def _parse_url_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if text.startswith("["):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(text)
        if cp.has_option("InternetShortcut", "URL"):
            return cp.get("InternetShortcut", "URL").strip()
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
    snapshot_dir = paths.ARCHIVEBOX_ARCHIVE / new[-1]
    log_event("scribe.web", "archivebox.add.ok", url=url, snapshot=str(snapshot_dir))
    return snapshot_dir


def _read_title(snapshot_dir: Path) -> str | None:
    index = snapshot_dir / "index.json"
    if not index.exists():
        return None
    try:
        return json.loads(index.read_text(encoding="utf-8")).get("title")
    except json.JSONDecodeError:
        return None


def ingest_url(
    url: str,
    *,
    provenance: Provenance | None = None,
    captured_via: str = "watcher",
    extra: dict | None = None,
) -> list[CandidateRecord]:
    """Run a URL through ArchiveBox and stage artifacts. The pollers (Drive,
    Slack, Calendar) call this directly when they encounter an external URL,
    instead of writing a tempfile and routing through ingest_path."""
    snapshot_dir = _run_archivebox_add(url)
    title = _read_title(snapshot_dir)

    captured_at = datetime.now(timezone.utc)
    seed = url
    arc_id = archive_id(seed, captured_at)

    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    artifacts = []
    singlefile = snapshot_dir / "singlefile.html"
    if singlefile.exists():
        shutil.copy2(singlefile, rendered_dir / "singlefile.html")
        artifacts.append(make_artifact(arc_id, "rendered", "singlefile.html"))
    readability_html = snapshot_dir / "readability" / "content.html"
    readability_txt = snapshot_dir / "readability" / "content.txt"
    if readability_html.exists():
        shutil.copy2(readability_html, clean_dir / "content.html")
        artifacts.append(make_artifact(arc_id, "clean", "content.html"))
    if readability_txt.exists():
        shutil.copy2(readability_txt, clean_dir / "content.txt")
        artifacts.append(make_artifact(arc_id, "clean", "content.txt"))
    if not artifacts:
        raise RuntimeError(
            f"archivebox snapshot {snapshot_dir} produced no usable artifacts"
        )

    merged_extra = {"archivebox_snapshot": snapshot_dir.name}
    if extra:
        merged_extra.update(extra)
    if provenance is None:
        provenance = Provenance(shared_by="self", shared_at=captured_at)

    candidate = CandidateRecord(
        source_type="web",
        captured_via=captured_via,
        arc_id=arc_id,
        seed=seed,
        captured_at=captured_at,
        canonical_url=url,
        title=title,
        provenance=provenance,
        artifacts=artifacts,
        extra=merged_extra,
    )
    log_event("scribe.web", "staged", arc_id=arc_id, url=url, captured_via=captured_via)
    return [candidate]


def ingest_path(path: Path) -> list[CandidateRecord]:
    url = _parse_url_file(path)
    return ingest_url(
        url,
        captured_via="watcher",
        provenance=Provenance(
            shared_by="self",
            context=f"manual capture via {path.name}",
            shared_at=datetime.now(timezone.utc),
        ),
    )
