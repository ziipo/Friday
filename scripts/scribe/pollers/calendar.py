"""Google Calendar poller per PRD §5.1.5.

Scope: events in primary calendar from now-7d to now+7d.

For each event with attached docs (event.attachments[] — Drive fileIds), emit a
CandidateRecord forwarded via the web ingestor (the canonical_url path of the
attachment is enough; ArchiveBox handles the fetch). Engagement signals fire
for past events: did_attend (response==accepted AND end<now AND not on PTO),
was_organizer, meeting_size bucket.

State persistence: per-calendar `syncToken` in poller_state for incremental
list. On first run (or 410 Gone), fall back to a full window list.

Run:
    PYTHONPATH=scripts uv run python -m scribe.pollers.calendar [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lib import poller_config, poller_state
from lib.engagement import append_signals
from lib.google_oauth import load_credentials
from lib.logging import log_event
from lib.protocol import CandidateRecord, EngagementSignal, Provenance
from scribe.ingestors import web as web_ingestor

source_type = "gcal"
poll_interval_seconds = 3600
STATE_NAME = "calendar"


def _make_service():
    """Built lazily so unit tests can monkey-patch this module-level factory."""
    creds = load_credentials("calendar")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_on_pto(when: datetime, ranges: list[str]) -> bool:
    """`ranges` is a list of "YYYY-MM-DD/YYYY-MM-DD" pairs (inclusive)."""
    day = when.astimezone(timezone.utc).date()
    for r in ranges:
        try:
            start_s, end_s = r.split("/", 1)
            start = datetime.fromisoformat(start_s).date()
            end = datetime.fromisoformat(end_s).date()
        except ValueError:
            continue
        if start <= day <= end:
            return True
    return False


def _meeting_size(event: dict) -> int:
    return len(event.get("attendees") or []) or 1


def _bucket(size: int, cfg: dict) -> str:
    if size <= int(cfg.get("small_meeting_max", 4)):
        return "small"
    if size <= int(cfg.get("medium_meeting_max", 8)):
        return "medium"
    return "large"


def _self_response(event: dict) -> str | None:
    for att in event.get("attendees") or []:
        if att.get("self"):
            return att.get("responseStatus")
    return None


def _list_events(
    service, calendar_id: str, *,
    time_min: datetime, time_max: datetime, sync_token: str | None,
) -> tuple[list[dict], str | None, bool]:
    """Return (events, next_sync_token, sync_token_expired).

    Prefer syncToken for incremental fetch; fall back to time-window on 410
    (the boolean tells the caller to retry without sync_token)."""
    events: list[dict] = []
    page_token: str | None = None
    next_sync: str | None = None
    while True:
        kwargs: dict[str, Any] = {"calendarId": calendar_id, "singleEvents": True, "showDeleted": False}
        if sync_token and not page_token:
            kwargs["syncToken"] = sync_token
        else:
            kwargs.update({
                "timeMin": time_min.isoformat().replace("+00:00", "Z"),
                "timeMax": time_max.isoformat().replace("+00:00", "Z"),
                "orderBy": "startTime",
            })
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            resp = service.events().list(**kwargs).execute()
        except HttpError as exc:
            if exc.resp.status == 410 and sync_token:
                log_event("scribe.calendar", "synctoken.expired", calendar=calendar_id)
                return [], None, True
            raise
        events.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            next_sync = resp.get("nextSyncToken")
            break
    return events, next_sync, False


def _attachments_to_candidates(event: dict, calendar_id: str) -> list[CandidateRecord]:
    candidates: list[CandidateRecord] = []
    for att in event.get("attachments") or []:
        url = att.get("fileUrl")
        if not url:
            continue
        organizer = (event.get("organizer") or {}).get("email", "")
        provenance = Provenance(
            shared_by=organizer,
            shared_in=f"gcal:{calendar_id}",
            shared_at=_parse_iso((event.get("start") or {}).get("dateTime", "")),
            context=f"calendar event: {event.get('summary') or '(untitled)'}",
        )
        try:
            staged = web_ingestor.ingest_url(
                url,
                provenance=provenance,
                captured_via="poll",
                extra={
                    "gcal_event_id": event.get("id"),
                    "gcal_calendar_id": calendar_id,
                    "gcal_attachment_title": att.get("title"),
                    "gcal_attachment_mime": att.get("mimeType"),
                },
            )
            candidates.extend(staged)
        except Exception as exc:
            log_event("scribe.calendar", "attachment.fail",
                      url=url, event=event.get("id"),
                      error=type(exc).__name__, message=str(exc))
    return candidates


def _engagement_for_event(event: dict, *, cfg: dict, now: datetime) -> list[EngagementSignal]:
    signals: list[EngagementSignal] = []
    end_dt = _parse_iso((event.get("end") or {}).get("dateTime", ""))
    if not end_dt or end_dt > now:
        return signals  # only past events emit attendance signals

    response = _self_response(event)
    on_pto = _is_on_pto(end_dt, list(cfg.get("pto_ranges") or []))
    organizer = (event.get("organizer") or {}).get("self", False)
    size = _meeting_size(event)
    bucket = _bucket(size, cfg)

    base_extra = {
        "gcal_event_id": event.get("id"),
        "summary": event.get("summary"),
        "meeting_size": size,
        "size_bucket": bucket,
    }
    if response == "accepted" and not on_pto:
        signals.append(EngagementSignal(
            ts=end_dt, type="calendar_attendance", actor="user",
            extra={**base_extra, "response": response},
        ))
    if organizer:
        signals.append(EngagementSignal(
            ts=end_dt, type="calendar_organized", actor="user",
            extra=base_extra,
        ))
    return signals


def poll() -> list[CandidateRecord]:
    cfg = poller_config.for_poller("calendar")
    state = poller_state.load(STATE_NAME)
    sync_tokens: dict[str, str] = state.get("sync_tokens") or {}

    now = datetime.now(timezone.utc)
    time_min = now - timedelta(days=int(cfg.get("lookback_days", 7)))
    time_max = now + timedelta(days=int(cfg.get("lookahead_days", 7)))

    service = _make_service()
    new_state_tokens: dict[str, str] = dict(sync_tokens)
    candidates: list[CandidateRecord] = []
    all_signals: list[EngagementSignal] = []

    for calendar_id in cfg.get("calendar_ids") or ["primary"]:
        sync = sync_tokens.get(calendar_id)
        events, next_sync, expired = _list_events(
            service, calendar_id,
            time_min=time_min, time_max=time_max, sync_token=sync,
        )
        if expired:
            new_state_tokens.pop(calendar_id, None)
            events, next_sync, _ = _list_events(
                service, calendar_id,
                time_min=time_min, time_max=time_max, sync_token=None,
            )
        if next_sync:
            new_state_tokens[calendar_id] = next_sync

        log_event("scribe.calendar", "events.fetched",
                  calendar=calendar_id, count=len(events))

        for ev in events:
            if ev.get("status") == "cancelled":
                continue
            candidates.extend(_attachments_to_candidates(ev, calendar_id))
            all_signals.extend(_engagement_for_event(ev, cfg=cfg, now=now))

    if all_signals:
        append_signals(all_signals)
        log_event("scribe.calendar", "signals.appended", count=len(all_signals))

    # Persist updated sync tokens (best-effort: drop the ones we couldn't resolve).
    poller_state.save(STATE_NAME, {**state, "sync_tokens": new_state_tokens})
    return candidates


def collect_engagement_signals() -> list[EngagementSignal]:
    """Engagement signals are emitted inline by `poll()`; the SourcePlugin
    contract requires the method, so we expose an empty-on-purpose hook."""
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday Google Calendar poller.")
    parser.add_argument("--once", action="store_true",
                        help="Run a single poll cycle and exit (default).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip pipeline.process_candidates and just log shapes.")
    args = parser.parse_args()

    log_event("scribe.calendar", "startup")
    candidates = poll()
    log_event("scribe.calendar", "poll.done", candidates=len(candidates))

    if args.dry_run:
        for c in candidates:
            print(f"  candidate: arc_id={c.arc_id} url={c.canonical_url} title={c.title}")
        return 0

    if candidates:
        from scribe.pipeline import process_candidates
        results = process_candidates(candidates)
        log_event("scribe.calendar", "pipeline.done",
                  decisions=[r["decision"] for r in results])
    return 0


if __name__ == "__main__":
    sys.exit(main())
