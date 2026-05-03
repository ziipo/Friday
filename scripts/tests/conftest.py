"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest


REAL_TUNING_YAML = Path(__file__).parents[2] / "scripts" / "config" / "tuning.yaml"


@pytest.fixture(autouse=True)
def reset_tuning_cache(monkeypatch):
    """Ensure tuning config always points at the real tuning.yaml and cache is fresh."""
    import lib.tuning as tuning
    monkeypatch.setattr(tuning, "_CONFIG", REAL_TUNING_YAML)
    monkeypatch.setattr(tuning, "_LOCAL", REAL_TUNING_YAML.parent / "tuning.local.yaml")
    tuning.load.cache_clear()
    yield
    tuning.load.cache_clear()
