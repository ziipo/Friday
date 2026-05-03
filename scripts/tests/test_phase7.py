"""Phase 7 tests — Trust Ratchet, Tuning config, Vault commit hook."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import frontmatter


# ---------------------------------------------------------------------------
# Tuning config loader
# ---------------------------------------------------------------------------

class TestTuningLoader:
    def test_get_returns_default_when_missing(self):
        from lib import tuning
        # Clear cache before testing
        tuning.load.cache_clear()
        val = tuning.get("nonexistent_section", "nonexistent_key", "fallback")
        assert val == "fallback"

    def test_get_known_values(self):
        from lib import tuning
        tuning.load.cache_clear()
        assert tuning.get("triage", "low_floor", 999) == pytest.approx(0.2)
        assert tuning.get("triage", "high_floor", 999) == pytest.approx(0.7)

    def test_get_janitor_values(self):
        from lib import tuning
        tuning.load.cache_clear()
        assert tuning.get("janitor", "stale_days", 0) == 30
        assert tuning.get("janitor", "dead_link_grace_days", 0) == 7

    def test_local_override_deep_merges(self, tmp_path, monkeypatch):
        from lib import tuning
        tuning.load.cache_clear()
        base = tmp_path / "tuning.yaml"
        local = tmp_path / "tuning.local.yaml"
        base.write_text("triage:\n  low_floor: 0.2\n  high_floor: 0.7\n", encoding="utf-8")
        local.write_text("triage:\n  high_floor: 0.9\n", encoding="utf-8")
        monkeypatch.setattr(tuning, "_CONFIG", base)
        monkeypatch.setattr(tuning, "_LOCAL", local)
        tuning.load.cache_clear()
        assert tuning.get("triage", "low_floor") == pytest.approx(0.2)
        assert tuning.get("triage", "high_floor") == pytest.approx(0.9)
        tuning.load.cache_clear()


# ---------------------------------------------------------------------------
# Trust ratchet constants wired from tuning
# ---------------------------------------------------------------------------

class TestTuningWired:
    def test_decide_uses_tuning_floors(self):
        from triage.decide import LOW_FLOOR, HIGH_FLOOR
        assert 0.0 < LOW_FLOOR < 1.0
        assert 0.0 < HIGH_FLOOR < 1.0
        assert LOW_FLOOR < HIGH_FLOOR

    def test_nightly_constants_from_tuning(self):
        from lib import tuning
        assert tuning.get("janitor", "stale_days") == 30
        assert tuning.get("janitor", "dead_link_grace_days") == 7
        assert tuning.get("janitor", "conflict_lookback_days") == 3
        assert tuning.get("janitor", "conflict_context_sources") == 10

    def test_weekly_constants_from_tuning(self):
        from lib import tuning
        assert tuning.get("weekly", "demotion_age_days") == 90
        assert tuning.get("weekly", "prune_age_days") == 365
        assert tuning.get("weekly", "prune_max_relevance") == pytest.approx(0.3)
        assert tuning.get("trust_ratchet", "auto_apply_threshold") == pytest.approx(0.95)
        assert tuning.get("trust_ratchet", "min_samples") == 20
        assert tuning.get("trust_ratchet", "window_days") == 30


# ---------------------------------------------------------------------------
# Trust ratchet apply logic
# ---------------------------------------------------------------------------

def _make_proposal(directory: Path, name: str, category: str, created_at: str) -> Path:
    p = directory / name
    p.write_text(
        f"---\ntype: {category}\nsrc_id: test-src\ncreated_at: {created_at}\n---\n\nProposal body.\n",
        encoding="utf-8",
    )
    return p


class TestTrustRatchetApply:
    def _apply_module(self):
        import importlib
        import trust_ratchet.apply as m
        return m

    def test_no_proposals_returns_zeros(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value={}):
            counts = m.apply()
        assert counts == {"applied": 0, "skipped": 0, "errors": 0}

    def test_graduated_proposals_are_moved(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        _make_proposal(pending, "2024-01-01_demote_foo.md", "demotion", "2024-01-01T00:00:00Z")
        stats = {"demotion": {"graduated": True, "ratio": 0.98, "kept": 20, "proposed": 1}}
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value=stats):
            counts = m.apply()
        assert counts["applied"] == 1
        assert counts["skipped"] == 0
        assert (approved / "2024-01-01_demote_foo.md").exists()
        assert not (pending / "2024-01-01_demote_foo.md").exists()

    def test_non_graduated_proposals_stay_in_pending(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        _make_proposal(pending, "2024-01-01_stale_bar.md", "staleness", "2024-01-01T00:00:00Z")
        stats = {"staleness": {"graduated": False, "ratio": 0.5, "kept": 5, "proposed": 10}}
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value=stats):
            counts = m.apply()
        assert counts["skipped"] == 1
        assert counts["applied"] == 0
        assert (pending / "2024-01-01_stale_bar.md").exists()

    def test_review_all_skips_everything(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        _make_proposal(pending, "2024-01-01_demote_baz.md", "demotion", "2024-01-01T00:00:00Z")
        stats = {"demotion": {"graduated": True}}
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value=stats):
            counts = m.apply(review_all=True)
        assert counts["skipped"] == 1
        assert counts["applied"] == 0

    def test_dry_run_does_not_move_files(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        _make_proposal(pending, "2024-01-01_demote_dry.md", "demotion", "2024-01-01T00:00:00Z")
        stats = {"demotion": {"graduated": True}}
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value=stats):
            counts = m.apply(dry_run=True)
        assert counts["applied"] == 1
        assert (pending / "2024-01-01_demote_dry.md").exists()

    def test_collision_safe_rename(self, tmp_path):
        m = self._apply_module()
        pending = tmp_path / "pending"
        approved = tmp_path / "approved"
        pending.mkdir()
        approved.mkdir()
        fname = "2024-01-01_demote_col.md"
        _make_proposal(pending, fname, "demotion", "2024-01-01T00:00:00Z")
        # Pre-create a file at the target path to force collision handling.
        (approved / fname).write_text("existing", encoding="utf-8")
        stats = {"demotion": {"graduated": True}}
        with patch.object(m, "PENDING_DIR", pending), \
             patch.object(m, "APPROVED_DIR", approved), \
             patch.object(m, "load_trust_stats", return_value=stats):
            counts = m.apply()
        assert counts["applied"] == 1
        # Original name preserved + a renamed copy (_1 suffix).
        assert any(approved.glob("*demote_col*.md"))


# ---------------------------------------------------------------------------
# Vault commit helper
# ---------------------------------------------------------------------------

class TestVaultCommit:
    def test_commit_called_on_success(self, tmp_path):
        from scribe import watcher
        with patch("subprocess.run") as mock_run:
            # Simulate: git add succeeds, git diff returns 1 (staged changes), git commit succeeds.
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add -A
                MagicMock(returncode=1),  # git diff --cached --quiet (1 = has changes)
                MagicMock(returncode=0),  # git commit
            ]
            with patch.object(watcher.paths, "FRIDAY_ROOT", tmp_path):
                watcher._vault_commit("test_file.md")
        assert mock_run.call_count == 3
        commit_call = mock_run.call_args_list[2]
        assert "ingest: test_file.md" in commit_call.args[0][-1]

    def test_no_commit_when_nothing_staged(self, tmp_path):
        from scribe import watcher
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add -A
                MagicMock(returncode=0),  # git diff --quiet (0 = nothing staged)
            ]
            with patch.object(watcher.paths, "FRIDAY_ROOT", tmp_path):
                watcher._vault_commit("empty.md")
        assert mock_run.call_count == 2

    def test_commit_error_logged_not_raised(self, tmp_path):
        from scribe import watcher
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "git", stderr=b"fatal: not a git repo"
            )
            with patch.object(watcher.paths, "FRIDAY_ROOT", tmp_path):
                watcher._vault_commit("bad.md")  # must not raise
