# Friday — Progress Log

Reverse-chronological. One entry per session or major milestone.

## 2026-05-03 — Phase 4 complete

Three pollers delivered (Calendar, Drive, Slack) plus the shared infrastructure they depend on.

- ✅ `scripts/lib/poller_state.py` — atomic cursor persistence (temp + rename) so a crashed poller can't corrupt its own state.
- ✅ `scripts/lib/poller_config.py` — YAML config with `.local.yaml` deep-merge for per-machine overrides (gitignored).
- ✅ `scripts/lib/engagement.py` — append-only EngagementLog writer with `fcntl.flock` so concurrent pollers can't tear each other's lines.
- ✅ `scripts/lib/google_oauth.py` — installed-app OAuth flow backed by macOS Keychain; auto-refreshes access tokens.
- ✅ Calendar poller: syncToken incremental fetch (falls back on 410 Gone), attachment extraction → web ingestor, did_attend + was_organizer engagement signals with PTO suppression and meeting-size bucketing.
- ✅ Drive poller: designated-folder + recent/starred sweep, Workspace type export (Doc/Sheet/Slide → PDF + text/csv/plain), Drive Activity API for view/comment/edit signals. Seen-file-id set caps at 5k entries.
- ✅ Slack poller: all DMs + allowlisted channels, URL extraction, Slack-hosted file download, reply/reaction engagement signals, per-channel `last_ts` cursor.
- ✅ Three launchd plists (15/30/60 min cadences) and updated README.
- ✅ `setup_secrets.py` extended with Google OAuth client credentials + Slack tokens.
- ✅ All Phase 1 ingestors refactored: they now return `CandidateRecord` with pre-set `arc_id`/`seed`/`captured_at`; the pipeline owns the archive record write. `protocol.py` updated accordingly.

Notable design calls:
- Pollers pre-stage artifacts on disk (Drive downloads, Slack file downloads) and pre-compute the `arc_id` before the pipeline sees them, so the pipeline can write the record under the exact same ID without re-running the download.
- Drive's designated-folder bypass skips the relevance gate intentionally — anything the user puts there is unconditionally ingested per PRD §5.1.6.
- Slack search.messages (for @-mention threads) requires a user token (xoxp-), which is a higher-privilege credential than the bot token. Flagged as a risk in PRD §5.1.4; documented in setup_secrets prompts.

## 2026-05-03 — Phase 3 complete

Synthesizer (PRD §5.4) delivered as a single-pass design — one LLM call returns the entire structured payload (memory body + entities + concepts + relations + reconciliation flags), avoiding the four round-trips a per-prompt design would require.

- ✅ `prompts/synthesize.md` defines the JSON schema. Closed vocabulary for relation types and entity types, conservative extraction rules, explicit "don't re-create existing pages — reference them via relations" rule.
- ✅ `scripts/synthesizer/models.py` parses with permissive defaults (unknown relation type → `mentions`, unknown entity_type → `other`) and JSON fence stripping.
- ✅ `memory_record.py` writes `Institutional-Memory/sources/{src_id}_{slug}.md` per §4.2. Reuses the arc hash for `src_id` per §4.5. Lifts `canonical_url`, `captured_at`, `source_type`, `artifacts` forward from the archive record.
- ✅ `upsert.py` creates new entity/concept pages or appends `src_id` to the `sources:` list of an existing page. Alias-aware matching; merges new aliases on update.
- ✅ `reconcile.py` writes `ReviewQueue/pending/{ts}_{kind}_{slug}.md` for `contradicts`/`supersedes` flags (PRD §5.4.3 v1 review-required behavior).
- ✅ `synthesize.py` orchestrator + CLI. Stamps archive record `status=promoted, promoted_to=<src_id>`.
- ✅ End-to-end smoke: dropped a markdown file in Inbox → watcher+pipeline → triage fast_tracked at 0.95 → ran `python -m synthesizer.synthesize <arc_id>` → memory record + 2 new entities (Promoter, Janitor) + 2 new concepts (Archive-to-Memory Promotion, Missing Data Principle). The existing **Personal Second Brain** concept was correctly referenced via `relations: elaborates_on` rather than recreated, validating the de-dup rule.

Notable design call: a single-call synthesis is cheaper and produces more coherent extraction than a four-prompt fan-out (entities, concepts, summary, reconcile separately), at the cost of one larger output token budget. Reverting to fan-out is straightforward if the model struggles with the combined schema as Memory tier grows.

Known minor issue: one `depends_on` relation in the smoke test had the existing source's summary string concatenated into the target field. Prompt could be tightened to demand bare titles in `relations.target` when referencing existing notes by ID. Logged as a minor follow-up; doesn't block Phase 4.

## 2026-05-02 — Phase 2 complete

Triage layer delivered and verified:

- ✅ Seeded `Institutional-Memory/` with initial entities (Friday, Anthropic) and concepts (Personal Second Brain, LLM Orchestration) to provide context for the LLM.
- ✅ Integrated `scripts/scribe/pipeline.py` into `scripts/scribe/watcher.py`, connecting capture to evaluation.
- ✅ Updated `scripts/triage/context.py` to include source IDs in memory snapshots, enabling LLM-based duplicate detection.
- ✅ Built evaluation harness `scripts/triage/eval.py` with 9 test cases.
- ✅ Verified Triage scoring, decision matrix (FAST_TRACK, ARCHIVE_ONLY, DISCARD), and duplicate detection (LINK_DUPLICATE) via Sonnet (falling back to OpenRouter). All 9 cases pass.

Notable design call: the pipeline now raises `RuntimeError` on `arc_id` drift to ensure that ingestors and the pipeline writer use the exact same deterministic ID logic. This caught a few bugs in the eval harness setup.

## 2026-05-02 — Phase 1 complete

Scribe Watcher MVP delivered in the same session as Phase 0:

- ✅ Shared lib (`scripts/lib/`): `paths`, `ids`, `protocol`, `archive_record` writer, `logging`. `SourcePlugin` Protocol declares both watcher and poller methods so Phase 4 won't be a breaking change.
- ✅ Four ingestors:
  - `web.py` — parses `.url` file (Windows INI or plain text), shells out to `archivebox add` with timestamp fragment per PRD §5.1.7, copies `singlefile.html` + `readability/content.{html,txt}` into `Archive/{Rendered,Clean}/{arc_id}/`.
  - `markdown.py` — preserves frontmatter into `extra.upstream_frontmatter`; uses canonical_url from frontmatter when present.
  - `pdf.py` — content-hashes the PDF (deterministic arc_id), keeps original under Rendered, extracts text via pdfplumber to Clean. Reads PDF metadata Title; falls back to first non-empty line.
  - `email.py` — parses `.eml` via stdlib `email` with default policy, stores raw eml + body.txt, emits one CandidateRecord per attachment with `parent_arc` linkback.
- ✅ `watchdog`-based watcher (`scripts/scribe/watcher.py`) with `--once` mode. Dispatches by extension, waits for files to be size-stable before processing, moves originals to `Inbox/processed/` (success) or `Inbox/failed/` (with `.error.log`).
- ✅ launchd plist at `scripts/launchd/com.friday.scribe.plist` with `KeepAlive`, `RunAtLoad`, throttle, and stdout/stderr redirected to `.logs/`. README documents install/status/uninstall.
- ✅ End-to-end acceptance test: 3 files (.url, .md, .pdf) dropped into Inbox while watcher runs in background. All 3 archive_records produced; URL took ~15s (network), md/pdf each <2s. Far under the 60s target.

Notable design call: the `archive_record` writer's serializer falls back to `str()` for unknown types so a stray `email.headerregistry.Address` (or similar) can't crash the pipeline. Was triggered by the email ingestor smoke test; locked down both at source (str()-coerce headers in email.py) and as a defense-in-depth (str() fallback in archive_record._serialize).

User setup deferred (not blocking code): installing the launchd plist via `launchctl bootstrap` and configuring Obsidian Web Clipper to write into `Inbox/`. Both documented; the user can do them when ready.

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
