"""Slack poller per PRD §5.1.4.

Scope:
- All DMs (high signal — shared directly with me).
- Allowlisted channels (configured in pollers.yaml).
- Threads where I'm @mentioned (via search.messages on user token).

For each new message since `last_ts` per channel:
- Each URL → web ingestor (ArchiveBox).
- Each `files[]` entry: if external (mode == 'external' or url_private absent)
  → web ingestor; else download bytes to Archive/Rendered/{arc_id}/.

Engagement signals:
- My reactions (reactions.get on items in scanned channels).
- My replies (messages I authored in scanned threads).
- @mentions of me (resolved into engagement when I haven't replied yet).

Run:
    PYTHONPATH=scripts uv run python -m scribe.pollers.slack [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from lib import paths, poller_config, poller_state, secrets
from lib.archive_record import make_artifact
from lib.engagement import append_signals
from lib.ids import archive_id
from lib.logging import log_event
from lib.protocol import CandidateRecord, EngagementSignal, Provenance
from scribe.ingestors import web as web_ingestor

source_type = "slack"
poll_interval_seconds = 900
STATE_NAME = "slack"

URL_RE = re.compile(r"https?://[^\s<>|]+")


def _bot_client() -> WebClient:
    return WebClient(token=secrets.require("slack.bot_token"))


def _user_client() -> WebClient:
    return WebClient(token=secrets.require("slack.user_token"))


def _self_user_id(client: WebClient) -> str:
    cached = poller_state.load(STATE_NAME).get("self_user_id")
    if cached:
        return cached
    info = client.auth_test()
    uid = info["user_id"]
    state = poller_state.load(STATE_NAME)
    state["self_user_id"] = uid
    poller_state.save(STATE_NAME, state)
    return uid


def _list_dm_channels(client: WebClient) -> list[dict]:
    out: list[dict] = []
    cursor: str | None = None
    while True:
        resp = client.conversations_list(types="im,mpim", cursor=cursor, limit=200)
        out.extend(resp.get("channels") or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def _channel_messages(
    client: WebClient, channel: str, *, oldest: str | None,
) -> list[dict]:
    out: list[dict] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"channel": channel, "limit": 200}
        if oldest:
            kwargs["oldest"] = oldest
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.conversations_history(**kwargs)
        except SlackApiError as exc:
            if exc.response.get("error") == "ratelimited":
                wait = int(exc.response.headers.get("Retry-After", "5"))
                log_event("scribe.slack", "ratelimited", channel=channel, wait_s=wait)
                time.sleep(wait)
                continue
            log_event("scribe.slack", "history.fail", channel=channel,
                      error=exc.response.get("error"))
            return out
        out.extend(resp.get("messages") or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return out


def _stage_slack_file(file: dict, *, sender: str, channel: str, thread_ts: str | None) -> CandidateRecord | None:
    """Download a Slack-hosted file and stage it under Archive/Rendered/."""
    url = file.get("url_private_download") or file.get("url_private")
    if not url:
        return None
    bot_token = secrets.require("slack.bot_token")
    captured_at = datetime.now(timezone.utc)
    seed = f"slack::file::{file.get('id') or url}"
    arc = archive_id(seed, captured_at)
    rendered_dir = paths.ARCHIVE_RENDERED / arc
    clean_dir = paths.ARCHIVE_CLEAN / arc
    rendered_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    name = file.get("name") or f"{file.get('id', 'file')}.bin"
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip() or "file.bin"
    target = rendered_dir / safe
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {bot_token}"}, timeout=60)
        resp.raise_for_status()
        target.write_bytes(resp.content)
    except requests.RequestException as exc:
        log_event("scribe.slack", "file.download_fail",
                  url=url, error=type(exc).__name__, message=str(exc))
        return None

    artifacts = [make_artifact(arc, "rendered", target.name)]
    mime = (file.get("mimetype") or "").lower()
    if mime.startswith("text/") or mime in ("application/json", "application/x-yaml"):
        clean_target = clean_dir / target.name
        clean_target.write_bytes(target.read_bytes())
        artifacts.append(make_artifact(arc, "clean", clean_target.name))

    provenance = Provenance(
        shared_by=sender,
        shared_in=channel,
        shared_at=captured_at,
        context=f"Slack file in {channel}" + (f" (thread {thread_ts})" if thread_ts else ""),
    )
    return CandidateRecord(
        source_type="slack",
        captured_via="poll",
        arc_id=arc,
        seed=seed,
        captured_at=captured_at,
        canonical_url=file.get("permalink"),
        title=file.get("title") or name,
        provenance=provenance,
        artifacts=artifacts,
        extra={
            "slack_file_id": file.get("id"),
            "slack_mimetype": file.get("mimetype"),
            "slack_thread_ts": thread_ts,
        },
    )


def _process_message(
    msg: dict, *, channel: str, channel_name: str,
) -> tuple[list[CandidateRecord], list[EngagementSignal]]:
    candidates: list[CandidateRecord] = []
    signals: list[EngagementSignal] = []
    sender = msg.get("user") or msg.get("bot_id") or "unknown"
    text = msg.get("text") or ""
    thread_ts = msg.get("thread_ts")

    # URLs in message text → web ingestor.
    for url in URL_RE.findall(text):
        url = url.rstrip(".,);]")
        try:
            staged = web_ingestor.ingest_url(
                url,
                provenance=Provenance(
                    shared_by=sender, shared_in=channel,
                    shared_at=_ts_to_dt(msg.get("ts")),
                    context=f"Slack message in {channel_name}",
                ),
                captured_via="poll",
                extra={"slack_channel": channel, "slack_ts": msg.get("ts")},
            )
            candidates.extend(staged)
        except Exception as exc:
            log_event("scribe.slack", "url.fail",
                      url=url, error=type(exc).__name__, message=str(exc))

    # Files attached to the message.
    for file in msg.get("files") or []:
        if file.get("mode") == "external" and file.get("url_private") is None:
            ext_url = file.get("permalink_public") or file.get("permalink")
            if ext_url:
                try:
                    candidates.extend(web_ingestor.ingest_url(
                        ext_url,
                        provenance=Provenance(shared_by=sender, shared_in=channel),
                        captured_via="poll",
                    ))
                except Exception:
                    pass
            continue
        cand = _stage_slack_file(file, sender=sender, channel=channel, thread_ts=thread_ts)
        if cand:
            candidates.append(cand)

    return candidates, signals


def _ts_to_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _engagement_for_channel(
    client: WebClient, *, channel: str, oldest: str | None, self_uid: str,
) -> list[EngagementSignal]:
    """Walk recent messages: my replies, and reactions where I appear."""
    signals: list[EngagementSignal] = []
    messages = _channel_messages(client, channel, oldest=oldest)
    for m in messages:
        ts = _ts_to_dt(m.get("ts")) or datetime.now(timezone.utc)
        # My replies (anything I authored that's a thread reply).
        if m.get("user") == self_uid and m.get("thread_ts") and m.get("thread_ts") != m.get("ts"):
            signals.append(EngagementSignal(
                ts=ts, type="slack_reply", actor="user",
                extra={"channel": channel, "thread_ts": m.get("thread_ts"), "ts": m.get("ts")},
            ))
        # Reactions I added.
        for r in m.get("reactions") or []:
            if self_uid in (r.get("users") or []):
                signals.append(EngagementSignal(
                    ts=ts, type="slack_reaction", actor="user",
                    extra={"channel": channel, "ts": m.get("ts"), "emoji": r.get("name")},
                ))
    return signals


def poll() -> list[CandidateRecord]:
    cfg = poller_config.for_poller("slack")
    state = poller_state.load(STATE_NAME)
    last_ts: dict[str, str] = state.get("last_ts") or {}

    bot = _bot_client()
    self_uid = _self_user_id(bot)

    # Build channel list: all DMs + allowlisted channel IDs.
    channels: list[tuple[str, str]] = []  # (id, display_name)
    for ch in _list_dm_channels(bot):
        channels.append((ch["id"], f"dm:{ch.get('user') or ch['id']}"))
    for cid in cfg.get("allowlisted_channels") or []:
        channels.append((cid, cid))

    candidates: list[CandidateRecord] = []
    all_signals: list[EngagementSignal] = []

    initial_lookback = float(cfg.get("initial_lookback_hours", 24)) * 3600
    initial_oldest = f"{time.time() - initial_lookback:.6f}"

    for cid, name in channels:
        oldest = last_ts.get(cid) or initial_oldest
        messages = _channel_messages(bot, cid, oldest=oldest)
        log_event("scribe.slack", "messages.fetched", channel=cid, count=len(messages))
        max_ts = oldest
        for m in messages:
            cands, sigs = _process_message(m, channel=cid, channel_name=name)
            candidates.extend(cands)
            all_signals.extend(sigs)
            if (m.get("ts") or "") > max_ts:
                max_ts = m["ts"]
        all_signals.extend(_engagement_for_channel(bot, channel=cid, oldest=oldest, self_uid=self_uid))
        last_ts[cid] = max_ts

    if all_signals:
        append_signals(all_signals)
        log_event("scribe.slack", "signals.appended", count=len(all_signals))

    state["last_ts"] = last_ts
    poller_state.save(STATE_NAME, state)
    return candidates


def collect_engagement_signals() -> list[EngagementSignal]:
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday Slack poller.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log_event("scribe.slack", "startup")
    candidates = poll()
    log_event("scribe.slack", "poll.done", candidates=len(candidates))

    if args.dry_run:
        for c in candidates:
            print(f"  candidate: arc_id={c.arc_id} title={c.title}")
        return 0
    if candidates:
        from scribe.pipeline import process_candidates
        results = process_candidates(candidates)
        log_event("scribe.slack", "pipeline.done",
                  decisions=[r["decision"] for r in results])
    return 0


if __name__ == "__main__":
    sys.exit(main())
