"""Trust ratchet applier — Phase 7 (PRD §5.5.3).

Reads Institutional-Memory/.trust-stats.json (written by janitor/weekly.py).
For each proposal category that has graduated (ratio > 0.95, N > 20 over 30d),
auto-applies pending proposals by moving them to ReviewQueue/approved/.

Paranoid mode (--review-all):
  Disregards graduation status and leaves ALL proposals in pending/ for human
  review. Useful when the user wants to audit a sweep before it takes effect.

Normal mode (default):
  - Graduated categories: proposals moved to approved/ automatically.
  - Non-graduated categories: proposals left in pending/ untouched.

The applier does NOT modify memory records or archive records directly.
Actual content mutations (demotion, pruning, conflict merge) are applied by
the Janitor's next sweep after a proposal lands in approved/.

Run:
    PYTHONPATH=scripts uv run python -m trust_ratchet.apply [--review-all] [--dry-run]
    PYTHONPATH=scripts uv run python -m trust_ratchet.apply --status
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import paths
from lib.logging import log_event

TRUST_STATS_PATH = paths.INSTITUTIONAL_MEMORY / ".trust-stats.json"
PENDING_DIR = paths.FRIDAY_ROOT / "ReviewQueue" / "pending"
APPROVED_DIR = paths.FRIDAY_ROOT / "ReviewQueue" / "approved"


# ---------------------------------------------------------------------------
# Trust stats
# ---------------------------------------------------------------------------

def load_trust_stats() -> dict[str, Any]:
    if TRUST_STATS_PATH.exists():
        try:
            return json.loads(TRUST_STATS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def graduated_categories(stats: dict[str, Any]) -> set[str]:
    """Return the set of category names that have graduated to auto-apply."""
    return {cat for cat, data in stats.items() if data.get("graduated") is True}


# ---------------------------------------------------------------------------
# Proposal scanning
# ---------------------------------------------------------------------------

def _proposal_category(path: Path) -> str | None:
    """Extract the 'type' field from a proposal's frontmatter, or None on failure."""
    try:
        import frontmatter
        post = frontmatter.load(path)
        return str(post.metadata.get("type") or "")
    except Exception:
        return None


def pending_proposals() -> list[tuple[Path, str]]:
    """Return [(path, category)] for all proposals in ReviewQueue/pending/."""
    if not PENDING_DIR.exists():
        return []
    results = []
    for p in sorted(PENDING_DIR.glob("*.md")):
        cat = _proposal_category(p)
        if cat:
            results.append((p, cat))
    return results


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply(*, review_all: bool = False, dry_run: bool = False) -> dict[str, int]:
    """Move graduated proposals from pending/ to approved/.

    Returns summary counts:
      applied   — proposals moved to approved/ automatically
      skipped   — proposals left in pending/ (non-graduated or review_all)
      errors    — proposals that couldn't be processed
    """
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    stats = load_trust_stats()
    graduated = graduated_categories(stats)

    counts = {"applied": 0, "skipped": 0, "errors": 0}
    proposals = pending_proposals()

    if review_all:
        log_event("trust_ratchet", "paranoid_mode",
                  pending=len(proposals), graduated_categories=sorted(graduated))
        counts["skipped"] = len(proposals)
        if proposals:
            print(f"  [--review-all] {len(proposals)} proposals left in pending/ for human review.")
        return counts

    for proposal_path, category in proposals:
        if category not in graduated:
            counts["skipped"] += 1
            continue

        dest = APPROVED_DIR / proposal_path.name
        # Avoid collision: append a counter suffix if the name already exists.
        if dest.exists():
            stem = proposal_path.stem
            suffix = proposal_path.suffix
            counter = 1
            while dest.exists():
                dest = APPROVED_DIR / f"{stem}_{counter}{suffix}"
                counter += 1

        log_event("trust_ratchet", "auto_apply",
                  category=category, proposal=proposal_path.name, dry_run=dry_run)

        if dry_run:
            print(f"  [dry-run] would apply {category}: {proposal_path.name}")
            counts["applied"] += 1
            continue

        try:
            shutil.move(str(proposal_path), str(dest))
            counts["applied"] += 1
        except OSError as exc:
            log_event("trust_ratchet", "apply.error",
                      proposal=proposal_path.name,
                      error=type(exc).__name__, message=str(exc))
            counts["errors"] += 1

    log_event("trust_ratchet", "cycle.done", **counts,
              graduated_categories=sorted(graduated))
    return counts


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def print_status() -> None:
    stats = load_trust_stats()
    proposals = pending_proposals()
    by_cat: dict[str, int] = {}
    for _, cat in proposals:
        by_cat[cat] = by_cat.get(cat, 0) + 1

    print("Trust ratchet status")
    print(f"  Stats file: {TRUST_STATS_PATH}")
    print(f"  Pending proposals: {len(proposals)}")
    print()

    if not stats:
        print("  No trust stats yet. Run the weekly sweep to populate.")
        return

    print(f"  {'Category':<20} {'Kept':>6} {'Proposed':>9} {'Ratio':>7} {'Graduated':>10} {'Pending':>8}")
    print("  " + "-" * 66)
    for cat, data in sorted(stats.items()):
        kept = data.get("kept", 0)
        proposed = data.get("proposed", 0)
        ratio = data.get("ratio", 0.0)
        graduated = "YES ✓" if data.get("graduated") else "no"
        pending = by_cat.get(cat, 0)
        print(f"  {cat:<20} {kept:>6} {proposed:>9} {ratio:>7.3f} {graduated:>10} {pending:>8}")

    not_in_stats = set(by_cat) - set(stats)
    if not_in_stats:
        print()
        for cat in sorted(not_in_stats):
            print(f"  {cat:<20} {'—':>6} {'—':>9} {'—':>7} {'(no data)':>10} {by_cat[cat]:>8}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Friday trust ratchet — auto-apply graduated ReviewQueue proposals."
    )
    parser.add_argument("--review-all", action="store_true",
                        help="Paranoid mode: leave all proposals in pending/ regardless of graduation.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be applied without moving any files.")
    parser.add_argument("--status", action="store_true",
                        help="Print graduation status for each category and exit.")
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    log_event("trust_ratchet", "startup", review_all=args.review_all, dry_run=args.dry_run)
    counts = apply(review_all=args.review_all, dry_run=args.dry_run)
    print(
        f"Trust ratchet: applied={counts['applied']} "
        f"skipped={counts['skipped']} errors={counts['errors']}"
    )
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
