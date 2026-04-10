"""Tests for the OpenOutreach CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from openoutreach.cli import app
from openoutreach.client import CheckoutResult, Credentials

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

VPN_ANSWERS = {
    "vpn_country": "Netherlands",
    "vpn_city": "Amsterdam",
}


def _save_vpn_config_and_token():
    from openoutreach.config import save
    save({**VPN_ANSWERS, "api_token": "tok_abc", "linkedin_email": "a@b.com"})


def _create_db_file(tmp_path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "db.sqlite3").write_bytes(b"fake-sqlite-content")
    return data_dir


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr("openoutreach.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("openoutreach.config.CREDENTIALS_FILE", tmp_path / "credentials.json")


# ── signup ────────────────────────────────────────────────────────


@patch("openoutreach.cli.webbrowser.open")
@patch("openoutreach.cli.client.poll_auth_status")
@patch("openoutreach.cli.client.create_checkout")
@patch("openoutreach.cli.ask_wizard")
@patch("openoutreach.cli.typer.prompt", return_value="a@b.com")
def test_signup_new_user(mock_prompt, mock_wizard, mock_checkout, mock_poll, mock_browser):
    mock_wizard.return_value = VPN_ANSWERS.copy()
    mock_checkout.return_value = CheckoutResult(
        credentials=None,
        checkout_url="https://checkout.stripe.com/test",
        session_id="sess_123",
    )
    mock_poll.return_value = Credentials(api_token="tok_abc", customer_id="cus_123")

    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 0
    assert "Signed up" in result.output
    mock_browser.assert_called_once()


@patch("openoutreach.cli.client.create_checkout")
@patch("openoutreach.cli.ask_wizard")
@patch("openoutreach.cli.typer.prompt", return_value="a@b.com")
def test_signup_already_active(mock_prompt, mock_wizard, mock_checkout):
    mock_wizard.return_value = VPN_ANSWERS.copy()
    mock_checkout.return_value = CheckoutResult(
        credentials=Credentials(api_token="tok_abc", customer_id="cus_123"),
        checkout_url=None,
        session_id=None,
    )

    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 0
    assert "already active" in result.output


@patch("openoutreach.cli.ask_wizard")
def test_signup_cancelled(mock_wizard):
    mock_wizard.return_value = None
    result = runner.invoke(app, ["signup"])
    assert result.exit_code == 1


# ── up ────────────────────────────────────────────────────────────


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.sidecar_upload_db")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up_auto_tails(mock_create, mock_poll, mock_upload, mock_stream, tmp_path):
    _save_vpn_config_and_token()
    data_dir = _create_db_file(tmp_path)
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up", str(data_dir)])
    assert result.exit_code == 0
    assert "running" in result.output
    assert "Database uploaded" in result.output
    mock_upload.assert_called_once()
    mock_stream.assert_called_once()


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.sidecar_upload_db")
@patch("openoutreach.cli.client.poll_instance_running")
@patch("openoutreach.cli.client.create_instance")
def test_up_no_logs(mock_create, mock_poll, mock_upload, mock_stream, tmp_path):
    _save_vpn_config_and_token()
    data_dir = _create_db_file(tmp_path)
    mock_create.return_value = {"id": 42}
    mock_poll.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["up", str(data_dir), "--no-logs"])
    assert result.exit_code == 0
    mock_stream.assert_not_called()


@patch("openoutreach.cli.client.create_instance")
def test_up_rejects_existing_instance(mock_create, tmp_path):
    import httpx

    _save_vpn_config_and_token()
    data_dir = _create_db_file(tmp_path)
    response = httpx.Response(409, json={"error": "You already have an active instance."})
    mock_create.side_effect = httpx.HTTPStatusError("conflict", request=httpx.Request("POST", "http://x"), response=response)

    result = runner.invoke(app, ["up", str(data_dir)])
    assert result.exit_code == 1
    assert "already have an active instance" in result.output


def test_up_rejects_bad_path(tmp_path):
    _save_vpn_config_and_token()
    result = runner.invoke(app, ["up", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1


# ── upload-db ────────────────────────────────────────────────────


@patch("openoutreach.cli.sidecar_upload_db")
@patch("openoutreach.cli.client.get_active_instance")
def test_upload_db(mock_active, mock_upload, tmp_path):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE
    data_dir = _create_db_file(tmp_path)

    result = runner.invoke(app, ["upload-db", str(data_dir)])
    assert result.exit_code == 0
    assert "Database uploaded" in result.output
    mock_upload.assert_called_once()


# ── status ────────────────────────────────────────────────────────


@patch("openoutreach.cli.client.get_active_instance")
def test_status(mock_active):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = {"status": "running", "region": "ams3", "uptime": "2h"}

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "running" in result.output


@patch("openoutreach.cli.client.get_active_instance", return_value=None)
def test_status_no_instance(mock_active):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1


def test_status_no_token():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1


# ── logs ──────────────────────────────────────────────────────────


@patch("openoutreach.cli.stream_logs")
@patch("openoutreach.cli.client.get_active_instance")
def test_logs(mock_active, mock_stream):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    mock_stream.assert_called_once()


@patch("openoutreach.cli.client.get_active_instance", return_value=None)
def test_logs_no_instance(mock_active):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1


def test_logs_no_token():
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1


# ── down ──────────────────────────────────────────────────────────


@patch("openoutreach.cli.sidecar_download_db")
@patch("openoutreach.cli.client.destroy_instance")
@patch("openoutreach.cli.client.get_active_instance")
def test_down_downloads_then_destroys(mock_active, mock_destroy, mock_download, tmp_path):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE
    backup = tmp_path / "backup.sqlite3"

    result = runner.invoke(app, ["down", "--backup-path", str(backup)])
    assert result.exit_code == 0
    assert "Database saved to" in result.output
    assert "destroyed" in result.output

    # download must happen before destroy
    mock_download.assert_called_once()
    assert mock_download.call_args.kwargs["dest_path"] == backup.resolve()
    mock_destroy.assert_called_once_with(42)


@patch("openoutreach.cli.sidecar_download_db")
@patch("openoutreach.cli.client.destroy_instance")
@patch("openoutreach.cli.client.get_active_instance")
def test_down_no_download_flag_skips_download(mock_active, mock_destroy, mock_download):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE

    result = runner.invoke(app, ["down", "--no-download"])
    assert result.exit_code == 0
    assert "destroyed" in result.output
    mock_download.assert_not_called()
    mock_destroy.assert_called_once_with(42)


@patch("openoutreach.cli.sidecar_download_db")
@patch("openoutreach.cli.client.destroy_instance")
@patch("openoutreach.cli.client.get_active_instance")
def test_down_prompts_and_aborts_if_user_declines_overwrite(
    mock_active, mock_destroy, mock_download, tmp_path
):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE
    existing = tmp_path / "backup.sqlite3"
    existing.write_bytes(b"preexisting")

    # Answer "n" to the overwrite prompt.
    result = runner.invoke(
        app, ["down", "--backup-path", str(existing)], input="n\n"
    )
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "Aborted" in result.output

    mock_download.assert_not_called()
    mock_destroy.assert_not_called()
    # Original file is untouched.
    assert existing.read_bytes() == b"preexisting"


@patch("openoutreach.cli.sidecar_download_db", side_effect=RuntimeError("network down"))
@patch("openoutreach.cli.client.destroy_instance")
@patch("openoutreach.cli.client.get_active_instance")
def test_down_aborts_if_download_fails(mock_active, mock_destroy, mock_download, tmp_path):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    mock_active.return_value = INSTANCE_RESPONSE

    result = runner.invoke(
        app, ["down", "--backup-path", str(tmp_path / "backup.sqlite3")]
    )
    assert result.exit_code == 1
    assert "DB download failed" in result.output
    mock_destroy.assert_not_called()


@patch("openoutreach.cli.client.get_active_instance", return_value=None)
def test_down_no_instance(mock_active):
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1


# ── client.destroy_instance idempotency ──────────────────────────


@patch("openoutreach.client.httpx.request")
def test_destroy_instance_treats_404_as_success(mock_request):
    """A retried DELETE after the server already removed the row must not error."""
    import httpx as _httpx

    from openoutreach import client as client_mod
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    response = _httpx.Response(
        404, request=_httpx.Request("DELETE", "https://hub/api/instances/42/")
    )
    mock_request.return_value = response

    # Should not raise.
    client_mod.destroy_instance(42)


@patch("openoutreach.client.httpx.request")
def test_destroy_instance_propagates_other_errors(mock_request):
    import httpx as _httpx

    from openoutreach import client as client_mod
    from openoutreach.config import save
    save({"api_token": "tok_abc"})

    response = _httpx.Response(
        400, request=_httpx.Request("DELETE", "https://hub/api/instances/42/")
    )
    mock_request.return_value = response

    with pytest.raises(_httpx.HTTPStatusError):
        client_mod.destroy_instance(42)
