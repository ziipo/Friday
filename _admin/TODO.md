# Friday — Active TODOs

Living checklist. Tasks tracked in the conversation TaskList tool are the source of truth during a working session; this file mirrors them between sessions.

## Phase 0 — Skeleton ✓ COMPLETE

- [x] Move PRD into `_admin/`
- [x] Write implementation plan
- [x] Init git, create public `ziipo/Friday` on GitHub
- [x] Scaffold v2 folder layout
- [x] Vendor templates from `Astro-Han/karpathy-llm-wiki` + `AgriciDaniel/claude-obsidian`
- [x] `uv init` Python 3.13 project; add PRD §11 deps
- [x] `git lfs track "*.html" "*.pdf"`
- [x] Install ArchiveBox + SingleFile + Readability extractor (ArchiveBox runs under Python 3.11 — needs `distutils`, removed in 3.12)
- [x] `archivebox init` in `archivebox-data/`; configured for SingleFile + Readability only per PRD §3
- [x] Smoke test: `archivebox add` 3 URLs; both artifacts produced for each

## Phase 1 — Scribe Watcher MVP (next)

- [ ] `scripts/scribe/watcher.py` using `watchdog`, dispatch by file extension
- [ ] Web ingestor: `.url` file → ArchiveBox → archive_record
- [ ] Markdown ingestor (frontmatter passthrough)
- [ ] PDF ingestor (pdfplumber + raw archive)
- [ ] Email ingestor (mailparser, attachments → sub-records)
- [ ] `SourcePlugin` Protocol per PRD §5.1.2 (Watcher mode)
- [ ] Archive record writer per §4.1
- [ ] launchd plist `com.friday.scribe.plist`
- [ ] Configure Obsidian Web Clipper to write into `Inbox/`
- [ ] Acceptance: Safari clip → archive_record within 60s

## Open questions queued for later

From PRD §9 — defer until the relevant phase:

- [ ] Pandoc vs Python Markdown library (Phase 1, web ingestor)
- [ ] Obsidian Web Clipper write target — `Inbox/` directly vs staging (Phase 1)
- [ ] Slack poller granularity: per-message vs per-thread digest (Phase 4)
- [ ] Calendar attendance heuristic refinement (Phase 4)
- [ ] Drive view-duration threshold (Phase 5, default 60s)
- [ ] Reputation cold start (Phase 2; default uniform 0.5)
- [ ] Demotion sensitivity (Phase 6)
- [ ] Triage relevance threshold 0.7 — tune in Phase 2 with held-out set

## Known issues / quirks

- **ArchiveBox + Wikipedia**: SingleFile extractor fails on `en.wikipedia.org` with "Bad file descriptor" (chromium sandbox issue on this macOS). Other sites work fine. Revisit before Phase 1 if Wikipedia URLs are common; otherwise document and move on.
- **ArchiveBox Python version**: ArchiveBox 0.7.2 imports `distutils` (removed in Python 3.12+). Installed under Python 3.11 via `uv tool install --python 3.11 archivebox`. Friday's own code runs Python 3.13.

## Decisions made

- Repo name: **Friday**, public, under `ziipo` on GitHub
- Folder: `~/Projects/Friday/` (PRD's `~/SecondBrain/` retired)
- Python: 3.13 via `uv` for Friday code; 3.11 for ArchiveBox tool
- Dev artifacts live in top-level `_admin/`, not under `Workspace/`
- Vendored `Astro-Han/karpathy-llm-wiki` + `AgriciDaniel/claude-obsidian` templates (adapted to PRD §4.2 frontmatter schema)
- ArchiveBox installed as a uv tool (system-level CLI), not bundled in Friday's venv
- ArchiveBox configured for SingleFile + Readability + favicon + title only; Mercury/git/media/wget/warc/dom/pdf/screenshot/headers disabled
