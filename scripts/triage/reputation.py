"""Reputation reader (read-only at Phase 2; writer comes in Phase 6 Janitor).

Schema per PRD §5.2.4:
{
  "channels": {"#name": {"promoted": int, "archived": int, "discarded": int, "score": 0..1}},
  "senders":  {"email":  {"promoted": int, "archived": int, "discarded": int, "score": 0..1}}
}

Cold start: missing entries score 0.5 — neutral, neither boost nor penalty.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from lib import paths

REPUTATION_PATH = paths.INSTITUTIONAL_MEMORY / ".reputation.json"
COLD_START_SCORE = 0.5


@dataclass(frozen=True)
class ReputationLookup:
    channels: dict[str, float]
    senders: dict[str, float]

    def channel_score(self, channel: str | None) -> float:
        if not channel:
            return COLD_START_SCORE
        return self.channels.get(channel, COLD_START_SCORE)

    def sender_score(self, sender: str | None) -> float:
        if not sender:
            return COLD_START_SCORE
        return self.senders.get(sender, COLD_START_SCORE)


def load() -> ReputationLookup:
    if not REPUTATION_PATH.exists():
        return ReputationLookup(channels={}, senders={})
    raw = json.loads(REPUTATION_PATH.read_text(encoding="utf-8"))
    return ReputationLookup(
        channels={k: float(v.get("score", COLD_START_SCORE)) for k, v in (raw.get("channels") or {}).items()},
        senders={k: float(v.get("score", COLD_START_SCORE)) for k, v in (raw.get("senders") or {}).items()},
    )
