# Friday

A personal three-layer second-brain system: **Archive → Memory → Synthesis**.

Friday captures professional knowledge ambiently from Slack, Google Calendar, Google Drive, and manual web/PDF/email/markdown drops; promotes engaged-with content into a structured memory tier; and synthesizes cross-source pages over time. Backed by an Obsidian vault, ArchiveBox for web archival, Git (with LFS) for versioning, and the Anthropic API for triage and synthesis.

This is a single-user personal project. Not a product.

## Status

Pre-build — Phase 0 (Skeleton). See [`_admin/`](./_admin/) for the PRD, implementation plan, TODOs, and progress log.

## Architecture

Five components: **Scribe** (ingestion), **Triage** (quality gate), **Promoter** (engagement-driven promotion), **Synthesizer** (memory + synthesis), **Janitor** (maintenance). See [`_admin/PRD-v2.pdf`](./_admin/PRD-v2.pdf) for full specification.

## Preconditions

- macOS 14+ on Apple Silicon, **FileVault enabled** (the system reads sensitive professional content)
- Python 3.13 (managed via `uv`)
- Node.js 18+ (for ArchiveBox extractors)
- Pandoc, Git LFS

## Repository layout

```
_admin/                  # Development workspace (plan, TODOs, test plans) — not part of the second-brain
Inbox/                   # Watched folder for manual captures (gitignored)
Workspace/               # PARA-structured human workspace
Institutional-Memory/    # Memory + Synthesis tiers
Archive/                 # Archive tier (LFS-tracked HTML + Markdown)
ReviewQueue/             # Proposed changes awaiting approval
EngagementLog/           # Raw engagement signals (gitignored, ephemeral)
archivebox-data/         # ArchiveBox working dir (gitignored)
scripts/                 # Implementation code
```
