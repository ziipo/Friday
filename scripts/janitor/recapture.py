"""Re-capture support for the nightly sweep (PRD §5.5.1 step 1 + 2).

For each promoted memory record with a web canonical_url:
  1. Re-run ArchiveBox to get a fresh snapshot.
  2. Copy the new singlefile.html / readability text into Archive/{Rendered,Clean}/{arc_id}/
     as a new timestamped artifact alongside the original.
  3. Diff the new clean artifact against the previous version.
  4. Ask the LLM to classify: trivial | notable | breaking.
  5. If notable or breaking → write a ReviewQueue proposal.
  6. Update the archive_record's artifacts list + last_verified timestamp.

Re-capture is best-effort: a failed URL is logged and skipped, not fatal.
"""
from __future__ import annotations

import difflib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from lib import llm, paths
from lib.logging import log_event


RECAPTURABLE_SOURCE_TYPES = {"web"}
STALE_DAYS = 30
DIFF_PREVIEW_LINES = 60  # lines sent to LLM for diff classification


def _is_recapturable(meta: dict[str, Any]) -> bool:
    return (
        meta.get("source_type") in RECAPTURABLE_SOURCE_TYPES
        and bool(meta.get("canonical_url"))
    )


def _last_verified(meta: dict[str, Any]) -> datetime | None:
    raw = meta.get("last_verified")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_archivebox(url: str) -> Path | None:
    """Invoke archivebox add and return the new snapshot dir, or None on failure."""
    before: set[str] = set()
    if paths.ARCHIVEBOX_ARCHIVE.exists():
        before = {p.name for p in paths.ARCHIVEBOX_ARCHIVE.iterdir() if p.is_dir()}
    fragment = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    target_url = f"{url}#{fragment}"
    try:
        subprocess.run(
            ["archivebox", "add", "--extract=singlefile,readability,title,favicon",
             "--update-all=False", target_url],
            cwd=str(paths.ARCHIVEBOX_DATA),
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log_event("janitor.recapture", "archivebox.fail", url=url,
                  error=type(exc).__name__, message=str(exc)[:200])
        return None
    after = {p.name for p in paths.ARCHIVEBOX_ARCHIVE.iterdir() if p.is_dir()}
    new = sorted(after - before)
    if not new:
        log_event("janitor.recapture", "archivebox.no_new_snapshot", url=url)
        return None
    return paths.ARCHIVEBOX_ARCHIVE / new[-1]


def _copy_artifacts(snapshot_dir: Path, arc_id: str, ts: str) -> list[dict[str, str]]:
    """Copy singlefile.html and readability text into Archive dirs as timestamped files."""
    new_artifacts: list[dict[str, str]] = []
    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    singlefile = snapshot_dir / "singlefile.html"
    if singlefile.exists():
        dest = rendered_dir / f"singlefile_{ts}.html"
        shutil.copy2(singlefile, dest)
        new_artifacts.append({"path": str(Path("Archive/Rendered") / arc_id / dest.name), "type": "rendered"})

    for fname in ("content.txt", "article.txt"):
        readability = snapshot_dir / "readability" / fname
        if readability.exists():
            dest = clean_dir / f"readability_{ts}.txt"
            shutil.copy2(readability, dest)
            new_artifacts.append({"path": str(Path("Archive/Clean") / arc_id / dest.name), "type": "clean"})
            break

    return new_artifacts


def _read_clean_text(arc_id: str) -> tuple[str, str]:
    """Return (previous_text, latest_text) from Archive/Clean/{arc_id}/."""
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    if not clean_dir.exists():
        return "", ""
    candidates = sorted(clean_dir.glob("*.txt")) + sorted(clean_dir.glob("*.md"))
    if not candidates:
        return "", ""
    if len(candidates) == 1:
        return "", candidates[0].read_text(encoding="utf-8", errors="replace")
    latest = candidates[-1].read_text(encoding="utf-8", errors="replace")
    previous = candidates[-2].read_text(encoding="utf-8", errors="replace")
    return previous, latest


def _classify_diff(title: str, previous: str, latest: str) -> str:
    """Ask the LLM to classify the diff. Returns 'trivial' | 'notable' | 'breaking'."""
    diff_lines = list(difflib.unified_diff(
        previous.splitlines(), latest.splitlines(),
        fromfile="previous", tofile="latest", lineterm="",
    ))[:DIFF_PREVIEW_LINES]
    if not diff_lines:
        return "trivial"
    diff_text = "\n".join(diff_lines)
    prompt = (
        "You are a maintenance assistant for a personal knowledge base. "
        "Classify a diff between two versions of a captured document.\n\n"
        "Respond with exactly one word: trivial, notable, or breaking.\n\n"
        "- trivial: whitespace, timestamps, ads, nav menus, minor wording\n"
        "- notable: new sections, updated figures, meaningful new content\n"
        "- breaking: content fundamentally changed or removed, factual reversal\n\n"
        f"Document title: {title}\n\nDiff (first {DIFF_PREVIEW_LINES} lines):\n```\n{diff_text}\n```"
    )
    try:
        raw = llm.complete(
            system="Classify the diff as exactly one word: trivial, notable, or breaking.",
            messages=[{"role": "user", "content": prompt}],
            job="triage",
            temperature=0.0,
        ).strip().lower()
        if raw in ("trivial", "notable", "breaking"):
            return raw
    except Exception as exc:
        log_event("janitor.recapture", "diff_classify.fail",
                  error=type(exc).__name__, message=str(exc))
    return "trivial"


def _write_diff_proposal(src_id: str, title: str, classification: str, diff_preview: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title[:40]).strip("_")
    fname = f"{ts}_diff_{slug}.md"
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    proposal_path = paths.REVIEW_QUEUE_PENDING / fname
    content = (
        f"---\ntype: diff_flag\nsrc_id: {src_id}\nclassification: {classification}\n"
        f"created_at: {ts}\n---\n\n"
        f"# Diff flag: {classification.upper()} — {title}\n\n"
        f"Source: `{src_id}`\n\n## Diff preview\n\n```diff\n{diff_preview}\n```\n"
    )
    proposal_path.write_text(content, encoding="utf-8")
    return proposal_path


def _update_memory_record(src_path: Path, new_artifacts: list[dict], ts_iso: str) -> None:
    post = frontmatter.load(src_path)
    existing = list(post.metadata.get("artifacts") or [])
    existing.extend(new_artifacts)
    post["artifacts"] = existing
    post["last_verified"] = ts_iso
    yaml_text = yaml.safe_dump(dict(post.metadata), sort_keys=False, allow_unicode=True)
    src_path.write_text(f"---\n{yaml_text}---\n\n{post.content}", encoding="utf-8")


def recapture_all(*, dry_run: bool = False) -> dict[str, int]:
    """Re-capture all eligible promoted memory records. Returns summary counts."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    counts = {"checked": 0, "recaptured": 0, "flagged": 0, "skipped": 0, "errors": 0}

    sources_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not sources_dir.exists():
        return counts

    for src_path in sorted(sources_dir.glob("src_*.md")):
        try:
            post = frontmatter.load(src_path)
        except Exception:
            counts["errors"] += 1
            continue
        meta: dict[str, Any] = post.metadata

        if meta.get("status") not in ("active", None, ""):
            continue

        arc_id = meta.get("archive_record") or ""
        if not arc_id:
            continue

        # Load matching archive record to check source_type and url.
        arc_path = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
        if not arc_path.exists():
            continue
        try:
            arc_post = frontmatter.load(arc_path)
        except Exception:
            counts["errors"] += 1
            continue
        arc_meta: dict[str, Any] = arc_post.metadata

        counts["checked"] += 1
        if not _is_recapturable(arc_meta):
            counts["skipped"] += 1
            continue

        url = arc_meta["canonical_url"]
        title = str(meta.get("title") or arc_id)
        src_id = str(meta.get("id") or src_path.stem)

        log_event("janitor.recapture", "recapturing", src_id=src_id, url=url)
        if dry_run:
            print(f"  [dry-run] would recapture {src_id}: {url}")
            counts["recaptured"] += 1
            continue

        snapshot_dir = _run_archivebox(url)
        if not snapshot_dir:
            counts["errors"] += 1
            continue

        new_artifacts = _copy_artifacts(snapshot_dir, arc_id, ts)
        if not new_artifacts:
            counts["errors"] += 1
            continue

        previous, latest = _read_clean_text(arc_id)
        classification = _classify_diff(title, previous, latest)
        log_event("janitor.recapture", "diff.classified",
                  src_id=src_id, classification=classification)

        if classification in ("notable", "breaking"):
            diff_lines = list(difflib.unified_diff(
                (previous or "").splitlines(),
                (latest or "").splitlines(),
                fromfile="previous", tofile="latest", lineterm="",
            ))[:DIFF_PREVIEW_LINES]
            _write_diff_proposal(src_id, title, classification, "\n".join(diff_lines))
            counts["flagged"] += 1

        _update_memory_record(src_path, new_artifacts, now.isoformat().replace("+00:00", "Z"))
        counts["recaptured"] += 1

    return counts
