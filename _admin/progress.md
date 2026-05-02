# Friday — Progress Log

Reverse-chronological. One entry per session or major milestone.

## 2026-05-02 — Phase 0 complete

Phase 0 (Skeleton) finished in one session:

- ✅ `_admin/` workspace with PRD, plan, TODOs, test plans, vendoring attribution
- ✅ Public repo `ziipo/Friday` on GitHub, with LFS for `*.html` and `*.pdf`
- ✅ v2 folder layout scaffolded per PRD §3.2 (renamed `SecondBrain` → `Friday`)
- ✅ Vendored templates from `Astro-Han/karpathy-llm-wiki` + `AgriciDaniel/claude-obsidian` — adapted to PRD §4.2 frontmatter schema
- ✅ Slash commands `/query`, `/promote`, `/synthesis` written
- ✅ Python 3.13 uv project with PRD §11 deps
- ✅ ArchiveBox installed under Python 3.11 (it imports `distutils`, removed in 3.12+); SingleFile + Readability extractors via npm
- ✅ Smoke test: 3 of 5 URLs archived with both artifacts. The 2 failures are Wikipedia URLs hitting an apparent local chromium sandbox issue ("Bad file descriptor"). 3 successful archives (example.com, Claude Code docs, Anthropic news) all have `singlefile.html` + `readability/` directory.

**Acceptance criterion met**: manual `archivebox add` produces both Rendered (HTML) and Clean (Markdown) artifacts.

Decisions:
- Repo public on GitHub under `ziipo`
- Python 3.13 for Friday; ArchiveBox runs under 3.11 via `uv tool`
- ArchiveBox configured for SingleFile + Readability + favicon + title only; everything else disabled
- `_admin/` is top-level dev workspace, not under PARA

Open issue to revisit in Phase 1: Wikipedia archival fails on this machine. Probably a sandbox/SSL state quirk; not blocking.

## 2026-05-02 — Project kickoff

- Reviewed PRD v2.0
- Confirmed scope: rename to **Friday**, drop "flight recorder" framing
- Drafted [implementation plan](./implementation-plan.md)
- Established `_admin/` as dev workspace (top-level, not under PARA)
- Decided: Python 3.13 via `uv`; public repo `ziipo/Friday`
- Identified vendoring targets: `Astro-Han/karpathy-llm-wiki`, `AgriciDaniel/claude-obsidian`
- Phase 0 in progress
