"""Tests for the OpenOutreach CLI commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from openoutreach.cli import app

runner = CliRunner()

INSTANCE_RESPONSE = {
    "id": 42,
    "status": "running",
    "region": "ams3",
    "droplet_ip": "1.2.3.4",
    "server_cert": "SERVERCERT",
    "client_cert": "CLIENTCERT",
    "client_key": "CLIENTKEY",
}

WIZARD_ANSWERS = {
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

# Config fields saved after wizard (legal_acceptance stripped).
SAVED_CONFIG = {k: v for k, v in WIZARD_ANSWERS.items() if k != "legal_acceptance"}


def _save_config_and_token():
    """Pre-populate credentials with config + api_token."""
    from openoutreach.config import save
    save({**SAVED_CONFIG, "api_token": "tok_abc"})


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    """Redirect credentials to a temp directory."""
    monkeypatch.setattr("openoutreach.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("openoutreach.config.CREDENTIALS_FILE", tmp_path / "credentials.json")


# ── config ────────────────────────────────────────────────────────


@patch("openoutreach.cli.ask_wizard")
def test_config_saves_locally(mock_wizard):
    from openoutreach.config import load

    mock_wizard.return_value = WIZARD_ANSWERS.copy()

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "Configuration saved" in result.output

    creds = load()
    assert creds["vpn_country"] == "Netherlands"
    assert creds["vpn_city"] == "Amsterdam"
    assert creds["linkedin_email"] == "a@b.com"
    assert "legal_acceptance" not in creds


@patch("openoutreach.cli.ask_wizard")
def test_config_cancelled(mock_wizard):
    mock_wizard.return_value = None
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 1


# ── signup ────────────────────────────────────────────────────────


@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
@patch("openoutreach.cli.ask_wizard")
def test_signup(mock_wizard, mock_checkout, mock_poll, mock_browser):
    mock_wizard.return_value = WIZARD_ANSWERS.copy()
    mock_checkout.return_value = {
        "checkout_url": "https://checkout.stripe.com/test",
        "session_id": "sess_123",
    }
    mock_poll.return_value = {"api_token": "tok_abc", "customer_id": "cus_123"}

    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 0
    assert "Signed up" in result.output
    mock_browser.assert_called_once()
    mock_checkout.assert_called_once_with("a@b.com")


@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
def test_signup_with_existing_config(mock_checkout, mock_poll, mock_browser):
    """If config already exists, signup skips the wizard."""
    from openoutreach.config import save
    save(SAVED_CONFIG)

    mock_checkout.return_value = {
        "checkout_url": "https://checkout.stripe.com/test",
        "session_id": "sess_123",
    }
    mock_poll.return_value = {"api_token": "tok_abc", "customer_id": "cus_123"}

    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 0
    mock_checkout.assert_called_once_with("a@b.com")


@patch("openoutreach.cli.ask_wizard")
def test_signup_cancelled(mock_wizard):
    mock_wizard.return_value = None
    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 1


# ── up ────────────────────────────────────────────────────────────


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up_auto_tails(mock_create, mock_poll, mock_stream):
    _save_config_and_token()
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "running" in result.output

    mock_stream.assert_called_once()
    assert mock_stream.call_args.kwargs["droplet_ip"] == "1.2.3.4"

    # Verify config was sent to create_instance.
    config_arg = mock_create.call_args[0][0]
    assert config_arg["linkedin_email"] == "a@b.com"
    assert config_arg["vpn_country"] == "Netherlands"


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up_no_logs(mock_create, mock_poll, mock_stream):
    _save_config_and_token()
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up", "--no-logs"])
    assert result.exit_code == 0
    assert "running" in result.output
    mock_stream.assert_not_called()


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up_saves_certs(mock_create, mock_poll, mock_stream):
    from openoutreach.config import load

    _save_config_and_token()
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    runner.invoke(app, ["up"])

    creds = load()
    assert creds["droplet_ip"] == "1.2.3.4"
    assert creds["server_cert"] == "SERVERCERT"
    assert creds["client_cert"] == "CLIENTCERT"
    assert creds["client_key"] == "CLIENTKEY"


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
@patch("openoutreach.cli.ask_wizard")
def test_up_chains_config_and_signup(
    mock_wizard, mock_checkout, mock_poll_auth, mock_browser,
    mock_create, mock_poll, mock_stream,
):
    """From scratch: up chains config → signup → provision."""
    mock_wizard.return_value = WIZARD_ANSWERS.copy()
    mock_checkout.return_value = {
        "checkout_url": "https://checkout.stripe.com/test",
        "session_id": "sess_123",
    }
    mock_poll_auth.return_value = {"api_token": "tok_abc", "customer_id": "cus_123"}
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0

    mock_wizard.assert_called_once()
    mock_checkout.assert_called_once_with("a@b.com")
    mock_create.assert_called_once()


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
def test_up_chains_signup_only(
    mock_checkout, mock_poll_auth, mock_browser,
    mock_create, mock_poll, mock_stream,
):
    """Config exists but no token: up chains signup → provision (no wizard)."""
    from openoutreach.config import save
    save(SAVED_CONFIG)

    mock_checkout.return_value = {
        "checkout_url": "https://checkout.stripe.com/test",
        "session_id": "sess_123",
    }
    mock_poll_auth.return_value = {"api_token": "tok_abc", "customer_id": "cus_123"}
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0

    mock_checkout.assert_called_once_with("a@b.com")
    mock_create.assert_called_once()


# ── status ────────────────────────────────────────────────────────


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

    mock_stream.assert_called_once()
    assert mock_stream.call_args.kwargs["droplet_ip"] == "1.2.3.4"


def test_logs_no_creds():
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1
    assert "No instance credentials" in result.output


# ── down ──────────────────────────────────────────────────────────


@patch("openoutreach.cli.client.destroy_instance")
def test_down(mock_destroy):
    from openoutreach.config import save

    save({"api_token": "tok_abc", "instance_id": 42})

    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "destroyed" in result.output


@patch("openoutreach.cli.client.destroy_instance")
def test_down_clears_certs(mock_destroy):
    from openoutreach.config import load, save

    save({
        "api_token": "tok_abc",
        "instance_id": 42,
        "droplet_ip": "1.2.3.4",
        "server_cert": "SERVERCERT",
        "client_cert": "CLIENTCERT",
        "client_key": "CLIENTKEY",
    })

    runner.invoke(app, ["down"])

    creds = load()
    assert creds["instance_id"] is None
    assert creds["droplet_ip"] is None


def test_down_no_instance():
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
