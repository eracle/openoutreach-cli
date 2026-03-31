"""Thin HTTP client for the OpenOutreach hub API."""

from __future__ import annotations

import time

import httpx

from openoutreach.config import hub_url, require_token


def _base_url() -> str:
    return hub_url().rstrip("/")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {require_token()}"}


# ── Auth / Checkout ────────────────────────────────────────────────


def create_checkout(**answers) -> dict:
    """POST /api/checkout/ → {checkout_url, session_id}"""
    r = httpx.post(f"{_base_url()}/api/checkout/", json=answers)
    r.raise_for_status()
    return r.json()


def poll_auth_status(session_id: str, *, timeout: int = 300, interval: int = 3) -> dict:
    """Poll GET /api/auth/status/?session=<id> until checkout completes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = httpx.get(f"{_base_url()}/api/auth/status/", params={"session": session_id})
        r.raise_for_status()
        data = r.json()
        if data.get("api_token"):
            return data
        time.sleep(interval)
    raise TimeoutError("Checkout was not completed in time.")


# ── Instances ──────────────────────────────────────────────────────


def create_instance() -> dict:
    """POST /api/instances/ → {instance_id}"""
    r = httpx.post(f"{_base_url()}/api/instances/", headers=_auth_headers())
    r.raise_for_status()
    return r.json()


def get_instance(instance_id: int) -> dict:
    """GET /api/instances/{id}/ → {status, region, created_at, uptime}"""
    r = httpx.get(f"{_base_url()}/api/instances/{instance_id}/", headers=_auth_headers())
    r.raise_for_status()
    return r.json()


def poll_instance_running(instance_id: int, *, timeout: int = 300, interval: int = 5) -> dict:
    """Poll until instance status == 'running'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = get_instance(instance_id)
        if data.get("status") == "running":
            return data
        time.sleep(interval)
    raise TimeoutError("Instance did not reach 'running' state in time.")


def destroy_instance(instance_id: int) -> None:
    """DELETE /api/instances/{id}/"""
    r = httpx.delete(f"{_base_url()}/api/instances/{instance_id}/", headers=_auth_headers())
    r.raise_for_status()
