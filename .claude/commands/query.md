---
description: Query the second-brain. Searches Memory tier for "what do I know about X?" and Archive tier for "have I seen anything about X?".
---

Run a query against the Friday knowledge base.

Usage:
- `/query [topic]` — Memory-tier search: returns synthesized knowledge from `Institutional-Memory/`. Use this for "what do I know about X?".
- `/query archive [topic]` — Archive-tier search: scans `Archive/records/*.md` provenance and one-line summaries. Use this for "have I seen anything about X?".
- `/query who shared [topic]` — Provenance search across archive records.

For Memory-tier queries, prefer reading `Institutional-Memory/sources/`, `entities/`, `concepts/`, and `synthesis/` directly. Cite the specific files used.

For Archive-tier queries, also include the artifact path so the user can read the original if needed.
