"""Per-poller state persistence.

Each poller (calendar, drive, slack) needs to remember cursors between runs:
- Slack: per-channel `last_ts`
- Drive: page tokens, last activity timestamp
- Calendar: sync tokens

State is one JSON file per poller under `.logs/state/{poller}.json`. Writes are
atomic via temp + rename so a crashed poller can't leave a half-written cursor.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import paths

_STATE_DIR = paths.LOGS / "state"


def _state_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name:
        raise ValueError(f"invalid poller state name: {name!r}")
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{name}.json"


def load(name: str) -> dict[str, Any]:
    path = _state_path(name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        # Corrupted state — start fresh rather than crash the poller. Next save
        # will overwrite. Worst case is one duplicate batch of candidates,
        # which the dedup-by-arc_id at write time handles.
        return {}


def save(name: str, state: dict[str, Any]) -> Path:
    """Atomically write `state` to `.logs/state/{name}.json`."""
    target = _state_path(name)
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return target
