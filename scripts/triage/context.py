"""Compact memory-tier context for the triage prompt.

Reads entity, concept, and source titles+summaries from Institutional-Memory/.
Renders a budgeted plain-text view, cap ~2000 tokens (≈8000 chars at the
"4 chars per token" heuristic). Truncate longest section first if over budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter

from lib import paths

CHAR_BUDGET = 8000
RECENT_SOURCES = 30


@dataclass
class MemorySnapshot:
    entities: list[str]
    concepts: list[str]
    recent_sources: list[tuple[str, str, str]]  # (id, title, summary)

    def is_empty(self) -> bool:
        return not (self.entities or self.concepts or self.recent_sources)


def _read_titles(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    out: list[str] = []
    for path in sorted(directory.glob("*.md")):
        try:
            post = frontmatter.load(path)
            title = post.metadata.get("title") or path.stem
            if title:
                out.append(str(title))
        except Exception:
            out.append(path.stem)
    return out


def _read_recent_sources(limit: int) -> list[tuple[str, str, str]]:
    src_dir = paths.INSTITUTIONAL_MEMORY / "sources"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.glob("*.md"), reverse=True)[:limit]
    out: list[tuple[str, str, str]] = []
    for path in files:
        try:
            post = frontmatter.load(path)
            src_id = path.stem
            title = str(post.metadata.get("title") or src_id)
            summary = str(post.metadata.get("summary") or "").strip().split("\n", 1)[0]
            out.append((src_id, title, summary))
        except Exception:
            continue
    return out


def load_snapshot() -> MemorySnapshot:
    return MemorySnapshot(
        entities=_read_titles(paths.INSTITUTIONAL_MEMORY / "entities"),
        concepts=_read_titles(paths.INSTITUTIONAL_MEMORY / "concepts"),
        recent_sources=_read_recent_sources(RECENT_SOURCES),
    )


def render(snapshot: MemorySnapshot, budget: int = CHAR_BUDGET) -> str:
    if snapshot.is_empty():
        return "(memory tier is empty — cold start)"

    sections: list[tuple[str, list[str]]] = []
    if snapshot.entities:
        sections.append(("Entities", snapshot.entities))
    if snapshot.concepts:
        sections.append(("Concepts", snapshot.concepts))
    if snapshot.recent_sources:
        sources_lines = [
            f"- {src_id}: {title}" + (f" — {summary}" if summary else "")
            for src_id, title, summary in snapshot.recent_sources
        ]
        sections.append(("Recent sources", sources_lines))

    # Render; if over budget, trim the longest section in half repeatedly.
    while True:
        rendered = "\n\n".join(
            f"## {name}\n" + "\n".join(f"- {item}" if not item.startswith("-") else item for item in items)
            for name, items in sections
        )
        if len(rendered) <= budget:
            return rendered
        # Trim longest section
        idx = max(range(len(sections)), key=lambda i: sum(len(x) for x in sections[i][1]))
        items = sections[idx][1]
        if len(items) <= 1:
            # Can't trim further; truncate raw output
            return rendered[:budget] + "\n…(truncated)"
        sections[idx] = (sections[idx][0], items[: max(1, len(items) // 2)])
