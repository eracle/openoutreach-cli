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
    """Merge *data* into the credentials file (owner-only permissions)."""
    current = load()
    current.update(data)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(current, indent=2) + "\n")
    CREDENTIALS_FILE.chmod(0o600)


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


# Keys sent to POST /api/instances/ when provisioning.
INSTANCE_CONFIG_REQUIRED = [
    "campaign_name",
    "product_description",
    "campaign_objective",
    "linkedin_email",
    "linkedin_password",
    "llm_api_key",
    "ai_model",
]

INSTANCE_CONFIG_OPTIONAL = [
    "vpn_country",
    "vpn_city",
    "booking_link",
    "seed_urls",
    "llm_api_base",
    "newsletter",
    "connect_daily_limit",
    "connect_weekly_limit",
    "follow_up_daily_limit",
]

INSTANCE_CONFIG_KEYS = INSTANCE_CONFIG_REQUIRED + INSTANCE_CONFIG_OPTIONAL


def get_instance_config() -> dict | None:
    """Extract provisioning config from credentials.

    Returns the config dict if all required keys are present, else None.
    """
    creds = load()
    if not all(creds.get(k) for k in INSTANCE_CONFIG_REQUIRED):
        return None
    return {k: creds[k] for k in INSTANCE_CONFIG_KEYS if k in creds}
