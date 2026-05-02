---
description: Generate a cross-source synthesis page on a topic, drawing from existing memory records.
---

Generate a synthesis page in `Institutional-Memory/synthesis/`.

Usage:
- `/synthesis [topic]` — synthesize what's known about a topic across sources
- `/synthesis evolved [topic]` — generate a "how my thinking on X has evolved" page using captured_at timestamps
- `/synthesis missing-links [topic]` — find concepts/entities related to the topic that aren't yet linked

Steps:
1. Search `Institutional-Memory/sources/` and `concepts/` for material related to the topic.
2. Read the relevant memory records.
3. Use the synthesis template at `Institutional-Memory/_templates/synthesis.md`.
4. Cite specific sources inline.
5. Write to `Institutional-Memory/synthesis/{slug}.md`.
6. Update `Institutional-Memory/index.md` and `log.md`.

If fewer than 3 memory records exist on the topic, report this and ask whether to proceed (synthesis from sparse material is low-value).
