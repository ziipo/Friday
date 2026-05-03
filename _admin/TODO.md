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

## Phase 1 — Scribe Watcher MVP ✓ COMPLETE

- [x] `scripts/scribe/watcher.py` using `watchdog`, dispatch by file extension
- [x] Web ingestor: `.url` file → ArchiveBox → archive_record
- [x] Markdown ingestor (frontmatter passthrough)
- [x] PDF ingestor (pdfplumber + raw archive + text extract)
- [x] Email ingestor (parses .eml, attachments → sub-records)
- [x] `SourcePlugin` Protocol per PRD §5.1.2 (Watcher mode)
- [x] Archive record writer per §4.1
- [x] launchd plist `com.friday.scribe.plist`
- [x] Acceptance: 3 file types end-to-end → archive_records in <30s

User-facing setup tasks (deferred — not required for code correctness):
- [ ] Install launchd plist (`launchctl bootstrap`) — see `scripts/launchd/README.md`
- [ ] Configure Obsidian Web Clipper to write `.url` into `Inbox/`

## Phase 2 — Triage ✓ COMPLETE

- [x] `scripts/triage/scorer.py` — Sonnet call returning JSON per §5.2.2
- [x] Memory-tier context loader (entities, concepts, recent titles; cap ~2k tokens)
- [x] Decision matrix per §5.2.3
- [x] `Institutional-Memory/.reputation.json` reader (writer in Phase 6)
- [x] Eval harness with 10 hand-labeled items (9 implemented and passing)
- [x] Acceptance: 10 varied items produce sensible decisions

## Phase 3 — Synthesizer ✓ COMPLETE

- [x] `prompts/synthesize.md` — single-call JSON schema covering memory body + entity/concept extraction + relations + reconciliation flags
- [x] `scripts/synthesizer/models.py` — typed parsing of the LLM JSON response
- [x] `scripts/synthesizer/memory_record.py` — writer per §4.2; reuses arc hash for src id
- [x] `scripts/synthesizer/upsert.py` — entity/concept upsert with alias matching + backlink append
- [x] `scripts/synthesizer/reconcile.py` — ReviewQueue/pending/ proposals (v1 review-required behavior)
- [x] `scripts/synthesizer/synthesize.py` — orchestrator + CLI (`python -m synthesizer.synthesize <arc_id>`)
- [x] Stamps archive record `status=promoted, promoted_to=<src_id>`
- [x] Acceptance: end-to-end run on a fresh markdown ingestion produced memory record + 2 entities + 2 concepts with backlinks; existing Personal Second Brain referenced via relations rather than recreated.

## Phase 4 — Pollers ✓ COMPLETE

- [x] Extended `SourcePlugin` Protocol: `CandidateRecord` gains `arc_id`, `seed`, `captured_at` (ingestors pre-set, pipeline reuses)
- [x] `scripts/lib/poller_state.py` — atomic JSON cursor persistence per poller
- [x] `scripts/lib/poller_config.py` — YAML config loader with `.local.yaml` deep-merge override
- [x] `scripts/lib/engagement.py` — EngagementLog writer (`EngagementLog/{date}.jsonl`), flock-protected appends
- [x] `scripts/lib/google_oauth.py` — Keychain-backed installed-app OAuth flow
- [x] `scripts/auth/google_calendar.py` — one-time Calendar OAuth install script
- [x] `scripts/auth/google_drive.py` — one-time Drive OAuth install script
- [x] `scripts/auth/slack.py` — Slack token verification script
- [x] `scripts/config/pollers.yaml` — poller config (lookback windows, size buckets, allowlists)
- [x] **Calendar poller** — events ±7d, attached docs → web ingestor, attendance/organizer engagement signals, syncToken incremental fetch
- [x] **Drive poller** — designated folder + recent/starred files, Workspace export (Doc/Sheet/Slide → PDF+text), Drive Activity API engagement signals
- [x] **Slack poller** — all DMs + allowlisted channels, URL extraction → web ingestor, file download, reply/reaction engagement signals
- [x] launchd plists: `com.friday.poller.calendar.plist` (60 min), `com.friday.poller.drive.plist` (30 min), `com.friday.poller.slack.plist` (15 min)
- [x] `scripts/setup_secrets.py` — extended with Google OAuth client credentials and Slack token prompts
- [x] Refactored all Phase 1 ingestors to return `CandidateRecord` without writing archive records (pipeline owns the write)
- [x] `watcher.py` — now calls `process_candidates()` inline, logs pipeline decisions

User-facing setup tasks (deferred — not required for code correctness):
- [ ] Run `setup_secrets.py` to register Google/Slack credentials in Keychain
- [ ] Run `python -m auth.google_calendar` + `auth.google_drive` + `auth.slack` for OAuth tokens
- [ ] Install and bootstrap the 3 new launchd plists (see `scripts/launchd/README.md`)
- [ ] Set `drive.designated_folder_id` in `pollers.local.yaml`
- [ ] Allowlist desired Slack channel IDs in `pollers.local.yaml`

## Phase 5 — Promoter (next)

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
