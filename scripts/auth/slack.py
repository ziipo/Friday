"""Slack auth verification.

Slack apps for personal use issue static bot (xoxb-) and user (xoxp-) tokens
once installed. Friday stores both in Keychain via setup_secrets.py; this
module simply verifies they work and prints the resolved identity.

    PYTHONPATH=scripts uv run python -m auth.slack
"""
from __future__ import annotations

import sys

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from lib import secrets


def _check(label: str, secret_name: str) -> bool:
    token = secrets.get(secret_name)
    if not token:
        print(f"  [{label}] not set — run scripts/setup_secrets.py")
        return False
    cli = WebClient(token=token)
    try:
        resp = cli.auth_test()
    except SlackApiError as exc:
        print(f"  [{label}] auth_test failed: {exc.response.get('error')}")
        return False
    print(f"  [{label}] OK — team={resp.get('team')!r} user={resp.get('user')!r} "
          f"id={resp.get('user_id')}")
    return True


def main() -> int:
    print("Verifying Slack tokens…")
    ok_bot = _check("bot_token", "slack.bot_token")
    ok_user = _check("user_token", "slack.user_token")
    if not (ok_bot and ok_user):
        print("\nFix the failing token(s) and re-run.")
        return 1
    print("\nAll Slack tokens verified ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
