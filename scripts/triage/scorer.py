"""Triage scorer — ask the LLM to score one CandidateRecord.

Usage:
    score = scorer.score(candidate, content_excerpt, snapshot)
    decision = decide(score, channel_score=..., sender_score=...)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from lib import llm
from lib.logging import log_event
from lib.paths import FRIDAY_ROOT
from lib.protocol import CandidateRecord

from . import context as ctx_mod
from .decide import TriageScore

PROMPT_PATH = FRIDAY_ROOT / "prompts" / "triage.md"
EXCERPT_BUDGET = 6000  # chars from the artifact body sent to the LLM
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _format_provenance(c: CandidateRecord) -> str:
    p = c.provenance
    parts = [
        f"- source_type: {c.source_type}",
        f"- captured_via: {c.captured_via}",
        f"- canonical_url: {c.canonical_url or '(none)'}",
        f"- title: {c.title or '(none)'}",
    ]
    if p.shared_by:
        parts.append(f"- shared_by: {p.shared_by}")
    if p.shared_in:
        parts.append(f"- shared_in: {p.shared_in}")
    if p.shared_at:
        ts = p.shared_at.isoformat() if isinstance(p.shared_at, datetime) else str(p.shared_at)
        parts.append(f"- shared_at: {ts}")
    if p.context:
        parts.append(f"- context: {p.context}")
    return "\n".join(parts)


def _read_excerpt(candidate: CandidateRecord) -> str:
    """Pick the best clean artifact and read up to EXCERPT_BUDGET chars."""
    clean = [a for a in candidate.artifacts if a.type == "clean"]
    target = clean[0] if clean else (candidate.artifacts[0] if candidate.artifacts else None)
    if target is None:
        return "(no artifact attached)"
    abs_path = FRIDAY_ROOT / target.path
    if not abs_path.exists():
        return f"(artifact not found at {target.path})"
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return f"(could not read artifact: {exc})"
    if len(text) <= EXCERPT_BUDGET:
        return text
    head = text[: EXCERPT_BUDGET - 200]
    tail = text[-200:]
    return f"{head}\n…(truncated, {len(text) - EXCERPT_BUDGET} chars omitted)…\n{tail}"


def _parse_json(raw: str) -> dict:
    """LLMs sometimes wrap JSON in ``` fences or add prose. Extract robustly."""
    raw = raw.strip()
    fence_match = JSON_FENCE_RE.search(raw)
    if fence_match:
        raw = fence_match.group(1)
    # Strip any leading prose up to the first '{'
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in: {raw[:200]!r}")
    return json.loads(raw[start : end + 1])


def score(candidate: CandidateRecord, *, snapshot: ctx_mod.MemorySnapshot | None = None) -> TriageScore:
    snapshot = snapshot or ctx_mod.load_snapshot()
    memory_text = ctx_mod.render(snapshot)
    excerpt = _read_excerpt(candidate)
    system = PROMPT_PATH.read_text(encoding="utf-8")

    user_message = (
        "## Memory tier\n\n"
        f"{memory_text}\n\n"
        "## Candidate provenance\n\n"
        f"{_format_provenance(candidate)}\n\n"
        "## Candidate content (excerpt)\n\n"
        f"{excerpt}\n\n"
        "Return only the JSON object."
    )
    raw = llm.complete(
        system=system,
        messages=[{"role": "user", "content": user_message}],
        job="triage",
        temperature=0.0,
    )
    try:
        data = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log_event("triage", "parse_error",
                  candidate_url=candidate.canonical_url, error=str(exc), raw=raw[:500])
        # Defensive default: treat unparseable response as low-relevance archive_only.
        return TriageScore(
            relevance_score=0.3, rationale=f"parse_error: {exc}",
            duplicate_of=None, spam=False,
            one_line_summary=(candidate.title or "")[:120],
        )
    result = TriageScore.from_json(data)
    log_event("triage", "scored",
              candidate_url=candidate.canonical_url, title=candidate.title,
              score=result.relevance_score, spam=result.spam,
              duplicate_of=result.duplicate_of)
    return result
