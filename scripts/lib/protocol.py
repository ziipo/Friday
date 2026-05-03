"""Plugin contracts per PRD §5.1.2.

A SourcePlugin can operate in two modes: watcher (file-driven) or poller (schedule-driven).
Phase 1 only exercises the watcher path. The poller methods are declared so we don't
need a breaking change in Phase 4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Provenance:
    """PRD §4.1 — who/where/when this artifact came from."""
    shared_by: str = ""
    shared_in: str = ""
    shared_at: datetime | None = None
    context: str = ""


@dataclass
class Artifact:
    """A stored representation of a captured source."""
    path: Path             # repo-relative path (string-rendered at write time)
    type: str              # "rendered" | "clean" | "raw"


@dataclass
class CandidateRecord:
    """A pre-archive record, returned by ingestors.

    Ingestors stage artifacts on disk under Archive/{Rendered,Clean}/{arc_id}/
    and return CandidateRecords carrying the arc_id seed and captured_at so the
    pipeline can reproduce the exact same arc_id when writing the record.
    """
    source_type: str                     # "web" | "gdrive" | "gcal" | "slack" | "email" | "markdown" | "pdf"
    captured_via: str                    # "watcher" | "poll" | "manual"
    arc_id: str = ""                     # set by ingestor; pipeline writes record under this id
    seed: str = ""                       # seed used to derive arc_id (URL or content-hash)
    captured_at: datetime | None = None  # set by ingestor; pipeline reuses for record write
    canonical_url: str | None = None
    title: str | None = None
    one_line_summary: str | None = None
    provenance: Provenance = field(default_factory=Provenance)
    artifacts: list[Artifact] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # source-type-specific metadata


@dataclass
class EngagementSignal:
    """Phase 4 placeholder — pollers will emit these into EngagementLog/{date}.jsonl."""
    ts: datetime
    type: str
    actor: str
    target_url: str | None = None
    target_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SourcePlugin(Protocol):
    """All source plugins implement this contract per PRD §5.1.2."""

    source_type: str
    poll_interval_seconds: int  # ignored in watcher mode

    def ingest_path(self, path: Path) -> list[CandidateRecord]:
        """Watcher mode: process a file dropped in Inbox/, return CandidateRecords."""
        ...

    def poll(self) -> list[CandidateRecord]:
        """Poller mode (Phase 4)."""
        ...

    def collect_engagement_signals(self) -> list[EngagementSignal]:
        """Poller mode (Phase 4)."""
        ...
