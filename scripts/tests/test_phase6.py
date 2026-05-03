"""Phase 6 test suite — Janitor.

Covers:
  - trigger.py (Phase 5, re-tested with edge cases)
  - matcher.py (Phase 5)
  - janitor/reputation.py — score formula, outcome tallying
  - janitor/index.py — index rebuild, log append
  - janitor/nightly.py — staleness, link rot logic, conflict proposal writer
  - janitor/weekly.py — demotion criteria, pruning criteria, trust ratchet
  - janitor/recapture.py — diff classification helpers

All tests use temp dirs and synthetic data; no network calls, no LLM calls,
no ArchiveBox invocations.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

# Point FRIDAY_ROOT at a temp dir for every test that touches the filesystem.
# Tests that need real records import after setting the env var.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_archive_record(root: Path, arc_id: str, **fields) -> Path:
    import yaml
    meta = {
        "id": arc_id,
        "source_type": fields.get("source_type", "markdown"),
        "captured_at": fields.get("captured_at", "2026-01-01T00:00:00Z"),
        "captured_via": "watcher",
        "provenance": {
            "shared_by": fields.get("shared_by", "alice@example.com"),
            "shared_in": fields.get("shared_in", "#product"),
            "shared_at": None,
            "context": "",
        },
        "canonical_url": fields.get("canonical_url", None),
        "artifacts": fields.get("artifacts", []),
        "title": fields.get("title", "Test Doc"),
        "one_line_summary": "",
        "relevance_score": fields.get("relevance_score", 0.5),
        "engagement_score": fields.get("engagement_score", 0.0),
        "status": fields.get("status", "archived"),
        "promoted_to": fields.get("promoted_to", None),
    }
    p = root / "Archive" / "records" / f"{arc_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n\n<!-- archive -->\n")
    return p


def make_memory_record(root: Path, src_id: str, arc_id: str, **fields) -> Path:
    import yaml
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta = {
        "id": src_id,
        "type": "source",
        "source_type": fields.get("source_type", "markdown"),
        "canonical_url": fields.get("canonical_url", None),
        "archive_record": arc_id,
        "captured_at": fields.get("captured_at", now),
        "last_verified": fields.get("last_verified", now),
        "promoted_at": fields.get("promoted_at", now),
        "promotion_reason": "relevance",
        "status": fields.get("status", "active"),
        "artifacts": fields.get("artifacts", []),
        "relations": fields.get("relations", []),
        "tags": [],
        "engagement": "passing",
        "title": fields.get("title", "Test Source"),
        "summary": fields.get("summary", "A test memory record."),
    }
    sources_dir = root / "Institutional-Memory" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    p = sources_dir / f"{src_id}.md"
    p.write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n\n# {meta['title']}\n")
    return p


# ===========================================================================
# 1. Trigger / engagement logic (Phase 5)
# ===========================================================================

class TestTrigger:
    def test_fast_track_promotes(self):
        from promoter.trigger import should_promote
        meta = {"status": "archived", "relevance_score": 0.95,
                 "extra": {"triage": {"fast_track": True}}}
        assert should_promote(meta, [])

    def test_high_relevance_promotes(self):
        from promoter.trigger import should_promote
        meta = {"status": "archived", "relevance_score": 0.71}
        assert should_promote(meta, [])

    def test_already_promoted_skipped(self):
        from promoter.trigger import should_promote
        meta = {"status": "promoted", "relevance_score": 0.99}
        assert not should_promote(meta, [{"type": "drive_comment"}])

    def test_engagement_promotes_low_relevance(self):
        from promoter.trigger import should_promote
        meta = {"status": "archived", "relevance_score": 0.3}
        assert should_promote(meta, [{"type": "slack_reaction"}])

    def test_large_meeting_does_not_promote_alone(self):
        from promoter.trigger import should_promote
        meta = {"status": "archived", "relevance_score": 0.3}
        signals = [{"type": "calendar_attendance", "extra": {"size_bucket": "large"}}]
        assert not should_promote(meta, signals)

    def test_small_meeting_promotes(self):
        from promoter.trigger import should_promote
        meta = {"status": "archived", "relevance_score": 0.3}
        signals = [{"type": "calendar_attendance", "extra": {"size_bucket": "small"}}]
        assert should_promote(meta, signals)

    def test_engagement_tags(self):
        from promoter.trigger import engagement_tag
        assert engagement_tag([]) == "passing"
        assert engagement_tag([{"type": "slack_reaction"}]) == "reviewed"
        assert engagement_tag([{"type": "drive_comment"}]) == "studied"
        assert engagement_tag([{"type": "slack_reaction"}, {"type": "drive_comment"}]) == "studied"
        cal_small = [{"type": "calendar_attendance", "extra": {"size_bucket": "small"}}]
        assert engagement_tag(cal_small) == "studied"
        cal_large = [{"type": "calendar_attendance", "extra": {"size_bucket": "large"}}]
        assert engagement_tag(cal_large) == "reviewed"


# ===========================================================================
# 2. Reputation score formula
# ===========================================================================

class TestReputationScore:
    def _score(self, p, a, d):
        from janitor.reputation import _score
        return _score({"promoted": p, "archived": a, "discarded": d})

    def test_cold_start(self):
        assert abs(self._score(0, 0, 0) - 0.5) < 1e-9

    def test_purely_promotional(self):
        s = self._score(100, 0, 0)
        assert s > 0.98

    def test_purely_noisy(self):
        s = self._score(0, 0, 100)
        assert s < 0.02

    def test_mixed(self):
        # 10 promoted, 5 archived, 5 discarded => (10+1)/(10+5+5+2) = 11/22 = 0.5
        assert abs(self._score(10, 5, 5) - 0.5) < 1e-9


# ===========================================================================
# 3. Reputation update — provenance join
# ===========================================================================

class TestReputationUpdate:
    def test_reads_provenance_from_archive_records(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setenv("FRIDAY_ROOT", str(tmp_path))
        # Reset lru_cache on paths module so FRIDAY_ROOT is picked up.
        import importlib
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.LOGS = tmp_path / ".logs"

        # Write a pipeline log entry with arc_id.
        log_dir = tmp_path / ".logs"
        log_dir.mkdir(parents=True)
        today = datetime.now(timezone.utc).date().isoformat()
        log_path = log_dir / "scribe.pipeline.jsonl"
        log_path.write_text(
            json.dumps({
                "ts": f"{today}T10:00:00Z",
                "component": "scribe.pipeline",
                "event": "archive_record.written",
                "arc_id": "arc_test_001",
                "decision": "fast_track",
            }) + "\n",
            encoding="utf-8",
        )

        # Write matching archive record.
        make_archive_record(tmp_path, "arc_test_001",
                            shared_by="bob@example.com", shared_in="#eng")

        # Patch module-level paths.
        import janitor.reputation as rep
        rep.LOG_PATH = log_path
        rep.REPUTATION_PATH = tmp_path / "Institutional-Memory" / ".reputation.json"

        channels, senders = rep._read_today_outcomes()
        assert "#eng" in channels
        assert channels["#eng"]["promoted"] == 1
        assert "bob@example.com" in senders
        assert senders["bob@example.com"]["promoted"] == 1

    def test_no_log_returns_empty(self, tmp_path):
        import janitor.reputation as rep
        rep.LOG_PATH = tmp_path / "nonexistent.jsonl"
        ch, se = rep._read_today_outcomes()
        assert len(ch) == 0
        assert len(se) == 0


# ===========================================================================
# 4. Index rebuild
# ===========================================================================

class TestIndexRebuild:
    def test_empty_vault(self, tmp_path, monkeypatch):
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        (tmp_path / "Institutional-Memory").mkdir(parents=True)

        import janitor.index as idx
        idx.INDEX_PATH = tmp_path / "Institutional-Memory" / "index.md"
        idx.LOG_PATH = tmp_path / "Institutional-Memory" / "log.md"

        counts = idx.rebuild_index()
        assert counts == {"sources": 0, "entities": 0, "concepts": 0}
        text = idx.INDEX_PATH.read_text()
        assert "0 sources" in text

    def test_with_sources(self, tmp_path):
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"

        make_archive_record(tmp_path, "arc_test_001")
        make_memory_record(tmp_path, "src_test_001", "arc_test_001",
                           title="Test Source Alpha")

        import janitor.index as idx
        idx.INDEX_PATH = tmp_path / "Institutional-Memory" / "index.md"
        idx.LOG_PATH = tmp_path / "Institutional-Memory" / "log.md"

        counts = idx.rebuild_index()
        assert counts["sources"] == 1
        text = idx.INDEX_PATH.read_text()
        assert "Test Source Alpha" in text
        assert "src_test_001" in text

    def test_append_log(self, tmp_path):
        import janitor.index as idx
        idx.LOG_PATH = tmp_path / "log.md"
        idx.append_log("Nightly sweep — 3 sources, 7s")
        text = idx.LOG_PATH.read_text()
        assert "Nightly sweep" in text
        assert datetime.now(timezone.utc).date().isoformat() in text


# ===========================================================================
# 5. Nightly — staleness check
# ===========================================================================

class TestStaleness:
    def _run_staleness(self, tmp_path, last_verified_days_ago: int,
                       source_type: str = "markdown") -> dict:
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"

        old_ts = (datetime.now(timezone.utc) - timedelta(days=last_verified_days_ago))
        old_iso = old_ts.isoformat().replace("+00:00", "Z")

        make_archive_record(tmp_path, "arc_stale_001", source_type=source_type)
        make_memory_record(tmp_path, "src_stale_001", "arc_stale_001",
                           last_verified=old_iso)

        import janitor.nightly as night
        night.STALE_DAYS = 30
        return night._check_staleness()

    def test_stale_non_web_flagged(self, tmp_path):
        counts = self._run_staleness(tmp_path, last_verified_days_ago=45,
                                     source_type="markdown")
        assert counts["marked_stale"] == 1

    def test_recent_not_flagged(self, tmp_path):
        counts = self._run_staleness(tmp_path, last_verified_days_ago=10,
                                     source_type="markdown")
        assert counts["marked_stale"] == 0

    def test_web_source_not_flagged_by_staleness(self, tmp_path):
        # Web sources get re-captured, not marked stale.
        counts = self._run_staleness(tmp_path, last_verified_days_ago=45,
                                     source_type="web")
        assert counts["marked_stale"] == 0


# ===========================================================================
# 6. Nightly — link rot check (no network — mock requests.head)
# ===========================================================================

class TestLinkRot:
    def _setup(self, tmp_path, url: str, status_code: int,
               dead_since_days: int | None = None):
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"
        lp.LOGS = tmp_path / ".logs"
        (tmp_path / ".logs" / "state").mkdir(parents=True, exist_ok=True)

        make_archive_record(tmp_path, "arc_lr_001", canonical_url=url,
                            source_type="web")
        make_memory_record(tmp_path, "src_lr_001", "arc_lr_001",
                           canonical_url=url)

        if dead_since_days is not None:
            dead_ts = (datetime.now(timezone.utc) - timedelta(days=dead_since_days))
            state = {"dead_since": {url: dead_ts.isoformat().replace("+00:00", "Z")}}
            state_path = tmp_path / ".logs" / "state" / "janitor_linkrot.json"
            state_path.write_text(json.dumps(state))

        return status_code

    def test_dead_link_after_grace(self, tmp_path, monkeypatch):
        url = "https://example.com/gone"
        self._setup(tmp_path, url, 404, dead_since_days=8)

        import requests
        class FakeResp:
            status_code = 404
        monkeypatch.setattr(requests, "head", lambda *a, **kw: FakeResp())

        # Patch poller_state._STATE_DIR so the pre-seeded dead_since is found.
        import lib.poller_state as ps
        monkeypatch.setattr(ps, "_STATE_DIR", tmp_path / ".logs" / "state")

        import janitor.nightly as night
        night.DEAD_LINK_GRACE_DAYS = 7
        counts = night._check_link_rot()
        assert counts["dead"] == 1

    def test_dead_link_within_grace_not_flagged(self, tmp_path, monkeypatch):
        url = "https://example.com/maybe-gone"
        self._setup(tmp_path, url, 404, dead_since_days=3)

        import requests
        class FakeResp:
            status_code = 404
        monkeypatch.setattr(requests, "head", lambda *a, **kw: FakeResp())

        import janitor.nightly as night
        night.DEAD_LINK_GRACE_DAYS = 7
        counts = night._check_link_rot()
        assert counts["dead"] == 0

    def test_live_link_not_flagged(self, tmp_path, monkeypatch):
        url = "https://example.com/alive"
        self._setup(tmp_path, url, 200)

        import requests
        class FakeResp:
            status_code = 200
        monkeypatch.setattr(requests, "head", lambda *a, **kw: FakeResp())

        import janitor.nightly as night
        counts = night._check_link_rot()
        assert counts["dead"] == 0


# ===========================================================================
# 7. Nightly — conflict proposal writer
# ===========================================================================

class TestConflictProposal:
    def test_proposal_written(self, tmp_path):
        import lib.paths as lp
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"

        from janitor.nightly import _write_conflict_proposal
        p = _write_conflict_proposal("src_001", "My Title",
                                     ["src_002", "src_003"], "Contradicts claim X")
        assert p.exists()
        text = p.read_text()
        assert "src_001" in text
        assert "src_002" in text
        assert "Contradicts claim X" in text
        assert p.parent == lp.REVIEW_QUEUE_PENDING


# ===========================================================================
# 8. Weekly — demotion criteria
# ===========================================================================

class TestDemotion:
    def _run_demotion(self, tmp_path, promoted_days_ago: int,
                      add_inbound_link: bool = False) -> dict:
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"

        promoted_at = (datetime.now(timezone.utc) - timedelta(days=promoted_days_ago))
        promoted_iso = promoted_at.isoformat().replace("+00:00", "Z")

        make_archive_record(tmp_path, "arc_demote_001")
        make_memory_record(tmp_path, "src_demote_001", "arc_demote_001",
                           promoted_at=promoted_iso)

        if add_inbound_link:
            # A second source whose relations point AT src_demote_001 (inbound link).
            make_archive_record(tmp_path, "arc_other_001")
            make_memory_record(tmp_path, "src_other_001", "arc_other_001",
                               relations=[{"type": "mentions", "target": "src_demote_001"}])

        import janitor.weekly as wk
        wk.DEMOTION_AGE_DAYS = 90
        return wk._demotion_pass()

    def test_old_unlinked_proposed(self, tmp_path):
        counts = self._run_demotion(tmp_path, promoted_days_ago=95)
        assert counts["proposed"] == 1

    def test_recent_not_proposed(self, tmp_path):
        counts = self._run_demotion(tmp_path, promoted_days_ago=30)
        assert counts["proposed"] == 0

    def test_linked_not_proposed(self, tmp_path):
        counts = self._run_demotion(tmp_path, promoted_days_ago=95, add_inbound_link=True)
        assert counts["proposed"] == 0


# ===========================================================================
# 9. Weekly — archive pruning criteria
# ===========================================================================

class TestPruning:
    def _run_pruning(self, tmp_path, captured_days_ago: int,
                     status: str = "archived",
                     relevance: float = 0.1) -> dict:
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.ARCHIVE_RENDERED = tmp_path / "Archive" / "Rendered"
        lp.ARCHIVE_CLEAN = tmp_path / "Archive" / "Clean"
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"

        cap_ts = (datetime.now(timezone.utc) - timedelta(days=captured_days_ago))
        make_archive_record(tmp_path, "arc_prune_001",
                            captured_at=cap_ts.isoformat().replace("+00:00", "Z"),
                            status=status,
                            relevance_score=relevance)

        import janitor.weekly as wk
        wk.PRUNE_AGE_DAYS = 365
        wk.PRUNE_MAX_RELEVANCE = 0.3
        return wk._pruning_pass()

    def test_old_low_relevance_proposed(self, tmp_path):
        counts = self._run_pruning(tmp_path, captured_days_ago=400, relevance=0.1)
        assert counts["proposed"] == 1

    def test_recent_not_proposed(self, tmp_path):
        counts = self._run_pruning(tmp_path, captured_days_ago=100)
        assert counts["proposed"] == 0

    def test_high_relevance_not_proposed(self, tmp_path):
        counts = self._run_pruning(tmp_path, captured_days_ago=400, relevance=0.8)
        assert counts["proposed"] == 0

    def test_promoted_not_proposed(self, tmp_path):
        counts = self._run_pruning(tmp_path, captured_days_ago=400,
                                   status="promoted", relevance=0.1)
        assert counts["proposed"] == 0


# ===========================================================================
# 10. Weekly — trust ratchet
# ===========================================================================

class TestTrustRatchet:
    def _setup(self, tmp_path, n_approved: int, n_pending: int, category: str = "diff_flag"):
        import lib.paths as lp
        lp.FRIDAY_ROOT = tmp_path
        lp.INSTITUTIONAL_MEMORY = tmp_path / "Institutional-Memory"
        lp.REVIEW_QUEUE_PENDING = tmp_path / "ReviewQueue" / "pending"

        approved_dir = tmp_path / "ReviewQueue" / "approved"
        approved_dir.mkdir(parents=True, exist_ok=True)
        pending_dir = tmp_path / "ReviewQueue" / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for i in range(n_approved):
            p = approved_dir / f"approved_{i:03d}.md"
            p.write_text(f"---\ntype: {category}\ncreated_at: {now_iso}\n---\n")
        for i in range(n_pending):
            p = pending_dir / f"pending_{i:03d}.md"
            p.write_text(f"---\ntype: {category}\ncreated_at: {now_iso}\n---\n")

        import janitor.weekly as wk
        wk.TRUST_STATS_PATH = tmp_path / "Institutional-Memory" / ".trust-stats.json"
        wk.TRUST_AUTO_APPLY_THRESHOLD = 0.95
        wk.TRUST_MIN_SAMPLES = 5  # lower threshold for test
        return wk

    def test_graduates_at_threshold(self, tmp_path):
        wk = self._setup(tmp_path, n_approved=19, n_pending=1, category="diff_flag")
        counts = wk._trust_ratchet()
        assert counts["graduated"] == 1
        stats = json.loads(wk.TRUST_STATS_PATH.read_text())
        assert stats["diff_flag"]["graduated"] is True

    def test_no_graduation_below_threshold(self, tmp_path):
        wk = self._setup(tmp_path, n_approved=10, n_pending=10, category="diff_flag")
        counts = wk._trust_ratchet()
        assert counts["graduated"] == 0

    def test_no_graduation_below_min_samples(self, tmp_path):
        # 4 approved, 0 pending = 100% ratio but only 4 samples (min=5)
        wk = self._setup(tmp_path, n_approved=4, n_pending=0, category="diff_flag")
        wk.TRUST_MIN_SAMPLES = 5
        counts = wk._trust_ratchet()
        assert counts["graduated"] == 0


# ===========================================================================
# 11. Recapture — diff classification helpers
# ===========================================================================

class TestDiffClassification:
    def test_identical_texts_returns_trivial(self, monkeypatch):
        from janitor.recapture import _classify_diff
        # Patch llm.complete to avoid network.
        import lib.llm as llm
        monkeypatch.setattr(llm, "complete", lambda **kw: "trivial")
        result = _classify_diff("Some Title", "same text", "same text")
        assert result == "trivial"

    def test_empty_diff_is_trivial_without_llm(self, monkeypatch):
        from janitor.recapture import _classify_diff
        called = []
        import lib.llm as llm
        monkeypatch.setattr(llm, "complete", lambda **kw: called.append(1) or "notable")
        result = _classify_diff("T", "abc", "abc")
        # No diff → should short-circuit before calling LLM
        assert result == "trivial"
        assert not called

    def test_llm_result_forwarded(self, monkeypatch):
        from janitor.recapture import _classify_diff
        import lib.llm as llm
        monkeypatch.setattr(llm, "complete", lambda **kw: "breaking")
        result = _classify_diff("Title", "old content here", "completely new content here")
        assert result == "breaking"

    def test_invalid_llm_response_falls_back_to_trivial(self, monkeypatch):
        from janitor.recapture import _classify_diff
        import lib.llm as llm
        monkeypatch.setattr(llm, "complete", lambda **kw: "definitely notable!!!")
        result = _classify_diff("T", "old", "brand new")
        assert result == "trivial"


# ===========================================================================
# 12. Matcher — resolution logic (Phase 5, comprehensive)
# ===========================================================================

class TestMatcher:
    def test_resolve_by_target_id(self, tmp_path):
        import lib.paths as lp
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        make_archive_record(tmp_path, "arc_match_001")

        from promoter import matcher
        matcher.invalidate_index()
        result = matcher.resolve_signal({"target_id": "arc_match_001"})
        assert result == "arc_match_001"

    def test_resolve_by_url(self, tmp_path):
        import lib.paths as lp
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        make_archive_record(tmp_path, "arc_match_002",
                            canonical_url="https://docs.example.com/page")

        from promoter import matcher
        matcher.invalidate_index()
        result = matcher.resolve_signal({"target_url": "https://docs.example.com/page"})
        assert result == "arc_match_002"

    def test_resolve_by_gdrive_file_id(self, tmp_path):
        import yaml
        import lib.paths as lp
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"

        # Build a record with gdrive_file_id in extra.
        arc_path = make_archive_record(tmp_path, "arc_match_003")
        text = arc_path.read_text()
        post_meta = yaml.safe_load(text.split("---")[1])
        post_meta["extra"] = {"gdrive_file_id": "gfile_abc123"}
        arc_path.write_text(
            f"---\n{yaml.safe_dump(post_meta, sort_keys=False)}---\n\n<!-- archive -->\n"
        )

        from promoter import matcher
        matcher.invalidate_index()
        result = matcher.resolve_signal({"extra": {"gdrive_file_id": "gfile_abc123"}})
        assert result == "arc_match_003"

    def test_unresolvable_returns_none(self, tmp_path):
        import lib.paths as lp
        lp.ARCHIVE_RECORDS = tmp_path / "Archive" / "records"
        lp.LOGS = tmp_path / ".logs"
        (tmp_path / ".logs").mkdir(parents=True)

        from promoter import matcher
        matcher.invalidate_index()
        result = matcher.resolve_signal({"type": "slack_reaction", "extra": {}})
        assert result is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
