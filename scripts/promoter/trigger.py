"""Promotion trigger logic per PRD §5.3.1.

Any one of these signals suffices to promote an archive record:

Engagement-based:
  - I commented or reacted on the document       → drive_comment, slack_reaction
  - I edited the document                        → drive_edit
  - I attended the meeting it was attached to,
    AND the meeting had ≤8 people                → calendar_attendance (size_bucket small|medium)
  - I opened it for >60s                         → drive_view (Phase 5 TODO: duration not yet
                                                   available from Activity API — treated as no-op
                                                   until Drive API exposes it)
  - I shared it forward / replied in thread      → slack_reply
  - Manual promotion via @promote tag or CLI     → handled upstream, not here

Relevance-based (no engagement needed):
  - Triage relevance_score ≥ 0.7                → FAST_TRACK records already handled
                                                   inline by the pipeline; the Promoter
                                                   still processes them to set engagement tag

Engagement tagging per PRD §5.3.4:
  - passing  — promoted by relevance alone (no personal signal)
  - reviewed — light engagement: reaction or brief view
  - studied  — substantial: comment, edit, attended small/medium meeting, thread reply
"""
from __future__ import annotations

from typing import Any

# Signal types that count as engagement and their weight class.
# "studied" outweighs "reviewed" — if any studied signal fires, that wins.
_SIGNAL_WEIGHT: dict[str, str] = {
    "drive_comment": "studied",
    "drive_edit": "studied",
    "slack_reply": "studied",
    "calendar_attendance": "studied",   # only for small/medium meetings (checked below)
    "drive_view": "reviewed",
    "slack_reaction": "reviewed",
    "calendar_organized": "reviewed",
}

_WEIGHT_ORDER = ["studied", "reviewed", "passing"]


def _calendar_bucket(signal: dict[str, Any]) -> str | None:
    extra = signal.get("extra") or {}
    return extra.get("size_bucket") if isinstance(extra, dict) else None


def engagement_tag(signals: list[dict[str, Any]]) -> str:
    """Return the highest engagement level across all signals for one record."""
    best = "passing"
    for sig in signals:
        stype = sig.get("type") or ""
        weight = _SIGNAL_WEIGHT.get(stype)
        if weight is None:
            continue
        # calendar_attendance only counts for small/medium meetings per PRD §5.3.1.
        if stype == "calendar_attendance":
            bucket = _calendar_bucket(sig)
            if bucket not in ("small", "medium"):
                weight = "reviewed"  # large meeting → lighter signal
        if _WEIGHT_ORDER.index(weight) < _WEIGHT_ORDER.index(best):
            best = weight
    return best


def should_promote(
    archive_meta: dict[str, Any],
    signals: list[dict[str, Any]],
) -> bool:
    """Return True if this record should be promoted to Memory tier.

    Promotion is triggered by ANY one of:
    1. Already FAST_TRACK from Triage (relevance_score ≥ 0.7 → status=fast_tracked
       OR extra.triage.fast_track=True).
    2. At least one qualifying engagement signal.
    """
    status = archive_meta.get("status") or ""
    if status == "promoted":
        return False  # already done

    # Relevance path: FAST_TRACK records are promoted on first Promoter sweep.
    relevance = float(archive_meta.get("relevance_score") or 0.0)
    extra = archive_meta.get("extra") or {}
    fast_tracked = (
        status == "fast_tracked"
        or (isinstance(extra, dict) and (extra.get("triage") or {}).get("fast_track"))
        or relevance >= 0.7
    )
    if fast_tracked:
        return True

    # Engagement path.
    for sig in signals:
        stype = sig.get("type") or ""
        if stype not in _SIGNAL_WEIGHT:
            continue
        if stype == "calendar_attendance":
            bucket = _calendar_bucket(sig)
            if bucket not in ("small", "medium"):
                continue  # large meeting attendance does not trigger promotion alone
        return True  # any qualifying signal suffices

    return False
