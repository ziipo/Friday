"""Interactive setup for Friday's macOS Keychain entries.

Run this once per machine to register API keys without ever pasting them into
shell history, env files, or git. Re-running is safe: it offers to overwrite
existing entries.

    PYTHONPATH=scripts uv run python scripts/setup_secrets.py
"""
from __future__ import annotations

import getpass
import sys

from lib import secrets
from lib.llm import healthcheck, load_config


PROMPTS = {
    "llm.anthropic": (
        "Anthropic API key (direct)",
        "Find or create one at https://console.anthropic.com/settings/keys",
    ),
    "llm.openrouter": (
        "OpenRouter API key",
        "Find or create one at https://openrouter.ai/keys",
    ),
    # Phase 4 — Google OAuth client credentials. Refresh tokens are obtained
    # via `python -m auth.google_<service>` after these are set.
    "google.calendar.client_id": (
        "Google Calendar OAuth client ID",
        "Create a 'Desktop app' OAuth client in https://console.cloud.google.com/apis/credentials",
    ),
    "google.calendar.client_secret": (
        "Google Calendar OAuth client secret",
        "Same OAuth client as above",
    ),
    "google.drive.client_id": (
        "Google Drive OAuth client ID",
        "Separate OAuth client (least privilege per PRD §6.1)",
    ),
    "google.drive.client_secret": (
        "Google Drive OAuth client secret",
        "Same OAuth client as above",
    ),
    # Phase 4 — Slack tokens. The bot token covers most poller reads;
    # search.messages requires a user token (PRD §5.1.4 risk note).
    "slack.bot_token": (
        "Slack bot token (xoxb-…)",
        "OAuth & Permissions page of your Slack app",
    ),
    "slack.user_token": (
        "Slack user token (xoxp-…)",
        "Required for search.messages (@-mention scan); user OAuth on the same app",
    ),
}


def _set_one(name: str, label: str, where: str) -> bool:
    existing = secrets.get(name)
    state = "set" if existing else "missing"
    print(f"\n[{name}] {label} — currently {state}")
    print(f"  ({where})")
    if existing:
        choice = input("  Overwrite? [y/N] ").strip().lower()
        if choice not in ("y", "yes"):
            return False
    value = getpass.getpass("  Paste key (input hidden): ").strip()
    if not value:
        print("  No value entered — skipping.")
        return False
    secrets.set_(name, value)
    print("  Stored in Keychain ✓")
    return True


def main() -> int:
    cfg = load_config()
    print("Friday secrets setup")
    print(f"Active LLM provider in config: {cfg.active}")
    print()

    changed_any = False
    for name in PROMPTS:
        label, where = PROMPTS[name]
        if _set_one(name, label, where):
            changed_any = True

    print("\nRunning health check against active provider…")
    try:
        provider, reply = healthcheck()
        print(f"  ✓ {provider} responded: {reply!r}")
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {exc}")
        print("  Fix the failing key and re-run, or switch `active:` in scripts/config/llm.yaml.")
        return 1

    if not changed_any:
        print("\n(No keys changed.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
