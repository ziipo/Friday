"""Google Drive poller per PRD §5.1.6.

Scope:
- A designated folder (configured in pollers.yaml) — anything dropped there is
  ingested unconditionally.
- Files I created/modified in the recent_lookback window.
- Files starred by me.
- Files shared with me directly (1:1, not via group).

Engagement signals via Drive Activity API: views, comments I posted, edits
I made. Note (PRD §9 open question): the Activity API does not expose view
*duration*; we record view counts instead, and leave duration as a TODO.

Run:
    PYTHONPATH=scripts uv run python -m scribe.pollers.drive [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from lib import paths, poller_config, poller_state
from lib.archive_record import make_artifact
from lib.engagement import append_signals
from lib.google_oauth import load_credentials
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, EngagementSignal, Provenance
from scribe.ingestors import web as web_ingestor

source_type = "gdrive"
poll_interval_seconds = 1800
STATE_NAME = "drive"

# Native Google Workspace types must be exported, not downloaded directly.
EXPORT_MAP = {
    "application/vnd.google-apps.document": ("application/pdf", "pdf",
                                             "text/plain", "txt"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", "pdf",
                                                "text/csv", "csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf",
                                                 "text/plain", "txt"),
}


def _make_drive_service():
    creds = load_credentials("drive")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _make_activity_service():
    creds = load_credentials("drive")
    return build("driveactivity", "v2", credentials=creds, cache_discovery=False)


def _seen_ids_from_state() -> set[str]:
    state = poller_state.load(STATE_NAME)
    return set(state.get("seen_file_ids") or [])


def _persist_seen(seen: set[str]) -> None:
    state = poller_state.load(STATE_NAME)
    # Cap memory footprint: keep most recent ~5000 ids.
    capped = list(seen)[-5000:]
    state["seen_file_ids"] = capped
    poller_state.save(STATE_NAME, state)


def _list_files(service, query: str, page_size: int = 100) -> list[dict]:
    """Run a Drive `files.list` query and return all matching files (paginated)."""
    out: list[dict] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "q": query,
            "pageSize": page_size,
            "fields": "nextPageToken, files(id, name, mimeType, webViewLink, "
                      "modifiedTime, createdTime, owners, sharedWithMeTime, starred, parents)",
            "supportsAllDrives": False,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.files().list(**kwargs).execute()
        out.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _candidate_for_url_forward(file: dict, *, reason: str) -> list[CandidateRecord]:
    url = file.get("webViewLink")
    if not url:
        return []
    owners = ", ".join((o.get("emailAddress") or "") for o in (file.get("owners") or []))
    provenance = Provenance(
        shared_by=owners or "drive",
        shared_in="gdrive",
        shared_at=_parse_iso(file.get("modifiedTime") or file.get("createdTime")),
        context=f"Drive file ({reason}): {file.get('name')}",
    )
    try:
        return web_ingestor.ingest_url(
            url, provenance=provenance, captured_via="poll",
            extra={
                "gdrive_file_id": file["id"],
                "gdrive_mime": file.get("mimeType"),
                "gdrive_reason": reason,
            },
        )
    except Exception as exc:
        log_event("scribe.drive", "url_forward.fail",
                  file_id=file["id"], url=url,
                  error=type(exc).__name__, message=str(exc))
        return []


def _download_file(service, file: dict) -> CandidateRecord | None:
    """Download (or export) a Drive file and stage as a candidate."""
    mime = file.get("mimeType") or "application/octet-stream"
    name = file.get("name") or file["id"]
    captured_at = datetime.now(timezone.utc)
    seed = f"gdrive::{file['id']}"
    arc = archive_id(seed, captured_at)

    rendered_dir = paths.ARCHIVE_RENDERED / arc
    clean_dir = paths.ARCHIVE_CLEAN / arc
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []

    try:
        if mime in EXPORT_MAP:
            rendered_mime, rendered_ext, clean_mime, clean_ext = EXPORT_MAP[mime]
            rendered_path = rendered_dir / f"export.{rendered_ext}"
            _export_to(service, file["id"], rendered_mime, rendered_path)
            artifacts.append(make_artifact(arc, "rendered", rendered_path.name))

            clean_path = clean_dir / f"export.{clean_ext}"
            _export_to(service, file["id"], clean_mime, clean_path)
            artifacts.append(make_artifact(arc, "clean", clean_path.name))
        else:
            target = rendered_dir / _safe_name(name)
            _download_to(service, file["id"], target)
            artifacts.append(make_artifact(arc, "rendered", target.name))
            # Best-effort: if it's text/markdown/plain, copy to Clean too.
            if mime.startswith(("text/", "application/json")):
                clean_target = clean_dir / target.name
                clean_target.write_bytes(target.read_bytes())
                artifacts.append(make_artifact(arc, "clean", clean_target.name))
    except HttpError as exc:
        log_event("scribe.drive", "download.fail",
                  file_id=file["id"], mime=mime,
                  error=type(exc).__name__, status=exc.resp.status)
        return None

    if not artifacts:
        return None

    owners = ", ".join((o.get("emailAddress") or "") for o in (file.get("owners") or []))
    provenance = Provenance(
        shared_by=owners or "drive",
        shared_in="gdrive",
        shared_at=_parse_iso(file.get("modifiedTime") or file.get("createdTime")),
        context=f"Drive download: {name}",
    )
    return CandidateRecord(
        source_type="gdrive",
        captured_via="poll",
        arc_id=arc,
        seed=seed,
        captured_at=captured_at,
        canonical_url=file.get("webViewLink"),
        title=name,
        provenance=provenance,
        artifacts=artifacts,
        extra={"gdrive_file_id": file["id"], "gdrive_mime": mime},
    )


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip() or "file"


def _export_to(service, file_id: str, mime: str, target: Path) -> None:
    request = service.files().export_media(fileId=file_id, mimeType=mime)
    _stream_to(request, target)


def _download_to(service, file_id: str, target: Path) -> None:
    request = service.files().get_media(fileId=file_id)
    _stream_to(request, target)


def _stream_to(request, target: Path) -> None:
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    target.write_bytes(buf.getvalue())


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _activity_signals(activity_service, *, since: datetime) -> list[EngagementSignal]:
    """Pull recent activity events on items I touched. Filters to actor==me."""
    signals: list[EngagementSignal] = []
    page_token: str | None = None
    body: dict[str, Any] = {
        "pageSize": 100,
        "filter": f"time >= \"{since.isoformat().replace('+00:00', 'Z')}\"",
    }
    while True:
        if page_token:
            body["pageToken"] = page_token
        try:
            resp = activity_service.activity().query(body=body).execute()
        except HttpError as exc:
            log_event("scribe.drive", "activity.fail",
                      error=type(exc).__name__, status=exc.resp.status)
            return signals
        for activity in resp.get("activities") or []:
            ts = _parse_iso(activity.get("timestamp")) or datetime.now(timezone.utc)
            for actor in activity.get("actors") or []:
                if not (actor.get("user") or {}).get("knownUser", {}).get("isCurrentUser"):
                    continue
                kind = _classify_activity(activity)
                if not kind:
                    continue
                target_id = _activity_target_id(activity)
                signals.append(EngagementSignal(
                    ts=ts, type=f"drive_{kind}", actor="user",
                    target_id=None,  # arc_id not known yet — Phase 5 Promoter resolves
                    target_url=None,
                    extra={"gdrive_file_id": target_id} if target_id else {},
                ))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return signals


def _classify_activity(activity: dict) -> str | None:
    detail = activity.get("primaryActionDetail") or {}
    if "view" in detail:
        return "view"
    if "comment" in detail:
        return "comment"
    if "edit" in detail:
        return "edit"
    return None


def _activity_target_id(activity: dict) -> str | None:
    for tgt in activity.get("targets") or []:
        item = tgt.get("driveItem") or {}
        name = item.get("name") or ""  # like "items/abc123"
        if name.startswith("items/"):
            return name[len("items/"):]
    return None


def poll() -> list[CandidateRecord]:
    cfg = poller_config.for_poller("drive")
    drive = _make_drive_service()
    seen = _seen_ids_from_state()
    download_mimes = set(cfg.get("download_mimetypes") or [])

    candidates: list[CandidateRecord] = []
    files: list[dict] = []

    folder_id = cfg.get("designated_folder_id")
    if folder_id:
        files += _list_files(drive, f"'{folder_id}' in parents and trashed=false")
    lookback = int(cfg.get("recent_lookback_days", 1))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat().replace("+00:00", "Z")
    files += _list_files(drive, f"modifiedTime > '{cutoff}' and ('me' in owners or sharedWithMe) and trashed=false")
    files += _list_files(drive, "starred = true and trashed=false")

    # Dedup by file id within a single run.
    by_id: dict[str, dict] = {}
    for f in files:
        by_id.setdefault(f["id"], f)

    for fid, file in by_id.items():
        if fid in seen:
            continue
        mime = file.get("mimeType") or ""
        if mime in download_mimes or mime in EXPORT_MAP:
            cand = _download_file(drive, file)
            if cand:
                candidates.append(cand)
        else:
            candidates.extend(_candidate_for_url_forward(file, reason="drive_recent_or_starred"))
        seen.add(fid)

    log_event("scribe.drive", "files.scanned", scanned=len(by_id), candidates=len(candidates))
    _persist_seen(seen)

    # Engagement signals (Activity API).
    try:
        activity = _make_activity_service()
        since = datetime.now(timezone.utc) - timedelta(hours=int(cfg.get("activity_lookback_hours", 24)))
        signals = _activity_signals(activity, since=since)
        if signals:
            append_signals(signals)
            log_event("scribe.drive", "signals.appended", count=len(signals))
    except Exception as exc:
        log_event("scribe.drive", "activity.skip",
                  error=type(exc).__name__, message=str(exc))

    return candidates


def collect_engagement_signals() -> list[EngagementSignal]:
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday Google Drive poller.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log_event("scribe.drive", "startup")
    candidates = poll()
    log_event("scribe.drive", "poll.done", candidates=len(candidates))

    if args.dry_run:
        for c in candidates:
            print(f"  candidate: arc_id={c.arc_id} title={c.title} url={c.canonical_url}")
        return 0
    if candidates:
        from scribe.pipeline import process_candidates
        results = process_candidates(candidates)
        log_event("scribe.drive", "pipeline.done",
                  decisions=[r["decision"] for r in results])
    return 0


if __name__ == "__main__":
    sys.exit(main())
