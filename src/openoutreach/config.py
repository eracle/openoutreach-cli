"""Local credential storage (~/.openoutreach/credentials.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".openoutreach"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"

PRODUCTION_HUB_URL = "https://hub.openoutreach.app"
LOCAL_HUB_URL = "http://localhost:8000"
LOCAL = bool(os.environ.get("OPENOUTREACH_LOCAL"))


def hub_url() -> str:
    if LOCAL:
        return LOCAL_HUB_URL
    creds = load()
    return creds.get("hub_url", PRODUCTION_HUB_URL)


def load() -> dict:
    """Load stored credentials, or return empty dict."""
    if not CREDENTIALS_FILE.exists():
        return {}
    return json.loads(CREDENTIALS_FILE.read_text())


def save(data: dict) -> None:
    """Merge *data* into the credentials file."""
    current = load()
    current.update(data)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(current, indent=2) + "\n")


def get_token() -> str | None:
    """Return the stored API token, or None."""
    return load().get("api_token")


def require_token() -> str:
    """Return the stored API token, or exit with a helpful message."""
    token = get_token()
    if not token:
        from rich.console import Console

        Console(stderr=True).print(
            "[red]Not logged in.[/red] Run [bold]openoutreach signup[/bold] first."
        )
        raise SystemExit(1)
    return token
