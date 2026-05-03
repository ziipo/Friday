"""EngagementLog writer per PRD §4.4.

Pollers append one JSON line per signal to `EngagementLog/{YYYY-MM-DD}.jsonl`,
where the date rolls over at UTC midnight. The Promoter (Phase 5) consumes
these and decides whether to fast-track a record into the Memory tier.

Writes are line-atomic: we open in append mode and protect with `fcntl.flock`
so two pollers running concurrently can't tear each other's lines.
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from . import paths
from .protocol import EngagementSignal


def _log_path_for(when: datetime) -> Path:
    paths.ENGAGEMENT_LOG.mkdir(parents=True, exist_ok=True)
    day = when.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return paths.ENGAGEMENT_LOG / f"{day}.jsonl"


def _serialize(signal: EngagementSignal) -> dict:
    d = asdict(signal)
    if isinstance(signal.ts, datetime):
        d["ts"] = signal.ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return d


@contextmanager
def _locked_append(path: Path):
    """Open `path` in append mode with an exclusive flock for the write."""
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def append_signal(signal: EngagementSignal) -> Path:
    """Append one signal to today's log file. Returns the file path written to."""
    when = signal.ts if isinstance(signal.ts, datetime) else datetime.now(timezone.utc)
    target = _log_path_for(when)
    line = json.dumps(_serialize(signal), ensure_ascii=False)
    with _locked_append(target) as f:
        f.write(line + "\n")
    return target


def append_signals(signals: Iterable[EngagementSignal]) -> int:
    """Append many signals to today's log. Each is timestamped independently
    but they all batch into a single locked write where possible."""
    by_day: dict[Path, list[str]] = {}
    for s in signals:
        when = s.ts if isinstance(s.ts, datetime) else datetime.now(timezone.utc)
        target = _log_path_for(when)
        by_day.setdefault(target, []).append(json.dumps(_serialize(s), ensure_ascii=False))
    written = 0
    for target, lines in by_day.items():
        with _locked_append(target) as f:
            for line in lines:
                f.write(line + "\n")
                written += 1
    return written


def iter_signals(*, since: datetime | None = None) -> Iterator[dict]:
    """Yield raw signal dicts in chronological order (by file, then by line).

    `since` is a UTC threshold: signals strictly older are skipped. Files that
    don't parse cleanly are reported via a malformed-line skip rather than
    raising, since the Promoter must keep making forward progress.
    """
    if not paths.ENGAGEMENT_LOG.exists():
        return
    threshold = since.astimezone(timezone.utc) if since else None
    for path in sorted(paths.ENGAGEMENT_LOG.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if threshold and "ts" in obj:
                    try:
                        ts = datetime.fromisoformat(obj["ts"].replace("Z", "+00:00"))
                    except ValueError:
                        ts = None
                    if ts and ts < threshold:
                        continue
                yield obj
