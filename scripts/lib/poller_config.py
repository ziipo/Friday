"""Poller configuration loader.

Reads scripts/config/pollers.yaml + an optional gitignored .local.yaml override
(deep-merged: per-key, shallow values overwrite, dicts recurse).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from .paths import FRIDAY_ROOT

CONFIG_PATH = FRIDAY_ROOT / "scripts" / "config" / "pollers.yaml"
LOCAL_CONFIG_PATH = FRIDAY_ROOT / "scripts" / "config" / "pollers.local.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    base = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if LOCAL_CONFIG_PATH.exists():
        override = yaml.safe_load(LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        base = _deep_merge(base, override)
    return base


def for_poller(name: str) -> dict[str, Any]:
    return dict(load().get(name) or {})
