"""Reputation writer for the Janitor (Phase 6, PRD §5.2.4 + §5.5.1 step 6).

Reads today's structured log entries from .logs/scribe.pipeline.jsonl
(written by lib/logging.py) to tally promote/archive/discard outcomes per
channel and sender. Updates Institutional-Memory/.reputation.json with new
scores using a simple Wilson-like smoothed ratio.

Score formula:
    score = (promoted + 1) / (promoted + archived + discarded + 2)

This is a Laplace-smoothed proportion (add-1 for each outcome class):
- Cold start (no data): 1/2 = 0.5 — neutral.
- Purely promotional source over time → approaches 1.0.
- Purely noise → approaches 0.0.
- The +2 denominator dampens early volatility (1 prior for each tail).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import paths
from lib.logging import log_event

REPUTATION_PATH = paths.INSTITUTIONAL_MEMORY / ".reputation.json"
LOG_PATH = paths.LOGS / "scribe.pipeline.jsonl"


def _load_reputation() -> dict[str, Any]:
    if REPUTATION_PATH.exists():
        try:
            return json.loads(REPUTATION_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"channels": {}, "senders": {}}


def _save_reputation(data: dict[str, Any]) -> None:
    REPUTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPUTATION_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _score(counts: dict[str, int]) -> float:
    p = counts.get("promoted", 0)
    a = counts.get("archived", 0)
    d = counts.get("discarded", 0)
    return (p + 1) / (p + a + d + 2)


def _provenance_for_arc(arc_id: str) -> tuple[str, str]:
    """Return (shared_in, shared_by) from the archive record, or ("", "")."""
    if not arc_id:
        return "", ""
    arc_path = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
    if not arc_path.exists():
        return "", ""
    try:
        import frontmatter as fm
        post = fm.load(arc_path)
        prov = post.metadata.get("provenance") or {}
        if isinstance(prov, dict):
            return str(prov.get("shared_in") or ""), str(prov.get("shared_by") or "")
    except Exception:
        pass
    return "", ""


def _read_today_outcomes() -> tuple[dict[str, dict], dict[str, dict]]:
    """Parse pipeline log entries from today (UTC). Returns (channels, senders) tallies.

    Strategy: pair `archive_record.written` entries (which carry arc_id + decision)
    with provenance from the archive record files.  The older eval-harness entries
    only have `triage.decision` without arc_id — those are skipped (no identity to
    attribute to).
    """
    channels: dict[str, dict[str, int]] = defaultdict(lambda: {"promoted": 0, "archived": 0, "discarded": 0})
    senders: dict[str, dict[str, int]] = defaultdict(lambda: {"promoted": 0, "archived": 0, "discarded": 0})

    today = datetime.now(timezone.utc).date().isoformat()
    if not LOG_PATH.exists():
        return channels, senders

    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") != "archive_record.written":
                continue
            ts = entry.get("ts") or ""
            if not ts.startswith(today):
                continue

            arc_id: str = entry.get("arc_id") or ""
            decision: str = entry.get("decision") or ""

            if decision == "fast_track":
                bucket = "promoted"
            elif decision == "archive_only":
                bucket = "archived"
            elif decision in ("discard", "link_duplicate"):
                bucket = "discarded"
            else:
                continue

            channel, sender = _provenance_for_arc(arc_id)
            if channel:
                channels[channel][bucket] += 1
            if sender:
                senders[sender][bucket] += 1

    return channels, senders


def update(*, dry_run: bool = False) -> dict[str, int]:
    """Update .reputation.json from today's pipeline log. Returns summary counts."""
    channels_today, senders_today = _read_today_outcomes()
    counts = {"channels_updated": 0, "senders_updated": 0}

    if not channels_today and not senders_today:
        log_event("janitor.reputation", "no_new_outcomes")
        return counts

    data = _load_reputation()
    rep_channels: dict[str, Any] = data.setdefault("channels", {})
    rep_senders: dict[str, Any] = data.setdefault("senders", {})

    for channel, today_counts in channels_today.items():
        entry = rep_channels.setdefault(channel, {"promoted": 0, "archived": 0, "discarded": 0, "score": 0.5})
        for k in ("promoted", "archived", "discarded"):
            entry[k] = entry.get(k, 0) + today_counts.get(k, 0)
        entry["score"] = round(_score(entry), 4)
        counts["channels_updated"] += 1

    for sender, today_counts in senders_today.items():
        entry = rep_senders.setdefault(sender, {"promoted": 0, "archived": 0, "discarded": 0, "score": 0.5})
        for k in ("promoted", "archived", "discarded"):
            entry[k] = entry.get(k, 0) + today_counts.get(k, 0)
        entry["score"] = round(_score(entry), 4)
        counts["senders_updated"] += 1

    log_event("janitor.reputation", "update",
              channels=counts["channels_updated"], senders=counts["senders_updated"])

    if dry_run:
        print(f"  [dry-run] would update {counts['channels_updated']} channels, "
              f"{counts['senders_updated']} senders")
        return counts

    _save_reputation(data)
    return counts
