"""Triage evaluation harness.

Runs 10 hand-labeled test cases through the Triage scorer and decision matrix.
Mocks artifacts on disk so the scorer can read them.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lib import paths
from lib.protocol import Artifact, CandidateRecord, Provenance
from scribe import pipeline
from triage import context as ctx_mod
from triage import reputation as rep_mod
from triage.decide import Decision


@dataclass
class TestCase:
    name: str
    title: str
    content: str
    source_type: str
    shared_by: str | None = "self"
    shared_in: str | None = None
    canonical_url: str | None = None
    expected_decision: Decision | None = None


TEST_CASES = [
    TestCase(
        name="high_project_spec",
        title="Friday Architecture Proposal: Promoter Layer",
        content="This document outlines the design of the Promoter layer for the Friday project. It handles engagement-triggered promotion.",
        source_type="markdown",
        canonical_url="https://github.com/ziipo/Friday/blob/main/docs/promoter.md",
        expected_decision=Decision.FAST_TRACK,
    ),
    TestCase(
        name="high_llm_report",
        title="Claude 3.7 Technical Report",
        content="Anthropic announces Claude 3.7, a new model optimized for reasoning and orchestration tasks.",
        source_type="web",
        canonical_url="https://www.anthropic.com/news/claude-3-7",
        expected_decision=Decision.FAST_TRACK,
    ),
    TestCase(
        name="high_pkm_concept",
        title="Designing a Personal Second Brain",
        content="An in-depth guide on how to build a personal second brain using modern AI tools and structured notes.",
        source_type="web",
        canonical_url="https://example.com/pkm-guide",
        expected_decision=Decision.FAST_TRACK,
    ),
    TestCase(
        name="medium_status",
        title="Weekly Status Update - Friday Project",
        content="Progress this week: Phase 1 complete, starting Phase 2. Triage logic is being drafted.",
        source_type="email",
        shared_by="ken@example.com",
        expected_decision=Decision.ARCHIVE_ONLY,
    ),
    TestCase(
        name="medium_sync",
        title="Meeting Notes: AI Infrastructure sync",
        content="Attendees: Ken, Alice. Discussed GPU allocation and model deployment strategies for the brain project.",
        source_type="markdown",
        expected_decision=Decision.ARCHIVE_ONLY,
    ),
    TestCase(
        name="medium_anthropic_news",
        title="Anthropic Q1 Community Update",
        content="We've updated our terms of service and added new features to the developer console.",
        source_type="web",
        canonical_url="https://www.anthropic.com/news/q1-update",
        expected_decision=Decision.ARCHIVE_ONLY,
    ),
    TestCase(
        name="low_spam_receipt",
        title="Amazon Order Confirmation",
        content="Thank you for your order! Your items will ship soon. Order #123-456789.",
        source_type="email",
        shared_by="auto-confirm@amazon.com",
        expected_decision=Decision.DISCARD,
    ),
    TestCase(
        name="low_noise_digest",
        title="Daily News Digest - 2026-05-02",
        content="Top stories today: Weather is sunny, local sports team wins, and new cafe opens downtown.",
        source_type="web",
        canonical_url="https://news.example.com/digest-2026-05-02",
        expected_decision=Decision.DISCARD,
    ),
    TestCase(
        name="duplicate_of_existing",
        title="Friday Architecture Proposal: Triage Layer",
        content="This document outlines the design of the Triage layer for the Friday project. It uses Sonnet for scoring.",
        source_type="markdown",
        canonical_url="https://github.com/ziipo/Friday/blob/main/docs/triage.md",
        expected_decision=Decision.LINK_DUPLICATE,
    ),
]


from lib.ids import archive_id

def setup_case(case: TestCase, arc_id: str, captured_at: datetime) -> CandidateRecord:
    clean_dir = paths.ARCHIVE_CLEAN / arc_id
    clean_dir.mkdir(parents=True, exist_ok=True)
    content_file = clean_dir / "content.txt"
    content_file.write_text(case.content, encoding="utf-8")

    seed = case.canonical_url or case.title
    return CandidateRecord(
        source_type=case.source_type,
        captured_via="eval",
        arc_id=arc_id,
        seed=seed,
        captured_at=captured_at,
        canonical_url=case.canonical_url,
        title=case.title,
        provenance=Provenance(
            shared_by=case.shared_by,
            shared_in=case.shared_in,
            context="eval harness",
        ),
        artifacts=[Artifact(path=Path("Archive/Clean") / arc_id / "content.txt", type="clean")],
    )


def run_eval():
    print("Starting Triage Evaluation...")
    snapshot = ctx_mod.load_snapshot()
    reputation = rep_mod.load()

    passed = 0
    total = len(TEST_CASES)

    for i, case in enumerate(TEST_CASES):
        captured_at = datetime.now(timezone.utc)
        seed = case.canonical_url or case.title
        arc_id = archive_id(seed, captured_at)
        candidate = setup_case(case, arc_id, captured_at)

        print(f"\n[{i+1}/{total}] Case: {case.name}")
        print(f"  Title: {case.title}")

        try:
            result = pipeline.process_candidate(candidate, snapshot=snapshot, reputation=reputation)
            decision = Decision(result["decision"])

            print(f"  Score: {result['score']:.2f}")
            print(f"  Decision: {decision.value}")

            if case.expected_decision:
                if decision == case.expected_decision:
                    print("  Result: PASS ✓")
                    passed += 1
                else:
                    print(f"  Result: FAIL ✗ (Expected: {case.expected_decision.value})")
            else:
                print("  Result: (No expectation set)")

        except Exception as exc:
            print(f"  Result: ERROR ✗ ({type(exc).__name__}: {exc})")
        finally:
            # Cleanup
            shutil.rmtree(paths.ARCHIVE_CLEAN / arc_id, ignore_errors=True)
            record_path = paths.ARCHIVE_RECORDS / f"{arc_id}.md"
            if record_path.exists():
                record_path.unlink()

    print(f"\nFinal Score: {passed}/{total}")
    if passed == total:
        print("All tests passed! 🎉")
        return 0
    else:
        print("Some tests failed.")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(run_eval())
