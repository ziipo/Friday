"""Filesystem layout — all paths anchor to FRIDAY_ROOT."""
from __future__ import annotations

import os
from pathlib import Path

FRIDAY_ROOT = Path(os.environ.get("FRIDAY_ROOT", Path(__file__).resolve().parents[2]))

INBOX = FRIDAY_ROOT / "Inbox"
INBOX_PROCESSED = INBOX / "processed"
INBOX_FAILED = INBOX / "failed"

ARCHIVE_RECORDS = FRIDAY_ROOT / "Archive" / "records"
ARCHIVE_RENDERED = FRIDAY_ROOT / "Archive" / "Rendered"
ARCHIVE_CLEAN = FRIDAY_ROOT / "Archive" / "Clean"

ARCHIVEBOX_DATA = FRIDAY_ROOT / "archivebox-data"
ARCHIVEBOX_ARCHIVE = ARCHIVEBOX_DATA / "archive"

INSTITUTIONAL_MEMORY = FRIDAY_ROOT / "Institutional-Memory"
ENGAGEMENT_LOG = FRIDAY_ROOT / "EngagementLog"
REVIEW_QUEUE_PENDING = FRIDAY_ROOT / "ReviewQueue" / "pending"

LOGS = FRIDAY_ROOT / ".logs"


def ensure_dirs() -> None:
    """Create runtime directories if missing. Safe to call repeatedly."""
    for p in (
        INBOX, INBOX_PROCESSED, INBOX_FAILED,
        ARCHIVE_RECORDS, ARCHIVE_RENDERED, ARCHIVE_CLEAN,
        ENGAGEMENT_LOG, REVIEW_QUEUE_PENDING, LOGS,
    ):
        p.mkdir(parents=True, exist_ok=True)
