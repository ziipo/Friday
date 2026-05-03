# Friday — Progress Log

Reverse-chronological. One entry per session or major milestone.

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
