"""Lightweight per-component structured logging.

Writes JSONL to .logs/{component}.jsonl plus optional stderr mirror.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from . import paths


def log_event(component: str, event: str, **fields: Any) -> None:
    paths.ensure_dirs()
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "component": component,
        "event": event,
        **fields,
    }
    line = json.dumps(record, default=str)
    log_path = paths.LOGS / f"{component}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr)
