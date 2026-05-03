"""Weekly sweep — Sunday 03:00 local (PRD §5.5.2).

Steps:
  1. Memory demotion pass: find memory records that are old, unqueried,
     unlinked, and never synthesized → propose demotion (never auto-demote in v1).
  2. Archive pruning pass: find archive records captured > 365 days ago,
     never promoted, low relevance → propose artifact deletion (record stays
     as a tombstone).
  3. Trust ratchet evaluation: recompute per-category auto-apply thresholds
     from Institutional-Memory/.trust-stats.json.

Run:
    PYTHONPATH=scripts uv run python -m janitor.weekly [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from lib import paths
from lib.logging import log_event
from lib.tuning import get as _tune

from . import index as index_mod

DEMOTION_AGE_DAYS: int = int(_tune("weekly", "demotion_age_days", 90))
PRUNE_AGE_DAYS: int = int(_tune("weekly", "prune_age_days", 365))
PRUNE_MAX_RELEVANCE: float = float(_tune("weekly", "prune_max_relevance", 0.3))

TRUST_STATS_PATH = paths.INSTITUTIONAL_MEMORY / ".trust-stats.json"
TRUST_AUTO_APPLY_THRESHOLD: float = float(_tune("trust_ratchet", "auto_apply_threshold", 0.95))
TRUST_MIN_SAMPLES: int = int(_tune("trust_ratchet", "min_samples", 20))
TRUST_WINDOW_DAYS: int = int(_tune("trust_ratchet", "window_days", 30))


# ---------------------------------------------------------------------------
# Step 1: Memory demotion pass
# ---------------------------------------------------------------------------

def _has_inbound_links(src_id: str, all_sources: list[dict[str, Any]]) -> bool:
    """Check if any other source references this src_id in its relations."""
    for other in all_sources:
        for rel in other.get("relations") or []:
            if isinstance(rel, dict) and str(rel.get("target") or "") == src_id:
                return True
    return False


def _in_any_synthesis(src_id: str) -> bool:
    synthesis_dir = paths.INSTITUTIONAL_MEMORY / "synthesis"
    if not synthesis_dir.exists():
        return False
    for p in synthesis_dir.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
            if src_id in text:
                return True
        except OSError:
            pass
    return False


def _demotion_pass(*, dry_run: bool = False) -> dict[str, int]:
    counts = {"checked": 0, "proposed": 0}
    sources_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not sources_dir.exists():
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=DEMOTION_AGE_DAYS)

    # Load all source metadata for inbound-link check.
    all_sources: list[dict[str, Any]] = []
    src_paths: list[tuple[Path, dict[str, Any]]] = []
    for p in sorted(sources_dir.glob("src_*.md")):
        try:
            post = frontmatter.load(p)
            meta = dict(post.metadata)
            meta["_content"] = post.content
            all_sources.append(meta)
            src_paths.append((p, meta))
        except Exception:
            continue

    for src_path, meta in src_paths:
        if meta.get("status") in ("demoted", "dead-link", "superseded"):
            continue
        counts["checked"] += 1

        promoted_at = meta.get("promoted_at")
        if not promoted_at:
            continue
        try:
            pat = datetime.fromisoformat(str(promoted_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if pat >= cutoff:
            continue  # too recent

        src_id = str(meta.get("id") or src_path.stem)

        # Skip if it has inbound links from other sources.
        if _has_inbound_links(src_id, all_sources):
            continue
        # Skip if it appears in a synthesis page.
        if _in_any_synthesis(src_id):
            continue

        log_event("janitor.weekly", "demotion.proposed", src_id=src_id)
        if not dry_run:
            _write_demotion_proposal(src_id, str(meta.get("title") or ""), str(promoted_at))
        counts["proposed"] += 1

    return counts


def _write_demotion_proposal(src_id: str, title: str, promoted_at: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title[:40]).strip("_")
    fname = f"{ts}_demote_{slug}.md"
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    p = paths.REVIEW_QUEUE_PENDING / fname
    p.write_text(
        f"---\ntype: demotion\nsrc_id: {src_id}\npromoted_at: {promoted_at}\n"
        f"created_at: {ts}\n---\n\n# Demotion proposal: {title}\n\n"
        f"Source `{src_id}` was promoted {promoted_at[:10]}, has no inbound links, "
        f"and has never been pulled into a synthesis.\n\n"
        f"**Action:** Review and move to `ReviewQueue/approved/` to demote, "
        f"or delete this proposal to keep.\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Step 2: Archive pruning pass
# ---------------------------------------------------------------------------

def _pruning_pass(*, dry_run: bool = False) -> dict[str, int]:
    counts = {"checked": 0, "proposed": 0}
    if not paths.ARCHIVE_RECORDS.exists():
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_AGE_DAYS)

    for arc_path in sorted(paths.ARCHIVE_RECORDS.glob("arc_*.md")):
        try:
            post = frontmatter.load(arc_path)
        except Exception:
            continue
        meta: dict[str, Any] = post.metadata

        status = str(meta.get("status") or "")
        if status in ("promoted", "discarded"):
            continue  # promoted → lives in memory; discarded → already pruned

        counts["checked"] += 1

        captured_at = meta.get("captured_at")
        if not captured_at:
            continue
        try:
            cat = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if cat >= cutoff:
            continue

        relevance = float(meta.get("relevance_score") or 0.0)
        if relevance >= PRUNE_MAX_RELEVANCE:
            continue

        arc_id = str(meta.get("id") or arc_path.stem)
        log_event("janitor.weekly", "prune.proposed", arc_id=arc_id, relevance=relevance)
        if not dry_run:
            _write_prune_proposal(arc_id, str(meta.get("title") or ""),
                                  str(captured_at), relevance)
        counts["proposed"] += 1

    return counts


def _write_prune_proposal(arc_id: str, title: str, captured_at: str, relevance: float) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title[:40]).strip("_")
    fname = f"{ts}_prune_{slug}.md"
    paths.REVIEW_QUEUE_PENDING.mkdir(parents=True, exist_ok=True)
    p = paths.REVIEW_QUEUE_PENDING / fname
    rendered_dir = paths.ARCHIVE_RENDERED / arc_id
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    artifact_sizes = []
    for d in (rendered_dir, clean_dir):
        if d.exists():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            artifact_sizes.append(f"{d.name}: {size // 1024}KB")
    size_str = ", ".join(artifact_sizes) or "unknown"
    p.write_text(
        f"---\ntype: prune\narc_id: {arc_id}\ncaptured_at: {captured_at}\n"
        f"relevance_score: {relevance}\ncreated_at: {ts}\n---\n\n"
        f"# Prune proposal: {title}\n\n"
        f"Archive record `{arc_id}` captured {captured_at[:10]}, never promoted, "
        f"relevance {relevance:.2f} < {PRUNE_MAX_RELEVANCE}. "
        f"Artifact sizes: {size_str}.\n\n"
        f"**Action:** Approve to delete artifacts (record kept as tombstone), "
        f"or delete this proposal to keep artifacts.\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Step 3: Trust ratchet evaluation
# ---------------------------------------------------------------------------

def _load_trust_stats() -> dict[str, Any]:
    if TRUST_STATS_PATH.exists():
        try:
            return json.loads(TRUST_STATS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_trust_stats(data: dict[str, Any]) -> None:
    TRUST_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRUST_STATS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _trust_ratchet(*, dry_run: bool = False) -> dict[str, int]:
    """Recompute auto-apply thresholds per category.

    Each category tracks how many ReviewQueue proposals the user kept vs.
    total proposed. Once kept/proposed > 0.95 with N > 20 over 30 days,
    the category graduates to auto-apply (trust_ratchet.py Phase 7 will act on it).
    For now (v1) we just recompute and persist the stats.
    """
    stats = _load_trust_stats()
    now = datetime.now(timezone.utc)
    window_cutoff = (now - timedelta(days=TRUST_WINDOW_DAYS)).isoformat().replace("+00:00", "Z")
    counts = {"categories_evaluated": 0, "graduated": 0}

    approved_dir = paths.FRIDAY_ROOT / "ReviewQueue" / "approved"
    if not approved_dir.exists():
        return counts

    by_category: dict[str, dict[str, int]] = {}

    for p in approved_dir.glob("*.md"):
        try:
            post = frontmatter.load(p)
        except Exception:
            continue
        meta = post.metadata
        category = str(meta.get("type") or "unknown")
        created_at = str(meta.get("created_at") or "")
        if created_at < window_cutoff:
            continue
        by_category.setdefault(category, {"kept": 0, "proposed": 0})
        by_category[category]["kept"] += 1

    pending_dir = paths.REVIEW_QUEUE_PENDING
    if pending_dir.exists():
        for p in pending_dir.glob("*.md"):
            try:
                post = frontmatter.load(p)
            except Exception:
                continue
            meta = post.metadata
            category = str(meta.get("type") or "unknown")
            created_at = str(meta.get("created_at") or "")
            if created_at < window_cutoff:
                continue
            by_category.setdefault(category, {"kept": 0, "proposed": 0})
            by_category[category]["proposed"] += 1

    for category, cat_counts in by_category.items():
        counts["categories_evaluated"] += 1
        total = cat_counts["kept"] + cat_counts["proposed"]
        ratio = cat_counts["kept"] / total if total > 0 else 0.0
        graduated = (ratio >= TRUST_AUTO_APPLY_THRESHOLD and total >= TRUST_MIN_SAMPLES)
        stats[category] = {
            "kept": cat_counts["kept"],
            "proposed": cat_counts["proposed"],
            "ratio": round(ratio, 4),
            "graduated": graduated,
            "last_evaluated": now.isoformat().replace("+00:00", "Z"),
        }
        if graduated:
            counts["graduated"] += 1
            log_event("janitor.weekly", "trust.graduated", category=category, ratio=ratio, total=total)

    if not dry_run:
        _save_trust_stats(stats)

    return counts


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(*, dry_run: bool = False) -> None:
    start = datetime.now(timezone.utc)
    log_event("janitor.weekly", "start", dry_run=dry_run)
    results: dict[str, Any] = {}

    r = _demotion_pass(dry_run=dry_run)
    results["demotion"] = r
    log_event("janitor.weekly", "step.demotion", **r)

    r = _pruning_pass(dry_run=dry_run)
    results["pruning"] = r
    log_event("janitor.weekly", "step.pruning", **r)

    r = _trust_ratchet(dry_run=dry_run)
    results["trust"] = r
    log_event("janitor.weekly", "step.trust", **r)

    # Regenerate index after potential status changes.
    idx = index_mod.rebuild_index(dry_run=dry_run)
    results["index"] = idx

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    summary = _format_summary(results, elapsed)
    index_mod.append_log(f"**Weekly sweep** — {summary}", dry_run=dry_run)
    log_event("janitor.weekly", "done", elapsed_s=round(elapsed, 1))
    print(f"Weekly sweep complete in {elapsed:.1f}s — {summary}")


def _format_summary(results: dict[str, Any], elapsed: float) -> str:
    parts = []
    d = results.get("demotion") or {}
    if d.get("proposed"):
        parts.append(f"{d['proposed']} demotion proposals")
    p = results.get("pruning") or {}
    if p.get("proposed"):
        parts.append(f"{p['proposed']} prune proposals")
    t = results.get("trust") or {}
    if t.get("graduated"):
        parts.append(f"{t['graduated']} trust categories graduated")
    parts.append(f"{elapsed:.0f}s")
    return ", ".join(parts) if parts else "nothing to do"


def main() -> int:
    parser = argparse.ArgumentParser(description="Friday weekly sweep.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
