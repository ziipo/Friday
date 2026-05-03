"""Upsert entity and concept pages in Institutional-Memory/.

If a page with a matching title (or alias) already exists, append the new
source to its `sources:` list and refresh `updated`. Otherwise create a new
page from the template. Backlinks: the source ID is added to the entity/concept
`sources:` list; conversely, the memory record's `relations:` already cite the
entity/concept by title.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import frontmatter
import yaml

from lib import paths

from .models import ExtractedConcept, ExtractedEntity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(title: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "_" for c in title).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:60] or "untitled"


def _index_existing(directory: Path) -> dict[str, Path]:
    """Map lowercase title (and aliases) → existing file path."""
    if not directory.exists():
        return {}
    out: dict[str, Path] = {}
    for path in directory.glob("*.md"):
        try:
            post = frontmatter.load(path)
        except Exception:
            continue
        title = str(post.metadata.get("title") or path.stem).strip()
        if title:
            out.setdefault(title.lower(), path)
        for alias in post.metadata.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                out.setdefault(alias.lower().strip(), path)
    return out


def _append_source(post: frontmatter.Post, src_id: str) -> bool:
    """Add src_id to the `sources` list if not present. Returns True if changed."""
    sources = list(post.metadata.get("sources") or [])
    if src_id in sources:
        return False
    sources.append(src_id)
    post.metadata["sources"] = sources
    post.metadata["updated"] = _now()
    return True


def _merge_aliases(post: frontmatter.Post, new_aliases: Iterable[str]) -> bool:
    existing = list(post.metadata.get("aliases") or [])
    existing_lc = {a.lower() for a in existing if isinstance(a, str)}
    changed = False
    for a in new_aliases:
        if a and a.lower() not in existing_lc:
            existing.append(a)
            existing_lc.add(a.lower())
            changed = True
    if changed:
        post.metadata["aliases"] = existing
    return changed


def _write_post(path: Path, post: frontmatter.Post) -> None:
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def upsert_entity(entity: ExtractedEntity, *, src_id: str) -> tuple[Path, bool]:
    """Create or update an entity page. Returns (path, created)."""
    directory = paths.INSTITUTIONAL_MEMORY / "entities"
    directory.mkdir(parents=True, exist_ok=True)
    index = _index_existing(directory)

    key = entity.title.lower()
    existing = index.get(key)
    for alias in entity.aliases:
        if not existing:
            existing = index.get(alias.lower())

    if existing:
        post = frontmatter.load(existing)
        changed = _append_source(post, src_id)
        changed |= _merge_aliases(post, entity.aliases)
        if changed:
            _write_post(existing, post)
        return existing, False

    metadata: dict[str, Any] = {
        "type": "entity",
        "entity_type": entity.entity_type,
        "title": entity.title,
        "aliases": list(entity.aliases),
        "first_mentioned": src_id,
        "created": _now(),
        "updated": _now(),
        "status": "active",
        "relations": [],
        "sources": [src_id],
        "tags": ["entity", entity.entity_type] if entity.entity_type != "other" else ["entity"],
    }
    body_lines = [
        f"# {entity.title}",
        "",
        "## Overview",
        "",
        entity.summary or f"[{entity.title}]",
        "",
        "## Key facts",
        "",
        "-",
        "",
        "## Connections",
        "",
        "-",
        "",
        "## Sources",
        "",
        f"- [[{src_id}]]",
        "",
    ]
    target = directory / f"{_slug(entity.title)}.md"
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    target.write_text(f"---\n{yaml_text}---\n\n" + "\n".join(body_lines), encoding="utf-8")
    return target, True


def upsert_concept(concept: ExtractedConcept, *, src_id: str) -> tuple[Path, bool]:
    directory = paths.INSTITUTIONAL_MEMORY / "concepts"
    directory.mkdir(parents=True, exist_ok=True)
    index = _index_existing(directory)

    existing = index.get(concept.title.lower())
    for alias in concept.aliases:
        if not existing:
            existing = index.get(alias.lower())

    if existing:
        post = frontmatter.load(existing)
        changed = _append_source(post, src_id)
        changed |= _merge_aliases(post, concept.aliases)
        if changed:
            _write_post(existing, post)
        return existing, False

    metadata: dict[str, Any] = {
        "type": "concept",
        "title": concept.title,
        "domain": concept.domain,
        "aliases": list(concept.aliases),
        "created": _now(),
        "updated": _now(),
        "status": "active",
        "relations": [],
        "sources": [src_id],
        "tags": ["concept"],
    }
    body_lines = [
        f"# {concept.title}",
        "",
        "## Definition",
        "",
        concept.summary or f"[{concept.title}]",
        "",
        "## How it works",
        "",
        "-",
        "",
        "## Why it matters",
        "",
        "-",
        "",
        "## Examples",
        "",
        "-",
        "",
        "## Connections",
        "",
        "-",
        "",
        "## Sources",
        "",
        f"- [[{src_id}]]",
        "",
    ]
    target = directory / f"{_slug(concept.title)}.md"
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    target.write_text(f"---\n{yaml_text}---\n\n" + "\n".join(body_lines), encoding="utf-8")
    return target, True
