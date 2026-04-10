"""Thin HTTP client for the OpenOutreach hub API."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from openoutreach.config import hub_url, require_token


class AuthExpiredError(Exception):
    """Raised when the hub rejects our API token (HTTP 403).

    This typically means the token was regenerated on another device.
    """


def _base_url() -> str:
    return hub_url().rstrip("/")


def _authed_request(method: str, path: str, **kwargs) -> httpx.Response:
    """Send an authenticated request to the hub API.

    Injects the Bearer token and raises ``AuthExpiredError`` on 403.
    All other HTTP errors propagate as ``httpx.HTTPStatusError``.
    """
    kwargs.setdefault("timeout", 30)
    r = httpx.request(
        method,
        f"{_base_url()}{path}",
        headers={"Authorization": f"Bearer {require_token()}"},
        **kwargs,
    )
    if r.status_code == 403:
        raise AuthExpiredError(r.text)
    r.raise_for_status()
    return r


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        return True
    return False


_retry_transient = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(max=4),
    reraise=True,
)


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


@_retry_transient
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


@_retry_transient
def create_instance(config: dict) -> dict:
    """POST /api/instances/ → instance data.  Retries on transient failures."""
    return _authed_request("POST", "/api/instances/", json=config, timeout=60).json()


@_retry_transient
def get_active_instance() -> dict | None:
    """GET /api/instances/ → active instance or None."""
    try:
        return _authed_request("GET", "/api/instances/").json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


@_retry_transient
def get_instance(instance_id: int) -> dict:
    """GET /api/instances/{id}/ → instance data."""
    return _authed_request("GET", f"/api/instances/{instance_id}/").json()


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


@_retry_transient
def destroy_instance(instance_id: int) -> None:
    """DELETE /api/instances/{id}/

    Idempotent: 404 is treated as success so a retried ``down`` after a
    dropped response (when the first DELETE did reach the hub) does not
    surface a spurious error.
    """
    try:
        _authed_request("DELETE", f"/api/instances/{instance_id}/")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
