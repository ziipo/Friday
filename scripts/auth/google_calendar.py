"""One-time OAuth install for Google Calendar (PRD §6.2).

Run once per machine:

    PYTHONPATH=scripts uv run python -m auth.google_calendar

Prerequisites: client_id and client_secret stored in Keychain via
`scripts/setup_secrets.py`. The Google Cloud project must have the Calendar API
enabled and the OAuth consent screen configured for "Desktop app" with
redirect URI http://localhost.
"""
from __future__ import annotations

import sys

from lib.google_oauth import DEFAULT_SCOPES, run_install_flow


def main() -> int:
    print("Installing Google Calendar OAuth credentials…")
    print(f"Scopes: {', '.join(DEFAULT_SCOPES['calendar'])}")
    creds = run_install_flow("calendar")
    print(f"  ✓ Stored refresh token in Keychain (friday.google.calendar)")
    print(f"  Token valid: {creds.valid}, scopes: {creds.scopes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
