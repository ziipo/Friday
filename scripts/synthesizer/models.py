"""Typed view of the Synthesizer LLM output.

The prompt asks for a single JSON object covering memory record fields plus
entity/concept extraction and reconciliation flags. This module parses and
validates that JSON, with permissive defaults so a slightly malformed response
still produces a usable record.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

VALID_RELATIONS = {
    "elaborates_on", "contradicts", "supersedes",
    "is_source_for", "mentions", "depends_on",
}
VALID_ENTITY_TYPES = {"person", "organization", "product", "tool", "other"}
VALID_RECON_KINDS = {"contradicts", "supersedes"}


@dataclass
class ExtractedEntity:
    title: str
    entity_type: str = "other"
    aliases: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class ExtractedConcept:
    title: str
    domain: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class Relation:
    type: str
    target: str
    rationale: str = ""


@dataclass
class ReconciliationFlag:
    target: str
    kind: str
    summary: str


@dataclass
class Synthesis:
    title: str
    summary: str
    long_summary: str
    key_points: list[str]
    notes: str
    tags: list[str]
    entities: list[ExtractedEntity]
    concepts: list[ExtractedConcept]
    relations: list[Relation]
    reconciliation: list[ReconciliationFlag]


def _clean_target(value: Any) -> str:
    """A relation/reconciliation `target` must be a bare title or ID.

    The prompt asks for this, but defense in depth: strip any descriptive
    suffix the LLM may append. We split on em-dash variants and the first
    newline, and reject anything containing a colon (which would mean the
    LLM embedded a "Title: summary" pair)."""
    s = str(value or "").strip()
    if not s:
        return ""
    # Strip prose appended after an em-dash or newline. If any of these fire,
    # we trust what's to the left and don't second-guess colons in the title.
    trimmed = False
    for sep in (" — ", " – ", " - ", "\n"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            trimmed = True
            break
    # Only fall back to colon-stripping when no em-dash was found AND the
    # right side looks like prose (contains a space) — leaves "Title: Subtitle"
    # alone but kills "Title: one-sentence summary of the source".
    if not trimmed and ":" in s and not s.startswith(("src_", "arc_")):
        head, tail = s.split(":", 1)
        tail_stripped = tail.strip()
        if " " in tail_stripped and len(tail_stripped) > 30:
            s = head.strip()
    return s


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    fence = JSON_FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in: {raw[:200]!r}")
    return json.loads(raw[start : end + 1])


def from_llm_response(raw: str, *, fallback_title: str = "") -> Synthesis:
    data = _parse_json(raw)

    entities = []
    for e in data.get("entities") or []:
        if not isinstance(e, dict) or not e.get("title"):
            continue
        et = (e.get("entity_type") or "other").lower()
        if et not in VALID_ENTITY_TYPES:
            et = "other"
        entities.append(ExtractedEntity(
            title=str(e["title"]).strip(),
            entity_type=et,
            aliases=_coerce_str_list(e.get("aliases")),
            summary=str(e.get("summary") or "").strip(),
        ))

    concepts = []
    for c in data.get("concepts") or []:
        if not isinstance(c, dict) or not c.get("title"):
            continue
        concepts.append(ExtractedConcept(
            title=str(c["title"]).strip(),
            domain=str(c.get("domain") or "").strip(),
            aliases=_coerce_str_list(c.get("aliases")),
            summary=str(c.get("summary") or "").strip(),
        ))

    relations = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        target = _clean_target(r.get("target"))
        if not target:
            continue
        rtype = (r.get("type") or "mentions").lower()
        if rtype not in VALID_RELATIONS:
            rtype = "mentions"
        relations.append(Relation(
            type=rtype,
            target=target,
            rationale=str(r.get("rationale") or "").strip(),
        ))

    reconciliation = []
    for f in data.get("reconciliation") or []:
        if not isinstance(f, dict) or not f.get("summary"):
            continue
        target = _clean_target(f.get("target"))
        if not target:
            continue
        kind = (f.get("kind") or "").lower()
        if kind not in VALID_RECON_KINDS:
            continue
        reconciliation.append(ReconciliationFlag(
            target=target,
            kind=kind,
            summary=str(f["summary"]).strip(),
        ))

    return Synthesis(
        title=str(data.get("title") or fallback_title).strip(),
        summary=str(data.get("summary") or "").strip(),
        long_summary=str(data.get("long_summary") or "").strip(),
        key_points=_coerce_str_list(data.get("key_points")),
        notes=str(data.get("notes") or "").strip(),
        tags=_coerce_str_list(data.get("tags")),
        entities=entities,
        concepts=concepts,
        relations=relations,
        reconciliation=reconciliation,
    )
