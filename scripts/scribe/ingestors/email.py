"""Email ingestor: stage .eml + each attachment as a sub-record."""
from __future__ import annotations

import email
from email import policy
from datetime import datetime, timezone
from pathlib import Path

from lib import paths
from lib.archive_record import make_artifact
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, Provenance

source_type = "email"
poll_interval_seconds = 0


def _parse(eml_path: Path):
    with eml_path.open("rb") as f:
        return email.message_from_binary_file(f, policy=policy.default)


def _best_body(msg) -> str:
    if msg.is_multipart():
        plain = msg.get_body(preferencelist=("plain",))
        if plain is not None:
            return plain.get_content()
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            return html.get_content()
        return ""
    return msg.get_content() if msg.get_content_type().startswith("text/") else ""


def ingest_path(path: Path) -> list[CandidateRecord]:
    captured_at = datetime.now(timezone.utc)
    msg = _parse(path)
    subject = (msg.get("Subject") or path.stem).strip()
    sender = str(msg.get("From") or "").strip()
    msgid = str(msg.get("Message-ID") or "").strip()
    seed = f"email::{msgid or path.name}"
    arc_id = archive_id(seed, captured_at)

    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    (rendered_dir / path.name).write_bytes(path.read_bytes())
    body = _best_body(msg) or ""
    (clean_dir / "body.txt").write_text(body, encoding="utf-8")

    artifacts = [
        make_artifact(arc_id, "raw", path.name),
        make_artifact(arc_id, "clean", "body.txt"),
    ]

    sub_candidates: list[CandidateRecord] = []
    for part in msg.iter_attachments():
        filename = part.get_filename() or "attachment.bin"
        safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in filename).strip()
        att_path = clean_dir / safe
        payload = part.get_payload(decode=True) or b""
        att_path.write_bytes(payload)
        artifacts.append(make_artifact(arc_id, "clean", safe))
        # Sub-record uses a *different* arc_id keyed off the attachment so it
        # stands as its own archive_record after triage. Artifact is shared
        # via a relative reference; if the parent is discarded we still keep
        # the attachment under its own arc_id by copying.
        att_seed = f"{seed}::attachment::{safe}"
        att_arc_id = archive_id(att_seed, captured_at)
        att_clean_dir = paths.ARCHIVE_CLEAN / att_arc_id
        att_clean_dir.mkdir(parents=True, exist_ok=True)
        (att_clean_dir / safe).write_bytes(payload)
        sub_candidates.append(CandidateRecord(
            source_type="email_attachment",
            captured_via="watcher",
            arc_id=att_arc_id,
            seed=att_seed,
            captured_at=captured_at,
            title=filename,
            provenance=Provenance(
                shared_by=sender,
                context=f"attachment of email '{subject}'",
                shared_at=captured_at,
            ),
            artifacts=[make_artifact(att_arc_id, "clean", safe)],
            extra={"parent_arc": arc_id, "content_type": part.get_content_type()},
        ))

    parent = CandidateRecord(
        source_type="email",
        captured_via="watcher",
        arc_id=arc_id,
        seed=seed,
        captured_at=captured_at,
        canonical_url=None,
        title=subject,
        provenance=Provenance(
            shared_by=sender,
            shared_in=str(msg.get("To") or ""),
            context=f"email captured from {path.name}",
            shared_at=captured_at,
        ),
        artifacts=artifacts,
        extra={
            "message_id": msgid,
            "date": str(msg.get("Date") or ""),
            "attachments": [c.title for c in sub_candidates],
        },
    )
    log_event("scribe.email", "staged",
              arc_id=arc_id, source=str(path), attachments=len(sub_candidates))
    return [parent, *sub_candidates]
