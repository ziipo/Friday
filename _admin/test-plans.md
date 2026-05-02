# Friday — Test Plans

Acceptance criteria per phase, plus the eval datasets we'll build along the way.

## Phase 0 — Skeleton

**Manual smoke test:**
1. `archivebox add 'https://example.com/'` → check `archivebox-data/archive/<ts>/` for `singlefile.html` + `readability/content.html`.
2. Repeat for 2 more URLs (a long-form article and a doc that requires JS).
3. Confirm both artifacts produced for each.

## Phase 1 — Scribe Watcher

**Acceptance:** Safari → Web Clipper → `Inbox/foo.url` → archive_record exists in `Archive/records/` within 60s, with both `Archive/Rendered/` and `Archive/Clean/` artifacts.

**Test cases:**
- `.url` file
- `.md` file with frontmatter (frontmatter must be preserved)
- `.pdf` file (both raw archive + extracted text artifact)
- `.eml` file with attachments (each attachment → sub-record)

## Phase 2 — Triage

**Acceptance:** 10 hand-labeled candidates produce expected decisions.

**Eval set (build during Phase 2):**
- 3 high-relevance items (proposal, RFC, spec from a tracked entity)
- 3 medium (status updates, meeting notes)
- 2 low/spam (auto-generated reports, unrelated newsletters)
- 2 duplicates of existing records

## Phase 3 — Synthesizer

**Acceptance:** 10 promoted archive records → memory records + entity/concept pages with mutual backlinks; reconciliations land in `ReviewQueue/pending/`.

## Phase 4 — Pollers

**Acceptance:**
- Doc shared in allowlisted Slack channel archived within 15 min
- Engagement signals flow into `EngagementLog/`
- OAuth tokens persist across reboots (Keychain)

## Phase 5 — Promoter

**Acceptance:** comment on already-archived Drive doc → memory record within one Promoter cycle (~5 min).

**Test cases:**
- Engagement-triggered promotion (reaction, comment, attendance)
- Relevance-triggered promotion (Triage score ≥ 0.7, no engagement)
- Manual promotion (`@promote` tag)
- Transitive promotion (memory record cites archive record)

## Phase 6 — Janitor

**Acceptance:** after a week of real usage, Saturday review queue contains sensible flags across staleness, link rot, conflicts, demotion proposals.

## Phase 7 — Trust ratchet

**Acceptance:** at least one category graduates to auto-apply with measured ratio > 0.95 and N > 20.
