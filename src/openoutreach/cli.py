"""OpenOutreach CLI — commands: config, signup, up, status, logs, down."""

from __future__ import annotations

import webbrowser

import httpx
import typer
from rich.console import Console

from openoutreach import __version__, client
from openoutreach import config as config_mod
from openoutreach.client import Credentials
from openoutreach.log_stream import stream_logs
from openoutreach.prompts import PREMIUM_QUESTIONS
from openoutreach.wizard import ask as ask_wizard

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"openoutreach {__version__}")
        raise typer.Exit()


app = typer.Typer(help="Manage your OpenOutreach Premium cloud instance.", invoke_without_command=True, add_completion=False)


@app.callback()
def main(
    ctx: typer.Context,
    local: bool = typer.Option(False, "--local", hidden=True),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    if local:
        config_mod.LOCAL = True
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()

console = Console()
err = Console(stderr=True)


# ── Config ──────────────────────────────────────────────────────


def _run_config_wizard() -> dict:
    """Run the interactive wizard and return cleaned answers."""
    answers = ask_wizard(PREMIUM_QUESTIONS)
    if answers is None:
        err.print("Cancelled.")
        raise SystemExit(1)

    answers.pop("legal_acceptance", None)
    return answers


@app.command("config")
def config_cmd() -> None:
    """Run the configuration wizard (saves settings locally)."""
    answers = _run_config_wizard()
    config_mod.save(answers)
    console.print("[green]✓[/green] Configuration saved.")


# ── Signup ──────────────────────────────────────────────────────


def _ensure_config() -> dict:
    """Return instance config, running the wizard if missing."""
    instance_config = config_mod.get_instance_config()
    if instance_config:
        return instance_config
    console.print("No configuration found — starting wizard.\n")
    answers = _run_config_wizard()
    config_mod.save(answers)
    instance_config = config_mod.get_instance_config()
    if not instance_config:
        err.print("[red]Configuration incomplete.[/red] Run [bold]openoutreach config[/bold] and fill all required fields.")
        raise SystemExit(1)
    return instance_config


def _authenticate(linkedin_email: str) -> Credentials:
    """Run the full checkout flow and return credentials.

    Handles both paths:
    - Already-active user → token returned immediately.
    - New user → Stripe checkout → poll until webhook fires.
    """
    with console.status("Creating checkout session…"):
        result = client.create_checkout(linkedin_email)

    if result.is_active:
        console.print("Subscription already active — reusing credentials.")
        return result.credentials

    console.print(f"Opening checkout: [link={result.checkout_url}]{result.checkout_url}[/link]")
    webbrowser.open(result.checkout_url)

    with console.status("Waiting for payment…"):
        return client.poll_auth_status(result.session_id)


def _save_credentials(credentials: Credentials) -> None:
    config_mod.save({"api_token": credentials.api_token, "customer_id": credentials.customer_id})


def _run_signup() -> None:
    """Run the full signup flow: config → checkout → save credentials."""
    instance_config = _ensure_config()
    credentials = _authenticate(instance_config["linkedin_email"])
    _save_credentials(credentials)
    console.print("[green]✓[/green] Signed up! Credentials saved.")


@app.command()
def signup() -> None:
    """Sign up for OpenOutreach Premium via Stripe checkout."""
    _run_signup()
    console.print("Run [bold]openoutreach up[/bold] to provision your cloud instance.")


# ── Up ──────────────────────────────────────────────────────────


@app.command()
def up(
    no_logs: bool = typer.Option(False, "--no-logs", help="Skip auto-tailing logs after provisioning."),
) -> None:
    """Provision and start your cloud instance."""
    instance_config = _ensure_config()

    if not config_mod.get_token():
        _run_signup()

    config_mod.require_token()

    try:
        with console.status("Creating instance…"):
            data = client.create_instance(instance_config)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            err.print("[red]You already have an active instance.[/red] Run [bold]openoutreach down[/bold] first.")
            raise SystemExit(1)
        raise

    instance_id = data["id"]

    with console.status("Waiting for instance to start…"):
        info = client.poll_instance_running(instance_id)

    console.print(f"[green]✓[/green] Instance running — region: {info['region']} ({info['droplet_ip']})")

    if no_logs:
        return

    stream_logs(
        droplet_ip=info["droplet_ip"],
        server_cert=info["server_cert"],
        client_cert=info["client_cert"],
        client_key=info["client_key"],
        console=console,
        max_wait=300,
    )


# ── Status / Logs / Down ───────────────────────────────────────


def _require_active_instance() -> dict:
    """Fetch the active instance from the hub, or exit."""
    config_mod.require_token()
    info = client.get_active_instance()
    if not info:
        err.print("[red]No instance found.[/red] Run [bold]openoutreach up[/bold] first.")
        raise SystemExit(1)
    return info


@app.command()
def status() -> None:
    """Show current instance status."""
    info = _require_active_instance()
    console.print(f"Status:  [bold]{info['status']}[/bold]")
    console.print(f"Region:  {info['region']}")
    if info.get("uptime"):
        console.print(f"Uptime:  {info['uptime']}")


@app.command()
def logs() -> None:
    """Stream live logs from your cloud instance (mTLS, tail -f style)."""
    info = _require_active_instance()
    stream_logs(
        droplet_ip=info["droplet_ip"],
        server_cert=info["server_cert"],
        client_cert=info["client_cert"],
        client_key=info["client_key"],
        console=console,
    )


@app.command()
def down() -> None:
    """Destroy your cloud instance."""
    info = _require_active_instance()
    with console.status("Destroying instance…"):
        client.destroy_instance(info["id"])
    console.print("[green]✓[/green] Instance destroyed.")
