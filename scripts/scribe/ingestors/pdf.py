"""PDF ingestor: stage raw PDF + extracted text."""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from lib import paths
from lib.archive_record import make_artifact
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, Provenance

source_type = "pdf"
poll_interval_seconds = 0


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_text(pdf_path: Path) -> tuple[str, str | None]:
    parts: list[str] = []
    title: str | None = None
    with pdfplumber.open(str(pdf_path)) as pdf:
        meta = pdf.metadata or {}
        if meta.get("Title"):
            title = str(meta["Title"]).strip() or None
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            parts.append(text)
            if title is None and i == 0:
                first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
                if first_line and len(first_line) <= 200:
                    title = first_line
    return "\n\n".join(parts), title


def ingest_path(path: Path) -> list[CandidateRecord]:
    captured_at = datetime.now(timezone.utc)
    seed = f"pdf::{_content_hash(path)}"
    arc_id = archive_id(seed, captured_at)

    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, rendered_dir / path.name)

    text, title = _extract_text(path)
    (clean_dir / (path.stem + ".txt")).write_text(text, encoding="utf-8")

    candidate = CandidateRecord(
        source_type="pdf",
        captured_via="watcher",
        arc_id=arc_id,
        seed=seed,
        captured_at=captured_at,
        canonical_url=None,
        title=title or path.stem,
        provenance=Provenance(
            shared_by="self",
            context=f"manual capture: {path.name}",
            shared_at=captured_at,
        ),
        artifacts=[
            make_artifact(arc_id, "raw", path.name),
            make_artifact(arc_id, "clean", path.stem + ".txt"),
        ],
        extra={"sha256": seed.split("::", 1)[1]},
    )
    log_event("scribe.pdf", "staged",
              arc_id=arc_id, source=str(path), chars=len(text))
    return [candidate]
