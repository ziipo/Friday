"""Inbox watcher per PRD §5.1.1.

Monitors Inbox/ for new files. On each new file:
1. Detect source type by extension.
2. Dispatch to the appropriate ingestor.
3. Move the original to Inbox/processed/ on success or Inbox/failed/ with an error log.

Run as `python -m scribe.watcher` (with PYTHONPATH=scripts) or via the launchd agent.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from lib import paths
from lib.logging import log_event

# Ingestor registry: file extension → callable(path) -> list[CandidateRecord].
# Imported lazily so a broken ingestor module doesn't kill the whole watcher.
def _load_ingestors() -> dict[str, Callable]:
    from scribe.ingestors import email as email_ingestor
    from scribe.ingestors import markdown as markdown_ingestor
    from scribe.ingestors import pdf as pdf_ingestor
    from scribe.ingestors import web as web_ingestor

    return {
        ".url": web_ingestor.ingest_path,
        ".md": markdown_ingestor.ingest_path,
        ".markdown": markdown_ingestor.ingest_path,
        ".pdf": pdf_ingestor.ingest_path,
        ".eml": email_ingestor.ingest_path,
    }


def _is_settled(path: Path, idle_seconds: float = 1.0, max_wait: float = 30.0) -> bool:
    """Return True once the file's size has been stable for `idle_seconds`.

    Browsers and Sync clients can drop a file in chunks. We wait until the size
    stops growing rather than racing the writer.
    """
    deadline = time.monotonic() + max_wait
    last_size = -1
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size != last_size:
            last_size = size
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_seconds:
            return True
        time.sleep(0.2)
    return False


def _move_with_collision_safety(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    candidate = dst_dir / src.name
    if not candidate.exists():
        shutil.move(str(src), str(candidate))
        return candidate
    stem, ext = candidate.stem, candidate.suffix
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    deduped = dst_dir / f"{stem}.{suffix}{ext}"
    shutil.move(str(src), str(deduped))
    return deduped


def _vault_commit(source_name: str) -> None:
    """Stage all Friday repo changes and create an ingest commit (PRD §5.6)."""
    repo = paths.FRIDAY_ROOT
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"],
            check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if result.returncode == 0:
            return  # nothing staged
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m",
             f"ingest: {source_name} [{ts}]"],
            check=True, capture_output=True,
        )
        log_event("scribe.watcher", "vault.commit", source=source_name, ts=ts)
    except subprocess.CalledProcessError as exc:
        log_event("scribe.watcher", "vault.commit_error",
                  source=source_name, error=exc.stderr.decode(errors="replace").strip())


def process_file(path: Path, ingestors: dict[str, Callable]) -> None:
    from scribe.pipeline import process_candidates

    ext = path.suffix.lower()
    ingestor = ingestors.get(ext)
    if ingestor is None:
        log_event("scribe.watcher", "skip.unknown_extension", path=str(path), ext=ext)
        _move_with_collision_safety(path, paths.INBOX_FAILED)
        return

    log_event("scribe.watcher", "ingest.start", path=str(path), ingestor=ingestor.__module__)
    try:
        if not _is_settled(path):
            log_event("scribe.watcher", "ingest.unsettled", path=str(path))
            return  # Skip; the next event will re-trigger.
        candidates = ingestor(path)
        results = process_candidates(candidates)
        summary = ", ".join(f"{r.get('arc_id') or 'rejected'}:{r['decision']}" for r in results)
        log_event("scribe.watcher", "pipeline.ok", path=str(path), results=summary)
    except Exception as exc:
        log_event("scribe.watcher", "ingest.error",
                  path=str(path), error=type(exc).__name__, message=str(exc))
        moved = _move_with_collision_safety(path, paths.INBOX_FAILED)
        err_log = moved.with_suffix(moved.suffix + ".error.log")
        err_log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return

    moved = _move_with_collision_safety(path, paths.INBOX_PROCESSED)
    log_event("scribe.watcher", "ingest.ok",
              path=str(path), moved_to=str(moved), candidates=len(candidates))
    _vault_commit(path.name)


class InboxHandler(FileSystemEventHandler):
    def __init__(self, ingestors: dict[str, Callable]):
        self.ingestors = ingestors

    def _eligible(self, path: Path) -> bool:
        # Ignore files inside Inbox/processed/ or Inbox/failed/.
        try:
            rel = path.relative_to(paths.INBOX)
        except ValueError:
            return False
        if len(rel.parts) > 1:
            return False
        if path.name.startswith("."):
            return False
        return True

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not self._eligible(path):
            return
        process_file(path, self.ingestors)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # A file moved INTO Inbox (e.g., browser writes to a temp file then renames).
        dest = Path(event.dest_path)
        if not self._eligible(dest):
            return
        process_file(dest, self.ingestors)


def scan_existing(ingestors: dict[str, Callable]) -> None:
    """Process anything already sitting in Inbox/ at startup."""
    for entry in sorted(paths.INBOX.iterdir()):
        if entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        process_file(entry, ingestors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday inbox watcher.")
    parser.add_argument("--once", action="store_true",
                        help="Scan inbox once and exit (no watch loop).")
    args = parser.parse_args()

    paths.ensure_dirs()
    ingestors = _load_ingestors()
    log_event("scribe.watcher", "startup",
              inbox=str(paths.INBOX), extensions=sorted(ingestors.keys()))

    scan_existing(ingestors)
    if args.once:
        return 0

    observer = Observer()
    observer.schedule(InboxHandler(ingestors), str(paths.INBOX), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    log_event("scribe.watcher", "shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
