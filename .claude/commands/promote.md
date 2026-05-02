---
description: Manually promote an archive record to a memory record. Triggers Synthesizer.
---

Promote an archive record to the Memory tier.

Usage:
- `/promote arc_2026-04-30_14-23-01_a8f2` — promote a specific archive record by ID
- `/promote latest` — promote the most recent archive record (last entry in `Archive/records/`)

Steps:
1. Read the archive record from `Archive/records/{id}.md`.
2. Read its artifacts (`Archive/Clean/{id}/*.md` and optionally `Archive/Rendered/{id}/*.html`).
3. Read `Institutional-Memory/index.md` to understand the existing knowledge structure.
4. Invoke the Synthesizer (`scripts/synthesizer/synthesize.py`) with the archive record ID and `--reason manual`.
5. Verify the resulting memory record in `Institutional-Memory/sources/`, plus any new/updated entity and concept pages.
6. Update the archive record: `status: promoted`, `promoted_to: src_{...}`.
7. Append a line to `Institutional-Memory/log.md`.

If the Synthesizer is not yet built (Phase 3 incomplete), do steps 1–3 manually using the `_templates/` and report the proposed memory record for user review.
