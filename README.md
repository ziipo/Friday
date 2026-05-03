# Friday

A personal three-layer second-brain system: **Archive → Memory → Synthesis**.

Friday captures professional knowledge ambiently from Slack, Google Calendar, Google Drive, and manual web/PDF/email/markdown drops; promotes engaged-with content into a structured memory tier; and synthesizes cross-source pages over time. Backed by an Obsidian-compatible Markdown vault, ArchiveBox for web archival, Git (with LFS) for versioning, and the Anthropic API for triage and synthesis.

This is a single-user personal project on macOS. Not a product.

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Credentials setup](#credentials-setup)
5. [Starting the agents](#starting-the-agents)
6. [Day-to-day use](#day-to-day-use)
7. [Reviewing proposals](#reviewing-proposals)
8. [Tuning thresholds](#tuning-thresholds)
9. [Manual operations](#manual-operations)
10. [Repository layout](#repository-layout)
11. [Limitations](#limitations)
12. [Future expansion](#future-expansion)

---

## Architecture overview

```
Sources                  Scribe               Archive tier
─────────────────────    ────────────────     ─────────────────────────────
Manual drop (Inbox/)  →  Ingestor             Archive/records/{arc_id}.md
Slack poller          →  ↓                    Archive/Rendered/{arc_id}/   (HTML)
Google Calendar       →  Triage               Archive/Clean/{arc_id}/      (text)
Google Drive          →  ↓
                         archive-only
                         │
                         fast-track ──────→  Promoter
                                             │
                               engagement    │  relevance ≥ 0.7
                               signals ──────┤
                                             ↓
                                         Synthesizer
                                             ↓
                                         Memory tier
                                         Institutional-Memory/
                                           sources/     (promoted notes)
                                           entities/    (people, orgs, tools)
                                           concepts/    (ideas, frameworks)
                                           synthesis/   (cross-source analysis)
                                             ↓
                                         Janitor
                                           nightly: re-capture, staleness,
                                                    link rot, conflicts,
                                                    reputation, index
                                           weekly:  demotion proposals,
                                                    prune proposals,
                                                    trust ratchet
```

**Five components:**

| Component | Purpose | Schedule |
|---|---|---|
| **Scribe** | Watches `Inbox/`, runs pollers, ingests files into Archive | Continuous + polling cadence |
| **Triage** | Scores each capture for relevance; decides discard / archive-only / fast-track | Inline (per capture) |
| **Promoter** | Moves fast-tracked and engaged-with archive records into Memory | Every 5 min |
| **Synthesizer** | Writes structured memory pages, extracts entities/concepts, flags conflicts | Triggered by Promoter |
| **Janitor** | Nightly hygiene + weekly demotion/pruning + trust ratchet | 02:00 nightly, Sunday 03:00 weekly |

**Trust ratchet** (Sunday 03:30): after the weekly sweep evaluates proposal acceptance ratios, the trust ratchet auto-promotes proposals in categories where the user has accepted ≥ 95% of suggestions over the last 30 days with ≥ 20 samples. Everything else stays in `ReviewQueue/pending/` for human review.

---

## Prerequisites

- **macOS 14+ on Apple Silicon** — FileVault should be enabled (the system reads and stores sensitive professional content)
- **Python 3.13** via [`uv`](https://docs.astral.sh/uv/)
- **Node.js 18+** — required by ArchiveBox's SingleFile and Readability extractors
- **Python 3.11** — required *only* for ArchiveBox (it imports `distutils`, removed in Python 3.12+)
- **Git + Git LFS** — `brew install git-lfs && git lfs install`
- **ArchiveBox** — installed as a separate uv tool under Python 3.11 (see below)
- An **Anthropic API key** (Claude Sonnet for triage and synthesis)
- Optional: **Google Cloud project** with Calendar and Drive APIs enabled (for pollers)
- Optional: **Slack app** with the required OAuth scopes (for the Slack poller)

---

## Installation

### 1. Clone and set up Python environment

```sh
git clone https://github.com/ziipo/Friday ~/Projects/Friday
cd ~/Projects/Friday
git lfs pull          # fetch any LFS-tracked archive artifacts
uv sync               # creates .venv with all Python 3.13 deps
```

### 2. Install ArchiveBox (Python 3.11)

ArchiveBox must run under its own Python 3.11 environment. Install it as a uv tool:

```sh
uv tool install --python 3.11 archivebox
```

Then initialise its working directory:

```sh
cd ~/Projects/Friday
archivebox init
```

ArchiveBox is configured to run only **SingleFile** and **Readability** extractors (HTML snapshot + reader-mode text). Heavier extractors (wget, warc, screenshot) are disabled in `archivebox-data/ArchiveBox.conf`.

### 3. Install Node.js extractors

```sh
npm install -g single-file-cli readability-extractor
```

### 4. Verify Python path

All Friday scripts use `PYTHONPATH=scripts`. The launchd plists set this automatically. For manual runs, set it in your shell:

```sh
export PYTHONPATH=~/Projects/Friday/scripts
```

Or prefix every `uv run` command:

```sh
PYTHONPATH=scripts uv run python -m scribe.watcher --once
```

---

## Credentials setup

Run the interactive setup wizard — it stores everything in macOS Keychain, nothing is written to disk:

```sh
PYTHONPATH=scripts uv run python scripts/setup_secrets.py
```

This will prompt for:
- **Anthropic API key** (required — triage and synthesis don't work without it)
- **Google OAuth client credentials** (optional — Calendar + Drive pollers)
- **Slack bot and user tokens** (optional — Slack poller)
- **OpenRouter API key** (optional — used as LLM fallback if Anthropic is unavailable)

### Google OAuth (Calendar + Drive)

After running `setup_secrets.py` with Google credentials, run the one-time OAuth flows:

```sh
PYTHONPATH=scripts uv run python -m auth.google_calendar
PYTHONPATH=scripts uv run python -m auth.google_drive
```

Each opens a browser window. After authorising, the refresh token is stored in Keychain. You will not need to re-authorise unless you revoke access in Google's security settings.

### Slack

```sh
PYTHONPATH=scripts uv run python -m auth.slack
```

Verifies the bot token and stores it. The Slack poller needs both a bot token (`xoxb-`) and a user token (`xoxp-`) — the latter is required for `search.messages` (fetching @-mention threads). Both are prompted by `setup_secrets.py`.

### Per-machine config (gitignored)

Create `scripts/config/pollers.local.yaml` to override defaults without touching committed files:

```yaml
drive:
  designated_folder_id: "1AbCdEfGhIjKlMnOpQrStUv"   # your Friday input folder in Drive

slack:
  channel_allowlist:
    - "C01234ABCDE"   # channel IDs to monitor beyond direct messages
```

To find a Slack channel ID: open the channel in Slack → channel name → About → copy the ID at the bottom.

---

## Starting the agents

Install launchd plists to run everything automatically:

```sh
LAUNCH_AGENTS=~/Library/LaunchAgents
PLIST_DIR=~/Projects/Friday/scripts/launchd

for plist in \
  com.friday.scribe.plist \
  com.friday.poller.calendar.plist \
  com.friday.poller.drive.plist \
  com.friday.poller.slack.plist \
  com.friday.promoter.plist \
  com.friday.janitor.nightly.plist \
  com.friday.janitor.weekly.plist \
  com.friday.trust_ratchet.plist \
  com.friday.vault.weekly_tag.plist; do
    ln -sfn "$PLIST_DIR/$plist" "$LAUNCH_AGENTS/$plist"
    launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENTS/$plist"
done
```

**Install pollers only after completing OAuth setup** — they will exit on every cycle with a missing-credentials error otherwise.

### Check agent status

```sh
# Running status
launchctl print gui/$(id -u)/com.friday.scribe

# Live logs
tail -f ~/Projects/Friday/.logs/scribe.watcher.stderr.log
tail -f ~/Projects/Friday/.logs/poller.slack.stderr.log

# Structured event log (JSONL)
tail -f ~/Projects/Friday/.logs/scribe.pipeline.jsonl | python -m json.tool
```

### Uninstall an agent

```sh
launchctl bootout gui/$(id -u)/com.friday.scribe
rm ~/Library/LaunchAgents/com.friday.scribe.plist
```

---

## Day-to-day use

### Manual capture (Inbox drop)

Drop any of these file types into `~/Projects/Friday/Inbox/`:

| Extension | What happens |
|---|---|
| `.url` | ArchiveBox fetches the URL; stores SingleFile HTML + Readability text |
| `.md` / `.markdown` | Frontmatter is preserved; content is archived as-is |
| `.pdf` | pdfplumber extracts text; original PDF stored in Archive |
| `.eml` | Email is parsed; attachments become separate archive records |

The Scribe watcher (running continuously) picks up the file within seconds, runs it through the triage pipeline, and moves it to `Inbox/processed/` on success or `Inbox/failed/` (with an `.error.log`) on failure.

**Browser integration:** configure [Obsidian Web Clipper](https://obsidian.md/clipper) or [MarkDownload](https://github.com/deathau/markdownload) to save `.url` files directly to `Inbox/` for frictionless web capture.

### Ambient capture (pollers)

Once running, the pollers harvest content automatically:

- **Calendar** (every 60 min): events ±7 days, attached Google Docs → web ingestor, attendance signals
- **Drive** (every 30 min): designated folder + recently modified/starred files, Workspace exports (Doc/Sheet/Slide → PDF + text)
- **Slack** (every 15 min): all DMs + allowlisted channels, embedded URLs → web ingestor, file downloads

You do not need to do anything for ambient capture to work once credentials are configured.

### Checking what was captured

```sh
ls ~/Projects/Friday/Archive/records/ | tail -20
cat ~/Projects/Friday/Institutional-Memory/index.md
cat ~/Projects/Friday/Institutional-Memory/log.md
```

### Vault (git history)

Every successful ingest creates a git commit (`ingest: {filename} [{timestamp}]`). You can browse the full capture history:

```sh
git log --oneline | head -20
git show HEAD                # latest ingest commit
```

Every Friday at 17:00, a weekly tag is created: `git tag weekly-2026-W19`. These tags make it easy to compare the knowledge base week-over-week.

---

## Reviewing proposals

The Janitor and Synthesizer write proposal files to `ReviewQueue/pending/` when they detect issues or want to suggest changes. Review them periodically (the weekly sweep produces most of them).

### Types of proposals

| Type | Created by | What it means |
|---|---|---|
| `staleness` | Janitor nightly | A non-web source hasn't been verified in >30 days |
| `dead-link` | Janitor nightly | A URL has returned 4xx/5xx for >7 consecutive days |
| `conflict` | Janitor nightly | LLM detected a factual contradiction with an existing source |
| `recapture` | Janitor nightly | A re-fetched web page changed significantly (notable or breaking change) |
| `demotion` | Janitor weekly | A memory record is >90 days old, unlinked, and never synthesised |
| `prune` | Janitor weekly | An archive record is >365 days old, never promoted, relevance <0.3 |
| `reconcile` | Synthesizer | A newly promoted source contradicts or supersedes an existing one |

### Accepting a proposal

Move it to `ReviewQueue/approved/`:

```sh
mv ReviewQueue/pending/2026-05-03T02-00-00Z_stale_foo.md ReviewQueue/approved/
```

The Janitor's next nightly run will act on all files in `approved/`.

### Rejecting a proposal

Delete it. No action is taken.

### Trust ratchet

Once you have accepted ≥ 20 proposals of a given type with a ≥ 95% acceptance rate over the last 30 days, that proposal type *graduates*. From that point the trust ratchet (Sunday 03:30) automatically moves new proposals of that type from `pending/` to `approved/` — you only see the unusual ones.

Check graduation status:

```sh
PYTHONPATH=scripts uv run python -m trust_ratchet.apply --status
```

Force paranoid mode for a given sweep (leave everything in pending regardless of graduation):

```sh
PYTHONPATH=scripts uv run python -m trust_ratchet.apply --review-all
```

---

## Tuning thresholds

All configurable parameters live in `scripts/config/tuning.yaml`. Change them without touching code. Override on a per-machine basis with `scripts/config/tuning.local.yaml` (gitignored, deep-merged at runtime):

```yaml
# tuning.local.yaml example — lower the relevance floor during initial calibration
triage:
  high_floor: 0.6        # was 0.7 — promote more aggressively while getting started
  low_floor: 0.15        # was 0.2 — discard less

janitor:
  stale_days: 60         # was 30 — tolerate older sources before flagging

weekly:
  demotion_age_days: 180 # was 90 — be more conservative about demotion proposals
```

Key parameters and their effect:

| Section | Key | Default | Effect |
|---|---|---|---|
| `triage` | `high_floor` | `0.7` | Relevance score at or above which a capture is fast-tracked to Memory |
| `triage` | `low_floor` | `0.2` | Relevance score below which a capture is discarded |
| `janitor` | `stale_days` | `30` | Days since last verification before a non-web source is flagged stale |
| `janitor` | `dead_link_grace_days` | `7` | Days a URL must fail before being marked dead-link |
| `janitor` | `conflict_lookback_days` | `3` | Window (days) for sources scanned in each conflict-detection run |
| `weekly` | `demotion_age_days` | `90` | Minimum age (days) for a demotion proposal to be generated |
| `weekly` | `prune_age_days` | `365` | Minimum archive age (days) for a pruning proposal |
| `weekly` | `prune_max_relevance` | `0.3` | Relevance ceiling for pruning candidates |
| `trust_ratchet` | `auto_apply_threshold` | `0.95` | Minimum kept/proposed ratio for a category to graduate |
| `trust_ratchet` | `min_samples` | `20` | Minimum sample count required before graduation |

---

## Manual operations

### Force a triage run on a file

```sh
PYTHONPATH=scripts uv run python -m scribe.watcher --once
```

Processes everything currently sitting in `Inbox/` and exits.

### Run the nightly sweep now

```sh
PYTHONPATH=scripts uv run python -m janitor.nightly
# or dry-run to see what it would do:
PYTHONPATH=scripts uv run python -m janitor.nightly --dry-run
```

### Run the weekly sweep now

```sh
PYTHONPATH=scripts uv run python -m janitor.weekly --dry-run
```

### Manually promote an archive record

```sh
PYTHONPATH=scripts uv run python -m synthesizer.synthesize arc_2026-05-03T14-22-32Z_ac4c
```

### Rebuild the Memory index

```sh
PYTHONPATH=scripts uv run python -c "
from janitor.index import rebuild_index
rebuild_index()
"
```

### Run all tests

```sh
PYTHONPATH=scripts uv run pytest scripts/tests/ -v
```

---

## Repository layout

```
_admin/                          Dev workspace (PRD, plan, TODOs) — not part of the second-brain
Archive/
  records/                       Archive record frontmatter files (.md), one per captured item
  Rendered/                      Raw capture artifacts (HTML) — Git LFS tracked
  Clean/                         Processed text artifacts — Git LFS tracked
archivebox-data/                 ArchiveBox working directory — gitignored
EngagementLog/                   Raw engagement signals from pollers — gitignored (ephemeral)
Inbox/                           Drop folder for manual captures — gitignored
Institutional-Memory/
  _templates/                    Frontmatter templates for new record types
  sources/                       Promoted memory records
  entities/                      People, organisations, products, tools
  concepts/                      Ideas, frameworks, theories
  synthesis/                     Cross-source analysis pages
  index.md                       Master catalog (regenerated nightly)
  log.md                         Operations log (appended by each sweep)
prompts/                         LLM prompt templates (triage.md, synthesize.md)
ReviewQueue/
  pending/                       Proposals awaiting human review (gitignored)
  approved/                      Accepted proposals, acted on by next nightly sweep
scripts/
  auth/                          One-time OAuth flows (calendar, drive, slack)
  config/
    tuning.yaml                  All configurable thresholds (edit here to tune)
    pollers.yaml                 Poller defaults (lookback windows, size buckets)
  janitor/                       Nightly + weekly maintenance sweeps
  launchd/                       macOS launchd plists for all background agents
  lib/                           Shared utilities (paths, llm, logging, engagement, etc.)
  promoter/                      Engagement-driven archive→memory promoter
  scribe/                        Inbox watcher + pollers + ingestors
  synthesizer/                   Memory record writer + entity/concept upsert
  tests/                         Pytest suite (57 tests)
  triage/                        LLM scorer + decision matrix
  trust_ratchet/                 Auto-apply graduated ReviewQueue proposals
  setup_secrets.py               Interactive Keychain credential setup
Workspace/                       PARA-structured human workspace (Projects/Areas/Resources/Archive)
```

---

## Limitations

### What Friday does not do

- **No search UI.** Friday writes to Markdown files. Read them in Obsidian, VS Code, or any text editor. There is no query interface — use `grep`, Obsidian search, or `fzf`.
- **No real-time synthesis.** Synthesis runs on-demand (`python -m synthesizer.synthesize`) or when the Promoter triggers it. There is no streaming or live-update UI.
- **No mobile capture.** The Inbox drop and pollers are macOS-only. Mobile capture requires a workaround (e.g., a Shortcut that saves to an iCloud-synced `Inbox/`).
- **No automatic de-duplication across pollers.** If the same URL appears in a Slack message, a Drive document, and a manual drop, you will get three archive records pointing at the same content. The triage `duplicate_of` field handles some of this; exact-URL deduplication is left to the Janitor.
- **No multi-user support.** Credentials, paths, and the Keychain integration are single-user. All paths are hardcoded to `~/Projects/Friday`.

### Cost

Every capture that reaches the triage stage makes one LLM call (Claude Sonnet). Every promotion makes a second call (Synthesizer). At moderate capture volume (50–100 items/day), expect roughly $2–5/month in Anthropic API costs. The nightly conflict-detection scan scales with Memory tier size.

### ArchiveBox

- SingleFile extraction fails on some sites that block headless browsers (e.g., paywalled content, Cloudflare-protected pages).
- Wikipedia archival fails on this machine (chromium sandbox issue on macOS with arm64). See `_admin/TODO.md` for details.
- ArchiveBox must be run under Python 3.11. It cannot be updated to a newer Python without the upstream project removing its `distutils` dependency.

### LLM reliability

- Triage scoring is probabilistic. Occasionally relevant items are discarded (false negatives) and irrelevant items are archived (false positives). The `tuning.yaml` thresholds let you adjust the tradeoff.
- Conflict detection produces false positives on related-but-not-contradictory sources. The proposal review step exists for exactly this reason.
- Synthesis quality depends on source quality. Short, low-context captures produce sparse memory records.

### Pollers

- The Google Drive Activity API does not expose view duration. The `drive_view_duration_seconds` threshold in `tuning.yaml` is a placeholder for a future API that exposes this.
- The Slack `search.messages` endpoint requires a user token (`xoxp-`), which is higher-privilege than a bot token. This means revoking the user token in Slack breaks @-mention tracking.
- Calendar invitations where you are a room/resource (not a person) may produce spurious attendance signals.

---

## Future expansion

The architecture is designed around clearly separated phases. These are the most natural next steps:

### Near-term

- **Synthesis trigger improvements** — currently synthesis is triggered by the Promoter at promotion time. A separate periodic re-synthesis pass could update existing memory records when new related sources arrive.
- **Manual `@promote` tag** — any Inbox markdown file with `tags: [promote]` in its frontmatter could be unconditionally fast-tracked, bypassing the relevance gate entirely.
- **Obsidian plugin** — the ReviewQueue and Inbox drop could be exposed via a custom Obsidian plugin for a cleaner UI layer over the Markdown files.
- **iOS/iPadOS capture** — a Shortcut that writes `.url` files to an iCloud-synced directory linked to `Inbox/` would close the mobile gap.

### Medium-term

- **Email poller** — a Gmail poller (using the Gmail API with an allowlisted `from:` filter) is the natural companion to the Slack and Drive pollers for professional inboxes.
- **Synthesis templates** — the current synthesizer writes a single memory record per source. Templated synthesis (e.g., "project brief" template, "person profile" template) would produce richer structured output for specific entity types.
- **Slack digest mode** — instead of per-message capture, a thread-digest mode would produce one archive record per Slack thread, reducing noise for high-volume channels.
- **Query interface** — a lightweight `python -m friday.query` CLI backed by a local embedding index (e.g., `chromadb` or `faiss`) would enable semantic search over the Memory tier without a full UI.

### Long-term

- **Calendar-driven synthesis** — after a meeting, the Synthesizer could automatically generate a meeting-notes stub by joining the calendar event with any related memory records (attendees, linked docs, past context).
- **Weekly briefing** — a Sunday synthesis job that composes a "what I learned this week" digest from the week's fast-tracked promotions, surfaced as a new synthesis page.
- **Annotation capture** — browser annotation tools (Hypothes.is, Readwise highlights) write structured annotations that could feed a dedicated annotation ingestor, producing richer context in archive records.
- **Reputation bootstrapping** — the current cold-start reputation is uniform 0.5 for all channels and senders. A bootstrapping step that imports your Slack emoji usage history or Drive access patterns could seed more realistic initial reputations.
