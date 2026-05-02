# Vendored Sources

Upstream code/templates that informed this repo, with attribution. License compliance is best-effort for this personal project.

## Vendored

### `Astro-Han/karpathy-llm-wiki`
- URL: https://github.com/Astro-Han/karpathy-llm-wiki
- License: see upstream `LICENSE`
- What we took: structural inspiration for `Institutional-Memory/` layout (article/raw/index/archive template patterns, lint workflow). Templates were rewritten to match Friday's PRD §4.2 frontmatter schema rather than copied verbatim.
- Where it lives: `Institutional-Memory/_templates/{source,entity,concept,synthesis}.md`
- Vendored on: 2026-05-02

### `AgriciDaniel/claude-obsidian`
- URL: https://github.com/AgriciDaniel/claude-obsidian
- License: see upstream `LICENSE`
- What we took: structural inspiration for `_templates/{entity,concept,source,comparison,question}.md` body sections, and slash-command structure (`/wiki`, `/save`, `/autoresearch`). Frontmatter rewritten — upstream uses Obsidian Templater syntax (`<% tp.* %>`); we use plain `{TIMESTAMP}` placeholders so non-Obsidian tools (CLI, launchd) can fill them.
- Where it lives: `Institutional-Memory/_templates/` and `.claude/commands/{query,promote,synthesis}.md`
- Vendored on: 2026-05-02

## Notes

Friday-specific slash commands (`/query`, `/promote`, `/synthesis`) were written from scratch to match PRD §8.3 and §5.3. The upstream `/wiki`, `/save`, `/autoresearch` commands were not adopted — Friday's automated ingestion pipeline replaces the manual `/save` workflow, and `/autoresearch` is out of scope for v1.
