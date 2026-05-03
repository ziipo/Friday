"""ReviewQueue reconciliation writer (PRD §5.4.3, v1 review-required behavior).

When a synthesis flags that a new source contradicts or supersedes an existing
note, drop a proposal markdown into ReviewQueue/pending/{ts}_{slug}.md so the
user can review on their next morning sweep. The Janitor (Phase 6) eventually
applies approved proposals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from lib import paths

from .models import ReconciliationFlag


def _slug(s: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:60] or "proposal"


def write_reconciliation_proposal(
    flag: ReconciliationFlag,
    *,
    src_id: str,
    archive_id: str,
    source_title: str,
) -> Path:
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = f"{ts}_{flag.kind}_{_slug(flag.target)}.md"
    target = paths.REVIEW_QUEUE_PENDING / filename

    metadata = {
        "type": "review_proposal",
        "kind": flag.kind,
        "target_note": flag.target,
        "new_source": src_id,
        "archive_record": archive_id,
        "created": now.isoformat().replace("+00:00", "Z"),
        "status": "pending",
    }
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    body = (
        f"# {flag.kind.title()}: {flag.target}\n\n"
        f"**New source:** [[{src_id}]] — {source_title}\n\n"
        f"**Existing note:** [[{flag.target}]]\n\n"
        f"## Rationale\n\n{flag.summary}\n\n"
        f"## Proposed action\n\n"
        f"- [ ] Approve {flag.kind}: move to `ReviewQueue/approved/`.\n"
        f"- [ ] Reject: delete this file.\n"
    )
    target.write_text(f"---\n{yaml_text}---\n\n{body}", encoding="utf-8")
    return target
