"""Thin HTTP client for the OpenOutreach hub API."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from openoutreach.config import hub_url, require_token


def _base_url() -> str:
    return hub_url().rstrip("/")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {require_token()}"}


# ── Response types ────────────────────────────────────────────────


@dataclass(frozen=True)
class Credentials:
    """Authenticated user credentials returned after checkout."""

    api_token: str
    customer_id: str


@dataclass(frozen=True)
class CheckoutResult:
    """Result of POST /api/checkout/.

    Exactly one of ``credentials`` or ``checkout`` is set:
    - ``credentials``: user already active, token returned directly.
    - ``checkout``: new user, redirect to Stripe then poll auth_status.
    """

    credentials: Credentials | None
    checkout_url: str | None
    session_id: str | None

    @property
    def is_active(self) -> bool:
        return self.credentials is not None


# ── Auth / Checkout ───────────────────────────────────────────────


def create_checkout(linkedin_email: str) -> CheckoutResult:
    """POST /api/checkout/ → CheckoutResult."""
    r = httpx.post(f"{_base_url()}/api/checkout/", json={"linkedin_email": linkedin_email})
    r.raise_for_status()
    data = r.json()

    if data["status"] == "active":
        return CheckoutResult(
            credentials=Credentials(api_token=data["api_token"], customer_id=data["customer_id"]),
            checkout_url=None,
            session_id=None,
        )

    return CheckoutResult(
        credentials=None,
        checkout_url=data["checkout_url"],
        session_id=data["session_id"],
    )


def poll_auth_status(session_id: str, *, timeout: int = 300, interval: int = 3) -> Credentials:
    """Poll GET /api/auth/status/?session=<id> until checkout completes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{_base_url()}/api/auth/status/", params={"session": session_id})
        r.raise_for_status()
        data = r.json()

        if data["status"] == "complete":
            return Credentials(api_token=data["api_token"], customer_id=data["customer_id"])

        if data["status"] == "consumed":
            raise RuntimeError("API token was already retrieved. Run 'openoutreach config' to re-authenticate.")

        time.sleep(interval)

    raise TimeoutError("Checkout was not completed in time.")


# ── Instances ─────────────────────────────────────────────────────


def create_instance(config: dict) -> dict:
    """POST /api/instances/ → instance data."""
    r = httpx.post(
        f"{_base_url()}/api/instances/",
        headers=_auth_headers(),
        json=config,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def get_active_instance() -> dict | None:
    """GET /api/instances/ → active instance or None."""
    r = httpx.get(f"{_base_url()}/api/instances/", headers=_auth_headers(), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_instance(instance_id: int) -> dict:
    """GET /api/instances/{id}/ → instance data."""
    r = httpx.get(f"{_base_url()}/api/instances/{instance_id}/", headers=_auth_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def poll_instance_running(
    instance_id: int,
    *,
    timeout: int = 300,
    interval: int = 5,
    on_tick: Callable[[str], None] | None = None,
) -> dict:
    """Poll until instance status == 'running'.

    *on_tick* is called after each poll with the current status string.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = get_instance(instance_id)
        status = data.get("status", "unknown")
        if status == "running":
            return data
        if on_tick:
            on_tick(status)
        time.sleep(interval)
    raise TimeoutError("Instance did not reach 'running' state in time.")


def destroy_instance(instance_id: int) -> None:
    """DELETE /api/instances/{id}/"""
    r = httpx.delete(f"{_base_url()}/api/instances/{instance_id}/", headers=_auth_headers())
    r.raise_for_status()
