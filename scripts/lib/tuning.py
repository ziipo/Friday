"""Tuning config loader (PRD §9 open questions).

Reads scripts/config/tuning.yaml with optional deep-merge from
scripts/config/tuning.local.yaml (gitignored).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from .paths import FRIDAY_ROOT

_CONFIG = FRIDAY_ROOT / "scripts" / "config" / "tuning.yaml"
_LOCAL = FRIDAY_ROOT / "scripts" / "config" / "tuning.local.yaml"


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
    base: dict[str, Any] = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    if _LOCAL.exists():
        override = yaml.safe_load(_LOCAL.read_text(encoding="utf-8")) or {}
        base = _deep_merge(base, override)
    return base


def get(section: str, key: str, default: Any = None) -> Any:
    return (load().get(section) or {}).get(key, default)
