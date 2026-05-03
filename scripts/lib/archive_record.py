"""Archive record writer per PRD §4.1.

An archive record is a Markdown file in Archive/records/ containing only frontmatter.
Body is intentionally empty — archive records hold metadata + pointers, not analysis.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .ids import archive_id
from .protocol import Artifact, CandidateRecord, Provenance


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Unknown type — coerce via str() so a stray header object can't crash the writer.
    return str(value)


def _provenance_dict(p: Provenance) -> dict[str, Any]:
    return {
        "shared_by": p.shared_by,
        "shared_in": p.shared_in,
        "shared_at": _serialize(p.shared_at) if p.shared_at else None,
        "context": p.context,
    }


def write_archive_record(
    candidate: CandidateRecord,
    *,
    captured_at: datetime | None = None,
    seed: str | None = None,
) -> tuple[str, Path]:
    """Write an archive_record .md file. Returns (arc_id, path).

    The caller is responsible for placing the artifact files at the paths declared
    in `candidate.artifacts` BEFORE calling this — the writer does not move files.
    """
    paths.ensure_dirs()
    captured_at = captured_at or datetime.now(timezone.utc)
    seed_value = seed or candidate.canonical_url or candidate.title or captured_at.isoformat()
    arc_id = archive_id(seed_value, captured_at)

    frontmatter: dict[str, Any] = {
        "id": arc_id,
        "source_type": candidate.source_type,
        "captured_at": _serialize(captured_at),
        "captured_via": candidate.captured_via,
        "provenance": _provenance_dict(candidate.provenance),
        "canonical_url": candidate.canonical_url,
        "artifacts": [
            {"path": str(a.path), "type": a.type} for a in candidate.artifacts
        ],
        "title": candidate.title,
        "one_line_summary": candidate.one_line_summary or "",
        "relevance_score": 0.0,
        "engagement_score": 0.0,
        "status": "archived",
        "promoted_to": None,
    }
    if candidate.extra:
        frontmatter["extra"] = _serialize(candidate.extra)

    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    body = (
        "<!--\n"
        "Archive-tier record per PRD §4.1. Frontmatter-only by design.\n"
        "-->\n"
    )
    record_path = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
    record_path.write_text(f"---\n{yaml_text}---\n\n{body}", encoding="utf-8")
    return arc_id, record_path


def artifact_paths_for(arc_id: str) -> tuple[Path, Path]:
    """Conventional artifact directories for a given archive ID."""
    return (paths.ARCHIVE_RENDERED / arc_id, paths.ARCHIVE_CLEAN / arc_id)


def make_artifact(arc_id: str, kind: str, filename: str) -> Artifact:
    """Build an Artifact descriptor for a file that will live in
    Archive/Rendered/{arc_id}/{filename} or Archive/Clean/{arc_id}/{filename}.
    Path is rendered repo-relative (PRD §4.1)."""
    if kind == "rendered":
        rel = Path("Archive/Rendered") / arc_id / filename
    elif kind == "clean":
        rel = Path("Archive/Clean") / arc_id / filename
    elif kind == "raw":
        rel = Path("Archive/Rendered") / arc_id / filename  # raw lives alongside rendered
    else:
        raise ValueError(f"unknown artifact kind: {kind}")
    return Artifact(path=rel, type=kind)
