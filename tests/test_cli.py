"""Tests for the OpenOutreach CLI commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from openoutreach.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    """Redirect credentials to a temp directory."""
    monkeypatch.setattr("openoutreach.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("openoutreach.config.CREDENTIALS_FILE", tmp_path / "credentials.json")


# ── signup ─────────────────────────────────────────────────────────


@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
@patch("openoutreach.cli.ask_wizard")
def test_signup(mock_wizard, mock_checkout, mock_poll, mock_browser):
    mock_wizard.return_value = {
        "vpn_country": "Netherlands",
        "vpn_city": "Amsterdam",
        "campaign_name": "test",
        "product_description": "A test product",
        "campaign_objective": "sell to CTOs",
        "booking_link": "",
        "seed_urls": "",
        "linkedin_email": "a@b.com",
        "linkedin_password": "secret",
        "llm_api_key": "sk-test",
        "ai_model": "gpt-4o",
        "llm_api_base": "https://api.openai.com/v1",
        "newsletter": True,
        "connect_daily_limit": 50,
        "connect_weekly_limit": 250,
        "follow_up_daily_limit": 100,
        "legal_acceptance": True,
    }
    mock_checkout.return_value = {
        "checkout_url": "https://checkout.stripe.com/test",
        "session_id": "sess_123",
    }
    mock_poll.return_value = {"api_token": "tok_abc", "customer_id": "cus_123"}

    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 0
    assert "Signed up" in result.output
    mock_browser.assert_called_once()
    # vpn_country/vpn_city should be collapsed into vpn_location
    call_kwargs = mock_checkout.call_args[1]
    assert "vpn_location" in call_kwargs
    assert "vpn_country" not in call_kwargs


@patch("openoutreach.cli.ask_wizard")
def test_signup_cancelled(mock_wizard):
    mock_wizard.return_value = None
    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 1


# ── up ─────────────────────────────────────────────────────────────


@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up(mock_create, mock_poll):
    from openoutreach.config import save

    save({"api_token": "tok_abc"})

    mock_create.return_value = {"id": 42}
    mock_poll.return_value = {
        "status": "running",
        "region": "ams3",
        "droplet_ip": "1.2.3.4",
        "server_cert": "SERVERCERT",
        "client_cert": "CLIENTCERT",
        "client_key": "CLIENTKEY",
    }

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "running" in result.output

    from openoutreach.config import load

    creds = load()
    assert creds["droplet_ip"] == "1.2.3.4"
    assert creds["server_cert"] == "SERVERCERT"


# ── status ─────────────────────────────────────────────────────────


@patch("openoutreach.cli.client.get_instance")
def test_status(mock_get):
    from openoutreach.config import save

    save({"api_token": "tok_abc", "instance_id": 42})
    mock_get.return_value = {"status": "running", "region": "ams3", "uptime": "2h"}

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "running" in result.output


def test_status_no_instance():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1


# ── logs ──────────────────────────────────────────────────────────


@patch("openoutreach.cli.stream_logs")
def test_logs(mock_stream):
    from openoutreach.config import save

    save({
        "api_token": "tok_abc",
        "instance_id": 42,
        "droplet_ip": "1.2.3.4",
        "server_cert": "SERVERCERT",
        "client_cert": "CLIENTCERT",
        "client_key": "CLIENTKEY",
    })

    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    mock_stream.assert_called_once_with("1.2.3.4", "SERVERCERT", "CLIENTCERT", "CLIENTKEY")


def test_logs_no_instance():
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1


@patch("openoutreach.cli.stream_logs", side_effect=ConnectionError("refused"))
def test_logs_connection_error(mock_stream):
    from openoutreach.config import save

    save({
        "droplet_ip": "1.2.3.4",
        "server_cert": "SERVERCERT",
        "client_cert": "CLIENTCERT",
        "client_key": "CLIENTKEY",
    })

    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1


# ── down ───────────────────────────────────────────────────────────


@patch("openoutreach.cli.client.destroy_instance")
def test_down(mock_destroy):
    from openoutreach.config import save

    save({"api_token": "tok_abc", "instance_id": 42})

    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "destroyed" in result.output


def test_down_no_instance():
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
