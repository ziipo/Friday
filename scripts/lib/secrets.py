"""Keychain-backed secret storage per PRD §6.1.

All Friday secrets share a service prefix so they're easy to audit/remove together.
Secrets stored:
- friday.llm.anthropic     — Anthropic API key (direct)
- friday.llm.openrouter    — OpenRouter API key (test/dev)
- friday.slack             — Slack OAuth refresh token (Phase 4)
- friday.google.calendar   — Google Calendar OAuth refresh token (Phase 4)
- friday.google.drive      — Google Drive OAuth refresh token (Phase 4)
"""
from __future__ import annotations

import keyring

SERVICE_PREFIX = "friday"


def _service(name: str) -> str:
    return f"{SERVICE_PREFIX}.{name}"


def get(name: str, *, account: str = "default") -> str | None:
    return keyring.get_password(_service(name), account)


def set_(name: str, value: str, *, account: str = "default") -> None:
    keyring.set_password(_service(name), account, value)


def delete(name: str, *, account: str = "default") -> None:
    try:
        keyring.delete_password(_service(name), account)
    except keyring.errors.PasswordDeleteError:
        pass


def require(name: str, *, account: str = "default") -> str:
    value = get(name, account=account)
    if not value:
        raise RuntimeError(
            f"Missing secret {_service(name)!r} in Keychain. "
            f"Run: uv run python scripts/setup_secrets.py"
        )
    return value
