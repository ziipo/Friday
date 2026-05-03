"""Memory record writer per PRD §4.2.

Reads an existing archive_record.md, lifts forward the metadata that's still
relevant, and writes the full memory_record under Institutional-Memory/sources/.
The new memory record's `id` reuses the archive record's hash component for
traceability (PRD §4.5).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from lib import paths
from lib.ids import memory_id_from_archive

from .models import Relation, Synthesis


def _slug(title: str, fallback: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:60] or fallback


def memory_record_filename(src_id: str, title: str) -> str:
    return f"{src_id}_{_slug(title, src_id)}.md"


def memory_record_path(src_id: str, title: str) -> Path:
    return paths.INSTITUTIONAL_MEMORY / "sources" / memory_record_filename(src_id, title)


def _relation_dicts(relations: list[Relation]) -> list[dict[str, str]]:
    return [{"type": r.type, "target": r.target} for r in relations]


def write_memory_record(
    *,
    archive_record_path: Path,
    synthesis: Synthesis,
    promotion_reason: str = "relevance",
    engagement: str = "passing",
) -> tuple[str, Path]:
    """Create Institutional-Memory/sources/{src_id}_{slug}.md.

    Returns (src_id, written_path). The archive_record_path is the .md file
    under Archive/records/; we read its frontmatter to lift forward
    canonical_url, captured_at, source_type, and artifacts.
    """
    archive = frontmatter.load(archive_record_path)
    arc_id = archive.metadata["id"]
    src_id = memory_id_from_archive(arc_id)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    captured_at = archive.metadata.get("captured_at") or now

    metadata: dict[str, Any] = {
        "id": src_id,
        "type": "source",
        "source_type": archive.metadata.get("source_type"),
        "canonical_url": archive.metadata.get("canonical_url"),
        "archive_record": arc_id,
        "captured_at": captured_at,
        "last_verified": captured_at,
        "promoted_at": now,
        "promotion_reason": promotion_reason,
        "status": "active",
        "artifacts": archive.metadata.get("artifacts") or [],
        "relations": _relation_dicts(synthesis.relations),
        "tags": synthesis.tags,
        "engagement": engagement,
        "title": synthesis.title,
        "summary": synthesis.summary,
    }

    body = _render_body(synthesis)
    target = memory_record_path(src_id, synthesis.title)
    target.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    target.write_text(f"---\n{yaml_text}---\n\n{body}", encoding="utf-8")
    return src_id, target


def _render_body(s: Synthesis) -> str:
    lines: list[str] = [f"# {s.title}", ""]
    if s.long_summary:
        lines += ["## Summary", "", s.long_summary, ""]
    if s.key_points:
        lines += ["## Key points", ""]
        lines += [f"- {kp}" for kp in s.key_points]
        lines.append("")
    if s.notes:
        lines += ["## Notes", "", s.notes, ""]
    lines += ["## History", ""]
    return "\n".join(lines)


def update_archive_record_promoted(
    archive_record_path: Path, *, src_id: str
) -> None:
    """Stamp the archive record as promoted, pointing back to the new memory record."""
    post = frontmatter.load(archive_record_path)
    post.metadata["status"] = "promoted"
    post.metadata["promoted_to"] = src_id
    archive_record_path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
