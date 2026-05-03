"""Archive and memory record IDs per PRD §4.5.

Format: {prefix}_{ISO-timestamp}_{short-hash}
- prefix: `arc` for archive records, `src` for memory records
- timestamp: UTC ISO-8601 with second resolution, colons replaced with hyphens for filesystem safety
- short-hash: first 4 hex chars of SHA-256(canonical_url || content)

When an archive record is promoted, the memory record reuses the same hash component
for traceability.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def _short_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:4]


def _fs_timestamp(dt: datetime) -> str:
    """ISO-8601 UTC with `:` → `-` so the timestamp is safe in filesystem paths."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def archive_id(seed: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"arc_{_fs_timestamp(when)}_{_short_hash(seed)}"


def memory_id_from_archive(arc_id: str) -> str:
    """Promote an archive record to a memory record, preserving the timestamp + hash."""
    if not arc_id.startswith("arc_"):
        raise ValueError(f"expected arc_ prefix, got {arc_id!r}")
    return "src_" + arc_id[len("arc_"):]
