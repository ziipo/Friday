"""Google OAuth helper — installed-app flow with Keychain-backed refresh tokens.

Each Google service (Calendar, Drive, Drive Activity) has its own least-privilege
OAuth client (per PRD §6.1), so client_id/secret and refresh tokens are stored
under separate Keychain entries:

  friday.google.<service>.client_id
  friday.google.<service>.client_secret
  friday.google.<service>            (the refresh token / serialized creds)

`run_install_flow(service, scopes)` opens the user's browser, exchanges the
auth code, and persists creds into Keychain. After that, `load_credentials()`
returns a Credentials object that auto-refreshes the access token.
"""
from __future__ import annotations

import json
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from . import secrets
from .logging import log_event

# Service-name → (default scopes) per PRD §6.2. Caller may override.
DEFAULT_SCOPES: dict[str, tuple[str, ...]] = {
    "calendar": (
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
    ),
    "drive": (
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.activity.readonly",
    ),
}


def _client_config(service: str) -> dict:
    """Build an InstalledAppFlow client_config dict from Keychain values."""
    client_id = secrets.require(f"google.{service}.client_id")
    client_secret = secrets.require(f"google.{service}.client_secret")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _serialize_creds(creds: Credentials) -> str:
    return json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    })


def _deserialize_creds(blob: str) -> Credentials:
    data = json.loads(blob)
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or [],
    )


def run_install_flow(service: str, scopes: Iterable[str] | None = None) -> Credentials:
    """One-time interactive flow. Opens the browser, grabs a refresh token,
    persists serialized creds under `friday.google.<service>`."""
    scopes = list(scopes or DEFAULT_SCOPES.get(service) or [])
    if not scopes:
        raise ValueError(f"no scopes for service {service!r}")
    flow = InstalledAppFlow.from_client_config(_client_config(service), scopes)
    creds = flow.run_local_server(port=0)
    secrets.set_(f"google.{service}", _serialize_creds(creds))
    log_event("google_oauth", "install.ok", service=service, scopes=scopes)
    return creds


def load_credentials(service: str) -> Credentials:
    """Return refreshed Credentials for `service`. Raises if no install has
    been run yet, or if the refresh token has been revoked."""
    blob = secrets.require(f"google.{service}")
    creds = _deserialize_creds(blob)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            secrets.set_(f"google.{service}", _serialize_creds(creds))
            log_event("google_oauth", "token.refreshed", service=service)
        else:
            raise RuntimeError(
                f"Google credentials for {service!r} are invalid and not refreshable. "
                f"Re-run: uv run python -m auth.google_{service}"
            )
    return creds
