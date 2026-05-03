"""Nightly sweep — 02:00 local (PRD §5.5.1).

Steps:
  1. Re-capture queue: re-fetch web sources, append new timestamped artifacts.
  2. Diff & flag: classify changes as trivial | notable | breaking.
     (Handled inside recapture.recapture_all.)
  3. Staleness check: sources with last_verified > 30d and no re-capture → mark stale.
  4. Link rot check: HTTP HEAD on every canonical_url; 4xx/5xx > 7d → mark dead-link.
  5. Conflict detection: LLM scans recent additions for contradictions with existing notes.
  6. Reputation update: tally today's pipeline decisions → update .reputation.json.
  7. Index regeneration: rebuild Institutional-Memory/index.md.
  8. Log: append summary to log.md.

Run:
    PYTHONPATH=scripts uv run python -m janitor.nightly [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import frontmatter
import requests
import yaml

from lib import llm, paths, poller_state
from lib.logging import log_event
from lib.tuning import get as _tune

from . import index as index_mod
from . import reputation as rep_mod
from .recapture import recapture_all

STALE_DAYS: int = int(_tune("janitor", "stale_days", 30))
DEAD_LINK_GRACE_DAYS: int = int(_tune("janitor", "dead_link_grace_days", 7))
CONFLICT_LOOKBACK_DAYS: int = int(_tune("janitor", "conflict_lookback_days", 3))
CONFLICT_CONTEXT_SOURCES: int = int(_tune("janitor", "conflict_context_sources", 10))


# ---------------------------------------------------------------------------
# Step 3: Staleness check
# ---------------------------------------------------------------------------

def _check_staleness(*, dry_run: bool = False) -> dict[str, int]:
    counts = {"checked": 0, "marked_stale": 0}
    sources_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not sources_dir.exists():
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)

    for src_path in sorted(sources_dir.glob("src_*.md")):
        try:
            post = frontmatter.load(src_path)
        except Exception:
            continue
        meta: dict[str, Any] = post.metadata
        if meta.get("status") in ("stale", "dead-link", "superseded", "demoted"):
            continue
        counts["checked"] += 1

        last_v = meta.get("last_verified")
        if not last_v:
            continue
        try:
            lv_dt = datetime.fromisoformat(str(last_v).replace("Z", "+00:00"))
        except ValueError:
            continue

        # Only flag as stale if NOT web (web gets re-captured instead).
        arc_id = meta.get("archive_record") or ""
        source_type = _get_arc_source_type(arc_id)
        if source_type == "web":
            continue

        if lv_dt < cutoff:
            log_event("janitor.nightly", "staleness.flagged",
                      src_id=meta.get("id"), last_verified=str(last_v))
            if not dry_run:
                _patch_src_status(src_path, post, "stale")
                _write_stale_proposal(str(meta.get("id") or src_path.stem),
                                      str(meta.get("title") or ""), str(last_v))
            counts["marked_stale"] += 1

    return counts


def _get_arc_source_type(arc_id: str) -> str:
    if not arc_id:
        return ""
    arc_path = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
    if not arc_path.exists():
        return ""
    try:
        post = frontmatter.load(arc_path)
        return str(post.metadata.get("source_type") or "")
    except Exception:
        return ""


def _patch_src_status(src_path: Path, post: frontmatter.Post, status: str) -> None:
    post["status"] = status
    yaml_text = yaml.safe_dump(dict(post.metadata), sort_keys=False, allow_unicode=True)
    src_path.write_text(f"---\n{yaml_text}---\n\n{post.content}", encoding="utf-8")


def _write_stale_proposal(src_id: str, title: str, last_verified: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title[:40]).strip("_")
    fname = f"{ts}_stale_{slug}.md"
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    p = paths.REVIEW_QUEUE_PENDING / fname
    p.write_text(
        f"---\ntype: staleness\nsrc_id: {src_id}\nlast_verified: {last_verified}\n"
        f"created_at: {ts}\n---\n\n# Staleness flag: {title}\n\n"
        f"Source `{src_id}` has not been verified since {last_verified[:10]} "
        f"(>{STALE_DAYS} days ago) and cannot be re-captured automatically.\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Step 4: Link rot check
# ---------------------------------------------------------------------------

def _check_link_rot(*, dry_run: bool = False) -> dict[str, int]:
    counts = {"checked": 0, "dead": 0, "errors": 0}
    state = poller_state.load("janitor_linkrot")
    # dead_since: url -> ISO timestamp when we first saw it fail
    dead_since: dict[str, str] = state.get("dead_since") or {}
    now = datetime.now(timezone.utc)
    grace = timedelta(days=DEAD_LINK_GRACE_DAYS)

    sources_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not sources_dir.exists():
        return counts

    updated_dead_since: dict[str, str] = {}

    for src_path in sorted(sources_dir.glob("src_*.md")):
        try:
            post = frontmatter.load(src_path)
        except Exception:
            continue
        meta: dict[str, Any] = post.metadata
        if meta.get("status") in ("dead-link", "demoted"):
            continue
        url = meta.get("canonical_url")
        if not url or not str(url).startswith("http"):
            continue
        counts["checked"] += 1

        try:
            resp = requests.head(url, timeout=10, allow_redirects=True,
                                 headers={"User-Agent": "Friday/1.0 link-rot-check"})
            status_code = resp.status_code
        except requests.RequestException:
            status_code = 0

        if status_code >= 400 or status_code == 0:
            if url not in dead_since:
                dead_since[url] = now.isoformat().replace("+00:00", "Z")
            updated_dead_since[url] = dead_since[url]
            # Mark dead-link only after grace period.
            try:
                first_fail = datetime.fromisoformat(dead_since[url].replace("Z", "+00:00"))
            except ValueError:
                first_fail = now
            if now - first_fail >= grace:
                src_id = str(meta.get("id") or src_path.stem)
                title = str(meta.get("title") or "")
                log_event("janitor.nightly", "link_rot.dead",
                          src_id=src_id, url=url, status_code=status_code)
                if not dry_run:
                    _patch_src_status(src_path, post, "dead-link")
                counts["dead"] += 1
        # URLs that pass don't accumulate in dead_since.

    if not dry_run:
        state["dead_since"] = updated_dead_since
        poller_state.save("janitor_linkrot", state)

    return counts


# ---------------------------------------------------------------------------
# Step 5: Conflict detection
# ---------------------------------------------------------------------------

def _check_conflicts(*, dry_run: bool = False) -> dict[str, int]:
    counts = {"scanned": 0, "conflicts": 0}
    sources_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not sources_dir.exists():
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=CONFLICT_LOOKBACK_DAYS)
    recent: list[tuple[str, str, str]] = []  # (src_id, title, summary)
    all_summaries: list[tuple[str, str, str]] = []  # (src_id, title, summary)

    for src_path in sorted(sources_dir.glob("src_*.md")):
        try:
            post = frontmatter.load(src_path)
        except Exception:
            continue
        meta: dict[str, Any] = post.metadata
        src_id = str(meta.get("id") or src_path.stem)
        title = str(meta.get("title") or src_id)
        summary = str(meta.get("summary") or "")
        all_summaries.append((src_id, title, summary))

        promoted_at = meta.get("promoted_at")
        if not promoted_at:
            continue
        try:
            pat = datetime.fromisoformat(str(promoted_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if pat >= cutoff:
            recent.append((src_id, title, summary))

    if not recent:
        return counts

    context_block = "\n".join(
        f"- [{sid}] {t}: {s[:300]}"
        for sid, t, s in all_summaries[-CONFLICT_CONTEXT_SOURCES:]
    )
    for src_id, title, summary in recent:
        counts["scanned"] += 1
        prompt = (
            "You are a knowledge-base consistency checker. Given a new source and "
            "a set of existing sources, identify any direct factual contradictions.\n\n"
            "Respond with JSON: {\"contradicts\": [\"src_id_of_conflicting_source\", ...], "
            "\"rationale\": \"one sentence\"}. "
            "If no contradictions, return {\"contradicts\": [], \"rationale\": \"\"}.\n\n"
            f"New source [{src_id}] {title}:\n{summary[:500]}\n\n"
            f"Existing sources:\n{context_block}"
        )
        try:
            raw = llm.complete(
                system="Output only valid JSON.",
                messages=[{"role": "user", "content": prompt}],
                job="triage",
                temperature=0.0,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
            result = json.loads(raw)
            conflicts = result.get("contradicts") or []
            rationale = result.get("rationale") or ""
        except Exception as exc:
            log_event("janitor.nightly", "conflict.fail",
                      src_id=src_id, error=type(exc).__name__, message=str(exc))
            continue

        if conflicts:
            log_event("janitor.nightly", "conflict.found",
                      src_id=src_id, contradicts=conflicts, rationale=rationale)
            if not dry_run:
                _write_conflict_proposal(src_id, title, conflicts, rationale)
            counts["conflicts"] += 1

    return counts


def _write_conflict_proposal(src_id: str, title: str, conflicts: list[str], rationale: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title[:40]).strip("_")
    fname = f"{ts}_conflict_{slug}.md"
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    p = paths.REVIEW_QUEUE_PENDING / fname
    conflict_list = "\n".join(f"- `{c}`" for c in conflicts)
    p.write_text(
        f"---\ntype: conflict\nsrc_id: {src_id}\ncontradicts: {json.dumps(conflicts)}\n"
        f"created_at: {ts}\n---\n\n# Conflict: {title}\n\n"
        f"**Rationale:** {rationale}\n\n**Contradicts:**\n{conflict_list}\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(*, dry_run: bool = False) -> None:
    start = datetime.now(timezone.utc)
    log_event("janitor.nightly", "start", dry_run=dry_run)
    results: dict[str, Any] = {}

    # Step 1+2: Re-capture + diff classification.
    r = recapture_all(dry_run=dry_run)
    results["recapture"] = r
    log_event("janitor.nightly", "step.recapture", **r)

    # Step 3: Staleness.
    r = _check_staleness(dry_run=dry_run)
    results["staleness"] = r
    log_event("janitor.nightly", "step.staleness", **r)

    # Step 4: Link rot.
    r = _check_link_rot(dry_run=dry_run)
    results["link_rot"] = r
    log_event("janitor.nightly", "step.link_rot", **r)

    # Step 5: Conflict detection.
    r = _check_conflicts(dry_run=dry_run)
    results["conflicts"] = r
    log_event("janitor.nightly", "step.conflicts", **r)

    # Step 6: Reputation update.
    r = rep_mod.update(dry_run=dry_run)
    results["reputation"] = r
    log_event("janitor.nightly", "step.reputation", **r)

    # Step 7: Index regeneration.
    r = index_mod.rebuild_index(dry_run=dry_run)
    results["index"] = r
    log_event("janitor.nightly", "step.index", **r)

    # Step 8: Log.
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    summary = _format_summary(results, elapsed)
    index_mod.append_log(f"**Nightly sweep** — {summary}", dry_run=dry_run)
    log_event("janitor.nightly", "done", elapsed_s=round(elapsed, 1))
    print(f"Nightly sweep complete in {elapsed:.1f}s — {summary}")


def _format_summary(results: dict[str, Any], elapsed: float) -> str:
    parts = []
    rc = results.get("recapture") or {}
    if rc.get("recaptured"):
        parts.append(f"{rc['recaptured']} re-captured, {rc.get('flagged', 0)} flagged")
    sc = results.get("staleness") or {}
    if sc.get("marked_stale"):
        parts.append(f"{sc['marked_stale']} stale")
    lc = results.get("link_rot") or {}
    if lc.get("dead"):
        parts.append(f"{lc['dead']} dead links")
    cc = results.get("conflicts") or {}
    if cc.get("conflicts"):
        parts.append(f"{cc['conflicts']} conflicts")
    rc2 = results.get("reputation") or {}
    rep_total = rc2.get("channels_updated", 0) + rc2.get("senders_updated", 0)
    if rep_total:
        parts.append(f"{rep_total} reputation entries updated")
    idx = results.get("index") or {}
    parts.append(f"index: {idx.get('sources', 0)}s/{idx.get('entities', 0)}e/{idx.get('concepts', 0)}c")
    parts.append(f"{elapsed:.0f}s")
    return ", ".join(parts) if parts else "nothing to do"


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday nightly sweep.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be done without writing anything.")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
