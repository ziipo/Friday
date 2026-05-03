"""Promoter — engagement watcher per PRD §5.3.

Runs every ~5 minutes as a launchd agent. Each cycle:

1. Load all EngagementLog signals since the last watermark.
2. Group signals by resolved arc_id (via matcher.py).
3. For each arc_id with signals:
   a. Recompute engagement_score and update the archive record.
   b. If should_promote() → call synthesize_archive() with the right engagement tag.
4. Also sweep archive records flagged FAST_TRACK that have no signals yet.
5. Save watermark so next cycle is incremental.

Run:
    PYTHONPATH=scripts uv run python -m promoter.promoter [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from lib import paths, poller_state
from lib.engagement import iter_signals
from lib.logging import log_event

from .matcher import invalidate_index, resolve_signal
from .trigger import engagement_tag, should_promote

STATE_NAME = "promoter"


# ---------------------------------------------------------------------------
# Archive record helpers
# ---------------------------------------------------------------------------

def _load_archive(arc_id: str) -> tuple[Path, dict[str, Any]] | tuple[None, None]:
    p = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
    if not p.exists():
        return None, None
    try:
        post = frontmatter.load(p)
        return p, dict(post.metadata)
    except Exception:
        return None, None


def _save_archive_fields(arc_path: Path, updates: dict[str, Any]) -> None:
    """Patch specific frontmatter fields without touching the rest of the file."""
    post = frontmatter.load(arc_path)
    for k, v in updates.items():
        post[k] = v
    # Reconstruct: yaml frontmatter + original body.
    yaml_text = yaml.safe_dump(dict(post.metadata), sort_keys=False, allow_unicode=True)
    body = post.content or (
        "<!--\n"
        "Archive-tier record per PRD §4.1. Frontmatter-only by design.\n"
        "-->\n"
    )
    arc_path.write_text(f"---\n{yaml_text}---\n\n{body}", encoding="utf-8")


def _compute_engagement_score(signals: list[dict[str, Any]]) -> float:
    """Simple additive score: studied=1.0, reviewed=0.4, others=0.1. Capped at 1.0."""
    weights = {"studied": 1.0, "reviewed": 0.4}
    from .trigger import _SIGNAL_WEIGHT, _calendar_bucket
    total = 0.0
    for sig in signals:
        stype = sig.get("type") or ""
        weight_class = _SIGNAL_WEIGHT.get(stype)
        if weight_class is None:
            continue
        if stype == "calendar_attendance":
            bucket = _calendar_bucket(sig)
            if bucket not in ("small", "medium"):
                weight_class = "reviewed"
        total += weights.get(weight_class, 0.1)
    return min(1.0, total)


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------

def _load_watermark() -> datetime | None:
    state = poller_state.load(STATE_NAME)
    raw = state.get("watermark")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _save_watermark(ts: datetime) -> None:
    state = poller_state.load(STATE_NAME)
    state["watermark"] = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    poller_state.save(STATE_NAME, state)


# ---------------------------------------------------------------------------
# FAST_TRACK sweep
# ---------------------------------------------------------------------------

def _fast_track_arc_ids() -> list[str]:
    """Return arc_ids that are fast_tracked but not yet promoted."""
    out: list[str] = []
    if not paths.ARCHIVE_RECORDS.exists():
        return out
    for p in paths.ARCHIVE_RECORDS.glob("arc_*.md"):
        try:
            post = frontmatter.load(p)
        except Exception:
            continue
        meta = post.metadata
        status = meta.get("status") or ""
        if status == "promoted":
            continue
        relevance = float(meta.get("relevance_score") or 0.0)
        extra = meta.get("extra") or {}
        fast_tracked = (
            status == "fast_tracked"
            or (isinstance(extra, dict) and (extra.get("triage") or {}).get("fast_track"))
            or relevance >= 0.7
        )
        if fast_tracked:
            out.append(meta.get("id") or p.stem)
    return out


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(*, dry_run: bool = False) -> dict[str, int]:
    """Execute one Promoter cycle. Returns summary counts."""
    watermark = _load_watermark()
    now = datetime.now(timezone.utc)

    # 1. Collect signals since watermark, grouped by arc_id.
    signals_by_arc: dict[str, list[dict]] = defaultdict(list)
    latest_ts = watermark

    for sig in iter_signals(since=watermark):
        arc_id = resolve_signal(sig)
        if arc_id:
            signals_by_arc[arc_id].append(sig)
        try:
            sig_ts = datetime.fromisoformat(str(sig.get("ts", "")).replace("Z", "+00:00"))
            if latest_ts is None or sig_ts > latest_ts:
                latest_ts = sig_ts
        except ValueError:
            pass

    # 2. Add FAST_TRACK records with no signals (relevance-path promotion).
    for arc_id in _fast_track_arc_ids():
        if arc_id not in signals_by_arc:
            signals_by_arc[arc_id]  # touch to ensure it's in the dict

    counts = {"checked": 0, "updated": 0, "promoted": 0, "errors": 0}

    for arc_id, signals in signals_by_arc.items():
        counts["checked"] += 1
        arc_path, meta = _load_archive(arc_id)
        if arc_path is None or meta is None:
            log_event("promoter", "archive.missing", arc_id=arc_id)
            continue

        # 3a. Update engagement_score.
        new_score = _compute_engagement_score(signals)
        old_score = float(meta.get("engagement_score") or 0.0)
        if new_score > old_score:
            if not dry_run:
                _save_archive_fields(arc_path, {"engagement_score": round(new_score, 4)})
                invalidate_index()
            counts["updated"] += 1
            log_event("promoter", "engagement.updated",
                      arc_id=arc_id, old=old_score, new=new_score)

        # 3b. Check promotion.
        if not should_promote(meta, signals):
            continue

        tag = engagement_tag(signals)
        reason = "engagement" if signals else "relevance"
        log_event("promoter", "promoting", arc_id=arc_id, reason=reason, tag=tag)

        if dry_run:
            print(f"  [dry-run] would promote {arc_id} reason={reason} tag={tag}")
            counts["promoted"] += 1
            continue

        try:
            from synthesizer.synthesize import synthesize_archive
            result = synthesize_archive(arc_id, promotion_reason=reason, engagement=tag)
            log_event("promoter", "promoted",
                      arc_id=arc_id, src_id=result.src_id,
                      entities_created=len(result.entities_created),
                      concepts_created=len(result.concepts_created))
            invalidate_index()
            counts["promoted"] += 1
        except Exception as exc:
            log_event("promoter", "promote.error",
                      arc_id=arc_id, error=type(exc).__name__, message=str(exc))
            counts["errors"] += 1

    # 4. Advance watermark to now (not latest_ts) so a failed signal doesn't
    #    cause infinite retries — each cycle is a fresh forward scan.
    if not dry_run:
        _save_watermark(now)

    log_event("promoter", "cycle.done", **counts)
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Friday Promoter — engagement-driven promotion.")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle and exit (default behavior for launchd).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and log what would be promoted without writing anything.")
    args = parser.parse_args()

    log_event("promoter", "startup", dry_run=args.dry_run)
    counts = run_cycle(dry_run=args.dry_run)
    print(
        f"Promoter cycle: checked={counts['checked']} "
        f"updated={counts['updated']} promoted={counts['promoted']} "
        f"errors={counts['errors']}"
    )
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
