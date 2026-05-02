# Friday — Implementation Plan

Project: **Friday** — personal three-layer second-brain system.
Source of truth for requirements: [`PRD-v2.pdf`](./PRD-v2.pdf).
This plan is a delivery view; when it conflicts with the PRD, the PRD wins.

## Naming & layout adjustments to the PRD

- Repo & folder: `~/Projects/Friday/` (replaces `~/SecondBrain/` everywhere in PRD §3.2 and §5.6)
- Component names stay as written: Scribe, Triage, Promoter, Synthesizer, Janitor, Vault
- Drop the "flight recorder" framing in any user-facing docs/prompts
- `_admin/` is a top-level dev workspace, NOT part of the second-brain memory system. It is gitignored from any future content-indexing logic but is committed to the repo.

## What we vendor vs. write

| Need | Source | How |
|---|---|---|
| Web archival (HTML + Markdown) | **ArchiveBox** | `uv add archivebox`; shell out to CLI |
| Single-file HTML capture | **SingleFile** (`single-file-cli`) | npm, invoked by ArchiveBox |
| HTML→Markdown extraction | **readability-extractor** + **Pandoc** | npm + brew, invoked by ArchiveBox |
| Memory-tier folder + lint conventions | **Astro-Han/karpathy-llm-wiki** | clone, lift entity/concept/synthesis templates into `Institutional-Memory/` |
| Slash-command surface (`/query`, `/promote`, `/synthesis`) | **AgriciDaniel/claude-obsidian** | crib `.claude/commands/*.md` structure |
| File watcher | **watchdog** (pip) | standard library use |
| Slack ingestion | **slack-sdk** (pip) | official SDK |
| Google Drive/Calendar/Activity | **google-api-python-client** + **google-auth-oauthlib** | official SDK |
| PDF extraction | **pdfplumber** | pip |
| Email parsing | **mailparser** | pip |
| macOS Keychain | **keyring** | pip |
| Frontmatter | **python-frontmatter** | pip |
| LLM | **anthropic** SDK | pip; Sonnet default per PRD §9.3 |

Net code we write: the five components' orchestration + prompts + plugin contracts.

## Phase 0 — Skeleton (Week 1)

1. Move PRD into `_admin/`. ✓
2. Init `~/Projects/Friday/` as git repo; create public `ziipo/Friday` on GitHub.
3. Scaffold v2 folder layout from PRD §3.2.
4. Vendor templates from `Astro-Han/karpathy-llm-wiki` and `AgriciDaniel/claude-obsidian`.
5. `uv init` with Python 3.13; pin PRD §11 deps.
6. `git lfs track "*.html" "*.pdf"`.
7. Install ArchiveBox + SingleFile + Readability extractor; init `archivebox-data/`.
8. Smoke test: `archivebox add` 3 URLs, confirm artifacts in `Archive/Rendered/` and `Archive/Clean/`.

**Acceptance:** manual `archivebox add` produces an archive_record + both artifacts.

## Phase 1 — Scribe Watcher MVP (Week 2)

1. `scripts/scribe/watcher.py` using `watchdog`, dispatching by file extension.
2. Ingestors: `web.py` (URL → ArchiveBox), `markdown.py`, `pdf.py` (pdfplumber + raw archive), `email.py` (mailparser, attachments → sub-records).
3. `SourcePlugin` Protocol per PRD §5.1.2, Watcher mode only.
4. Archive record writer: emits `Archive/records/arc_*.md` per §4.1.
5. launchd plist at `~/Library/LaunchAgents/com.friday.scribe.plist`.
6. Configure Obsidian Web Clipper to write `.url` files into `Inbox/`.

**Acceptance:** Safari clip → archive_record within 60s.

## Phase 2 — Triage (Week 3)

1. `scripts/triage/scorer.py` — single Sonnet call returning the JSON schema in §5.2.2.
2. Prompt loads compact Memory-tier context (entities, concepts, recent titles); cap ~2k tokens.
3. Decision matrix from §5.2.3 as pure Python.
4. `Institutional-Memory/.reputation.json` read-only at first.
5. Eval harness: 10 hand-labeled items.

**Acceptance:** 10 varied items produce sensible decisions.

## Phase 3 — Synthesizer & Review Queue (Week 4)

1. Prompts: `prompts/synthesize_source.md`, `extract_entities.md`, `extract_concepts.md`, `reconcile.md` — adapted from karpathy-llm-wiki lint conventions.
2. `scripts/synthesizer/synthesize.py`: archive record → memory record per §4.2 + entity/concept pages.
3. Reconciliation proposals to `ReviewQueue/pending/{ts}_{slug}.md` per §5.4.3.
4. Claude Code slash command: `.claude/commands/synthesize.md`.
5. Batch script via Anthropic API.

**Acceptance:** 10 promoted records → coherent memory + entities + concepts with backlinks.

## Phase 4 — Pollers (Weeks 5–6)

1. Extend `SourcePlugin` Protocol with `poll()` and `collect_engagement_signals()`.
2. OAuth helpers in `scripts/auth/` using `keyring`; one-time browser-flow scripts per service.
3. **Calendar poller** — events ±7d, attached docs, RSVP tracking.
4. **Drive poller** — designated folder + recent/starred/shared-direct files; Drive Activity API for engagement.
5. **Slack poller** — DMs + allowlisted channels + @-mention threads.
6. Each poller as its own launchd agent on its schedule per §5.1.3.
7. EngagementLog writer: `EngagementLog/{date}.jsonl` append-only.

**Acceptance:** doc shared in allowlisted Slack channel archived within 15 min; engagement signals flow.

## Phase 5 — Promoter (Week 7)

1. `scripts/promoter/promoter.py` — tails EngagementLog, applies §5.3.1 trigger logic.
2. On promotion: invoke Synthesizer; update `archive_record.status = promoted` + `promoted_to`.
3. Engagement field tagging: passing/reviewed/studied per §5.3.4.
4. `@promote` tag handler + `promote arc_xxx` Claude Code command.
5. launchd agent every ~5 min.

**Acceptance:** comment on already-archived Drive doc → memory record within one cycle.

## Phase 6 — Janitor (Week 8)

1. `scripts/janitor/nightly.py` — re-capture, diff & LLM-classify, staleness, link rot, conflict detection, reputation update, index rebuild. 02:00 local.
2. `scripts/janitor/weekly.py` — demotion proposals, archive pruning, trust-ratchet recompute. Sun 03:00.
3. Reputation writer activates here (Phase 2 was read-only).
4. launchd agents; commit + push after each sweep.

**Acceptance:** after a week of usage, Saturday review queue populated with sensible flags.

## Phase 7 — Trust ratchet (Week 9+)

1. `Institutional-Memory/.trust-stats.json` tracks `proposed` vs `user_kept` per category.
2. Per-category auto-apply once ratio > 0.95 with N>20 over 30d.
3. `--review-all` paranoid mode.
4. Tune §9 open questions (relevance threshold, view-duration cutoff, demotion sensitivity) with real data.

## Cross-cutting concerns

- **Privacy** (PRD §6): Anthropic zero-retention enabled in client config; FileVault precondition documented in `README.md`; per-service OAuth apps.
- **Vault** (§5.6): commit-after-each-ingest hook in Scribe; weekly tag at Fri 17:00 via launchd.
- **Config**: `scripts/config/*.yaml` (committed), `scripts/config/*.local.yaml` (gitignored).
- **Logging**: `Institutional-Memory/log.md` for human-readable, `.logs/{component}.jsonl` for tooling.
