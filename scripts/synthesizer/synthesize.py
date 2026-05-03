"""Synthesizer orchestrator (PRD §5.4).

Given an arc_id (an archive record), produce:
  - One memory record under Institutional-Memory/sources/.
  - Created-or-updated entity pages.
  - Created-or-updated concept pages.
  - Optional ReviewQueue proposals when reconciliation flags fire.
  - Archive record stamped status=promoted, promoted_to=<src_id>.

Usage:
    python -m synthesizer.synthesize <arc_id> [--reason engagement|relevance|manual|transitive]

Or programmatically:
    from synthesizer.synthesize import synthesize_archive
    result = synthesize_archive("arc_2026-05-03T...", promotion_reason="relevance")
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from lib import llm, paths
from lib.logging import log_event
from triage import context as ctx_mod

from . import models
from .memory_record import (
    update_archive_record_promoted,
    write_memory_record,
)
from .reconcile import write_reconciliation_proposal
from .upsert import upsert_concept, upsert_entity

PROMPT_PATH = paths.FRIDAY_ROOT / "prompts" / "synthesize.md"
EXCERPT_BUDGET = 12000  # synthesis sees more than triage; we want fidelity


@dataclass
class SynthesisResult:
    arc_id: str
    src_id: str
    memory_record: Path
    entities_created: list[Path] = field(default_factory=list)
    entities_updated: list[Path] = field(default_factory=list)
    concepts_created: list[Path] = field(default_factory=list)
    concepts_updated: list[Path] = field(default_factory=list)
    review_proposals: list[Path] = field(default_factory=list)


def _resolve_archive_path(arc_id: str) -> Path:
    p = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
    if not p.exists():
        raise FileNotFoundError(f"archive record not found: {p}")
    return p


def _read_excerpt(archive_post: frontmatter.Post) -> str:
    """Read the best clean artifact for the LLM. Falls back to title only."""
    artifacts = archive_post.metadata.get("artifacts") or []
    clean = [a for a in artifacts if isinstance(a, dict) and a.get("type") == "clean"]
    target = clean[0] if clean else (artifacts[0] if artifacts else None)
    if not isinstance(target, dict) or not target.get("path"):
        return "(no artifact available)"
    abs_path = paths.FRIDAY_ROOT / target["path"]
    if not abs_path.exists():
        return f"(artifact missing: {target['path']})"
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return f"(could not read artifact: {exc})"
    if len(text) <= EXCERPT_BUDGET:
        return text
    head = text[: EXCERPT_BUDGET - 200]
    tail = text[-200:]
    return f"{head}\n…(truncated, {len(text) - EXCERPT_BUDGET} chars omitted)…\n{tail}"


def _format_provenance(meta: dict) -> str:
    prov = meta.get("provenance") or {}
    parts = [
        f"- source_type: {meta.get('source_type')}",
        f"- captured_via: {meta.get('captured_via')}",
        f"- captured_at: {meta.get('captured_at')}",
        f"- canonical_url: {meta.get('canonical_url') or '(none)'}",
        f"- title: {meta.get('title') or '(none)'}",
    ]
    if isinstance(prov, dict):
        for k in ("shared_by", "shared_in", "shared_at", "context"):
            v = prov.get(k)
            if v:
                parts.append(f"- {k}: {v}")
    return "\n".join(parts)


def synthesize_archive(
    arc_id: str,
    *,
    promotion_reason: str = "relevance",
    engagement: str = "passing",
) -> SynthesisResult:
    archive_path = _resolve_archive_path(arc_id)
    archive_post = frontmatter.load(archive_path)
    fallback_title = archive_post.metadata.get("title") or arc_id

    snapshot = ctx_mod.load_snapshot()
    memory_text = ctx_mod.render(snapshot)
    excerpt = _read_excerpt(archive_post)
    system = PROMPT_PATH.read_text(encoding="utf-8")

    user_message = (
        "## Memory tier (existing entities, concepts, recent sources)\n\n"
        f"{memory_text}\n\n"
        "## Source provenance\n\n"
        f"{_format_provenance(archive_post.metadata)}\n\n"
        "## Source content (excerpt)\n\n"
        f"{excerpt}\n\n"
        "Return only the JSON object."
    )
    log_event("synthesizer", "request", arc_id=arc_id, title=fallback_title)
    raw = llm.complete(
        system=system,
        messages=[{"role": "user", "content": user_message}],
        job="synthesis",
        temperature=0.0,
    )
    try:
        synthesis = models.from_llm_response(raw, fallback_title=str(fallback_title))
    except (ValueError, Exception) as exc:
        log_event("synthesizer", "parse_error",
                  arc_id=arc_id, error=type(exc).__name__, message=str(exc),
                  raw=raw[:500])
        raise

    src_id, memory_path = write_memory_record(
        archive_record_path=archive_path,
        synthesis=synthesis,
        promotion_reason=promotion_reason,
        engagement=engagement,
    )

    result = SynthesisResult(arc_id=arc_id, src_id=src_id, memory_record=memory_path)

    for entity in synthesis.entities:
        path, created = upsert_entity(entity, src_id=src_id)
        (result.entities_created if created else result.entities_updated).append(path)

    for concept in synthesis.concepts:
        path, created = upsert_concept(concept, src_id=src_id)
        (result.concepts_created if created else result.concepts_updated).append(path)

    for flag in synthesis.reconciliation:
        proposal = write_reconciliation_proposal(
            flag, src_id=src_id, archive_id=arc_id,
            source_title=synthesis.title,
        )
        result.review_proposals.append(proposal)

    update_archive_record_promoted(archive_path, src_id=src_id)

    log_event("synthesizer", "done",
              arc_id=arc_id, src_id=src_id,
              entities_created=len(result.entities_created),
              entities_updated=len(result.entities_updated),
              concepts_created=len(result.concepts_created),
              concepts_updated=len(result.concepts_updated),
              review_proposals=len(result.review_proposals))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthesize an archive record into memory.")
    parser.add_argument("arc_id", help="Archive record ID, e.g. arc_2026-05-03T...")
    parser.add_argument("--reason", default="relevance",
                        choices=["engagement", "relevance", "manual", "transitive"])
    parser.add_argument("--engagement", default="passing",
                        choices=["passing", "reviewed", "studied"])
    args = parser.parse_args()

    result = synthesize_archive(
        args.arc_id,
        promotion_reason=args.reason,
        engagement=args.engagement,
    )
    print(f"Wrote memory record: {result.memory_record.relative_to(paths.FRIDAY_ROOT)}")
    if result.entities_created:
        print(f"  entities created: {len(result.entities_created)}")
    if result.entities_updated:
        print(f"  entities updated: {len(result.entities_updated)}")
    if result.concepts_created:
        print(f"  concepts created: {len(result.concepts_created)}")
    if result.concepts_updated:
        print(f"  concepts updated: {len(result.concepts_updated)}")
    if result.review_proposals:
        print(f"  review proposals: {len(result.review_proposals)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
