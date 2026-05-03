"""One-time OAuth install for Google Drive + Drive Activity (PRD §6.2).

    PYTHONPATH=scripts uv run python -m auth.google_drive

Prerequisites: client_id/secret in Keychain (setup_secrets), Drive API and
Drive Activity API enabled in the Google Cloud project, OAuth consent screen
configured for "Desktop app".
"""
from __future__ import annotations

import sys

from lib.google_oauth import DEFAULT_SCOPES, run_install_flow


def main() -> int:
    print("Installing Google Drive OAuth credentials…")
    print(f"Scopes: {', '.join(DEFAULT_SCOPES['drive'])}")
    creds = run_install_flow("drive")
    print(f"  ✓ Stored refresh token in Keychain (friday.google.drive)")
    print(f"  Token valid: {creds.valid}, scopes: {creds.scopes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
