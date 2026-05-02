# Friday — Active TODOs

Living checklist. Tasks tracked in the conversation TaskList tool are the source of truth during a working session; this file mirrors them between sessions.

## Phase 0 — Skeleton

- [x] Move PRD into `_admin/`
- [x] Write implementation plan
- [ ] Init git, create public `ziipo/Friday` on GitHub
- [ ] Scaffold v2 folder layout
- [ ] Vendor templates from `Astro-Han/karpathy-llm-wiki` + `AgriciDaniel/claude-obsidian`
- [ ] `uv init` Python 3.13 project; add PRD §11 deps
- [ ] `git lfs track "*.html" "*.pdf"`
- [ ] Install ArchiveBox + SingleFile + Readability extractor
- [ ] `archivebox init` in `archivebox-data/`
- [ ] Smoke test: `archivebox add` 3 URLs; verify artifacts

## Open questions queued for later

From PRD §9 — defer until the relevant phase, but track here:

- [ ] Pandoc vs Python Markdown library (Phase 1, web ingestor)
- [ ] Obsidian Web Clipper write target (Phase 1)
- [ ] Slack poller granularity: per-message vs per-thread digest (Phase 4)
- [ ] Calendar attendance heuristic refinement (Phase 4)
- [ ] Drive view-duration threshold (Phase 5, default 60s)
- [ ] Reputation cold start (Phase 2; default uniform 0.5)
- [ ] Demotion sensitivity (Phase 6)
- [ ] Triage relevance threshold 0.7 — tune in Phase 2 with held-out set

## Decisions made

- Repo name: **Friday**, public, under `ziipo` on GitHub
- Folder: `~/Projects/Friday/` (PRD's `~/SecondBrain/` retired)
- Python: 3.13 via `uv`
- Dev artifacts live in top-level `_admin/`, not under `Workspace/`
- Vendoring `Astro-Han/karpathy-llm-wiki` + `AgriciDaniel/claude-obsidian` templates
