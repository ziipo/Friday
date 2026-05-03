"""Markdown ingestor: stage .md into Archive/Clean/, preserve frontmatter."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from lib import paths
from lib.archive_record import make_artifact
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, Provenance

source_type = "markdown"
poll_interval_seconds = 0


def ingest_path(path: Path) -> list[CandidateRecord]:
    captured_at = datetime.now(timezone.utc)
    text = path.read_text(encoding="utf-8")
    post = frontmatter.loads(text)

    title = post.metadata.get("title") or path.stem
    canonical_url = post.metadata.get("canonical_url") or post.metadata.get("url")
    summary = post.metadata.get("one_line_summary") or post.metadata.get("summary")
    seed = canonical_url or f"md::{path.name}::{captured_at.isoformat()}"
    arc_id = archive_id(seed, captured_at)

    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    clean_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, clean_dir / path.name)

    candidate = CandidateRecord(
        source_type="markdown",
        captured_via="watcher",
        arc_id=arc_id,
        seed=seed,
        captured_at=captured_at,
        canonical_url=canonical_url,
        title=title,
        one_line_summary=summary,
        provenance=Provenance(
            shared_by="self",
            context=f"manual capture: {path.name}",
            shared_at=captured_at,
        ),
        artifacts=[make_artifact(arc_id, "clean", path.name)],
        extra={"upstream_frontmatter": dict(post.metadata)} if post.metadata else {},
    )
    log_event("scribe.markdown", "staged",
              arc_id=arc_id, source=str(path), title=title)
    return [candidate]
