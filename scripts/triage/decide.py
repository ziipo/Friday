"""Decision matrix per PRD §5.2.3.

Inputs: triage score JSON + provenance + reputation.
Output: a Decision enum the watcher can act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from lib.tuning import get as _tune


class Decision(str, Enum):
    DISCARD = "discard"                 # spam or below relevance floor
    LINK_DUPLICATE = "link_duplicate"   # known duplicate; link to existing record
    ARCHIVE_ONLY = "archive_only"       # write archive_record, no fast-track
    FAST_TRACK = "fast_track"           # archive AND queue for memory promotion


# PRD §5.2.3 thresholds — read from tuning.yaml, fall back to PRD defaults.
LOW_FLOOR: float = _tune("triage", "low_floor", 0.2)
HIGH_FLOOR: float = _tune("triage", "high_floor", 0.7)


@dataclass(frozen=True)
class TriageScore:
    relevance_score: float
    rationale: str
    duplicate_of: str | None
    spam: bool
    one_line_summary: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TriageScore":
        score_raw = data.get("relevance_score", 0.0)
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        return cls(
            relevance_score=max(0.0, min(1.0, score)),
            rationale=str(data.get("rationale") or "").strip(),
            duplicate_of=(str(data["duplicate_of"]).strip() or None) if data.get("duplicate_of") else None,
            spam=bool(data.get("spam", False)),
            one_line_summary=str(data.get("one_line_summary") or "").strip(),
        )


def decide(score: TriageScore, *, channel_score: float = 0.5, sender_score: float = 0.5) -> Decision:
    """Apply PRD §5.2.3 decision matrix with a small reputation boost.

    Reputation effect is intentionally modest: the Triage prompt already considers
    reputation when scoring. We add up to ±0.1 here as a guardrail so a sender
    we've already flagged as noisy can't sneak through if the model is
    over-generous on a single item."""
    if score.spam:
        return Decision.DISCARD
    if score.duplicate_of:
        return Decision.LINK_DUPLICATE

    reputation_adjustment = ((channel_score - 0.5) + (sender_score - 0.5)) * 0.1
    adjusted = score.relevance_score + reputation_adjustment

    if adjusted < LOW_FLOOR:
        return Decision.DISCARD
    if adjusted >= HIGH_FLOOR:
        return Decision.FAST_TRACK
    return Decision.ARCHIVE_ONLY
