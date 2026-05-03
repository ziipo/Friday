"""Scribe pipeline: ingest → triage → decide → archive.

The watcher dispatches a file to an ingestor; the ingestor returns
CandidateRecords with artifacts already on disk under Archive/{Rendered,Clean}/{arc_id}/.
This module then:
  1. Loads the memory snapshot and reputation lookup once per file.
  2. Triages each candidate.
  3. Applies the decision matrix.
  4. For ARCHIVE_ONLY / FAST_TRACK: writes the archive_record .md, with
     relevance_score + one_line_summary populated.
  5. For DISCARD / LINK_DUPLICATE: cleans up the staged artifact dirs and
     emits a log entry.

FAST_TRACK candidates are flagged via `extra.triage.fast_track = true` in the
archive_record so the Promoter (Phase 5) can pick them up immediately.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import paths
from lib.archive_record import write_archive_record
from lib.logging import log_event
from lib.protocol import CandidateRecord
from triage import context as ctx_mod
from triage import reputation as rep_mod
from triage.decide import Decision, decide
from triage.scorer import score as score_candidate


def _artifact_dirs(candidate: CandidateRecord) -> set[Path]:
    """Top-level Archive/{Rendered,Clean}/{arc_id} dirs touched by this candidate."""
    dirs: set[Path] = set()
    for art in candidate.artifacts:
        # art.path is repo-relative like Archive/Clean/{arc_id}/file.txt
        rel = Path(str(art.path))
        if len(rel.parts) >= 3 and rel.parts[0] == "Archive":
            dirs.add(paths.FRIDAY_ROOT / Path(*rel.parts[:3]))  # type: ignore[attr-defined]
    return dirs


def _cleanup_artifacts(candidate: CandidateRecord) -> None:
    """Discard the artifact directories staged by an ingestor when triage rejects."""
    for d in _artifact_dirs(candidate):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def process_candidate(candidate: CandidateRecord, *, snapshot=None, reputation=None) -> dict[str, Any]:
    """Run a single candidate through triage + decision + write."""
    snapshot = snapshot or ctx_mod.load_snapshot()
    reputation = reputation or rep_mod.load()

    score = score_candidate(candidate, snapshot=snapshot)
    channel_score = reputation.channel_score(candidate.provenance.shared_in)
    sender_score = reputation.sender_score(candidate.provenance.shared_by)
    decision = decide(score, channel_score=channel_score, sender_score=sender_score)

    log_event("scribe.pipeline", "triage.decision",
              decision=decision.value,
              relevance=score.relevance_score,
              spam=score.spam,
              duplicate_of=score.duplicate_of,
              channel_rep=channel_score,
              sender_rep=sender_score,
              title=candidate.title)

    if decision in (Decision.DISCARD, Decision.LINK_DUPLICATE):
        _cleanup_artifacts(candidate)
        return {
            "decision": decision.value,
            "score": score.relevance_score,
            "duplicate_of": score.duplicate_of,
            "arc_id": None,
        }

    # Carry triage output onto the archive_record.
    extra = dict(candidate.extra)
    extra["triage"] = {
        "rationale": score.rationale,
        "fast_track": decision == Decision.FAST_TRACK,
    }
    enriched = CandidateRecord(
        source_type=candidate.source_type,
        captured_via=candidate.captured_via,
        arc_id=candidate.arc_id,
        seed=candidate.seed,
        captured_at=candidate.captured_at,
        canonical_url=candidate.canonical_url,
        title=candidate.title,
        one_line_summary=score.one_line_summary or candidate.one_line_summary,
        provenance=candidate.provenance,
        artifacts=candidate.artifacts,
        extra=extra,
    )

    if not candidate.arc_id or not candidate.seed or not candidate.captured_at:
        raise ValueError(
            "Pipeline requires CandidateRecord.arc_id, seed, and captured_at "
            "(set by the ingestor). Got arc_id="
            f"{candidate.arc_id!r}, seed={candidate.seed!r}"
        )

    arc_id, record_path = write_archive_record(
        enriched, seed=candidate.seed, captured_at=candidate.captured_at,
    )
    if arc_id != candidate.arc_id:
        raise RuntimeError(
            f"arc_id drift: ingestor staged under {candidate.arc_id} but "
            f"pipeline writer produced {arc_id}. Seed/captured_at must round-trip."
        )

    # Patch in the relevance_score (writer initializes it to 0.0).
    _patch_relevance(record_path, score.relevance_score)

    log_event("scribe.pipeline", "archive_record.written",
              arc_id=arc_id, decision=decision.value,
              relevance=score.relevance_score,
              record_path=str(record_path))

    return {
        "decision": decision.value,
        "score": score.relevance_score,
        "duplicate_of": None,
        "arc_id": arc_id,
    }


def _patch_relevance(record_path: Path, relevance: float) -> None:
    """Replace the placeholder `relevance_score: 0.0` line in the frontmatter."""
    text = record_path.read_text(encoding="utf-8")
    new_line = f"relevance_score: {relevance:.3f}"
    patched = text.replace("relevance_score: 0.0", new_line, 1)
    if patched == text:
        # Frontmatter format may have changed; do a regex fallback.
        import re
        patched = re.sub(r"relevance_score:\s*[\d.]+", new_line, text, count=1)
    record_path.write_text(patched, encoding="utf-8")


def process_candidates(candidates: list[CandidateRecord]) -> list[dict[str, Any]]:
    snapshot = ctx_mod.load_snapshot()
    reputation = rep_mod.load()
    return [process_candidate(c, snapshot=snapshot, reputation=reputation) for c in candidates]
