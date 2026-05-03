"""Match engagement signals to archive records.

Each engagement signal may carry a `target_id` (arc_id) set directly by a
poller, or a `target_url` / `gdrive_file_id` / `slack_file_id` that we must
resolve against existing archive records.

Resolution order:
1. `target_id` present and record exists → done.
2. `target_url` → match against `canonical_url` in all archive records.
3. `gdrive_file_id` in signal extra → match against `extra.gdrive_file_id`.
4. `slack_file_id` in signal extra → match against `extra.slack_file_id`.

We build an in-memory index on first call and cache it for the run. The index
is cheap to rebuild (all archive records are frontmatter-only .md files).
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import frontmatter

from lib import paths
from lib.logging import log_event


@functools.lru_cache(maxsize=1)
def _build_index() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return three lookup dicts: url→arc_id, gdrive_file_id→arc_id, slack_file_id→arc_id."""
    by_url: dict[str, str] = {}
    by_gdrive: dict[str, str] = {}
    by_slack: dict[str, str] = {}

    if not paths.ARCHIVE_RECORDS.exists():
        return by_url, by_gdrive, by_slack

    for p in paths.ARCHIVE_RECORDS.glob("arc_*.md"):
        try:
            post = frontmatter.load(p)
        except Exception:
            continue
        meta: dict[str, Any] = post.metadata
        arc_id: str = meta.get("id") or p.stem
        status: str = meta.get("status") or ""
        if status in ("discarded",):
            continue

        url = meta.get("canonical_url")
        if url:
            by_url[url] = arc_id

        extra = meta.get("extra") or {}
        if isinstance(extra, dict):
            gdrive_id = extra.get("gdrive_file_id")
            if gdrive_id:
                by_gdrive[str(gdrive_id)] = arc_id
            slack_id = extra.get("slack_file_id")
            if slack_id:
                by_slack[str(slack_id)] = arc_id

    return by_url, by_gdrive, by_slack


def invalidate_index() -> None:
    """Call after writing new archive records so next lookup rebuilds."""
    _build_index.cache_clear()


def resolve_signal(signal: dict) -> str | None:
    """Return the arc_id this signal refers to, or None if unresolvable."""
    target_id = signal.get("target_id")
    if target_id:
        p = paths.ARCHIVE_RECORDS / f"{target_id}.md"
        if p.exists():
            return str(target_id)

    by_url, by_gdrive, by_slack = _build_index()

    target_url = signal.get("target_url")
    if target_url and target_url in by_url:
        return by_url[target_url]

    extra = signal.get("extra") or {}
    if isinstance(extra, dict):
        gdrive_id = extra.get("gdrive_file_id")
        if gdrive_id and str(gdrive_id) in by_gdrive:
            return by_gdrive[str(gdrive_id)]
        slack_id = extra.get("slack_file_id")
        if slack_id and str(slack_id) in by_slack:
            return by_slack[str(slack_id)]

    log_event("promoter.matcher", "unresolvable",
              signal_type=signal.get("type"), extra=str(extra)[:200])
    return None
