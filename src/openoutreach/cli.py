"""OpenOutreach CLI — commands: signup, up, upload-db, status, logs, down."""

from __future__ import annotations

import webbrowser
from pathlib import Path

import httpx
import typer
from rich.console import Console

from openoutreach import __version__, client
from openoutreach import config as config_mod
from openoutreach.client import Credentials
from openoutreach.log_stream import stream_logs, upload_db as sidecar_upload_db
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


# ── Signup ──────────────────────────────────────────────────────


def _ensure_vpn_config() -> dict:
    """Return VPN config, running the wizard if missing."""
    instance_config = config_mod.get_instance_config()
    if instance_config:
        return instance_config

    console.print("No VPN configuration found — starting wizard.\n")
    answers = ask_wizard(PREMIUM_QUESTIONS)
    if answers is None:
        err.print("Cancelled.")
        raise SystemExit(1)

    config_mod.save(answers)
    instance_config = config_mod.get_instance_config()
    if not instance_config:
        err.print("[red]VPN configuration incomplete.[/red] Run [bold]openoutreach signup[/bold] again.")
        raise SystemExit(1)
    return instance_config


def _authenticate(email: str) -> Credentials:
    """Run the Stripe checkout flow and return credentials."""
    with console.status("Creating checkout session…"):
        result = client.create_checkout(email)

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
    """Run the full signup flow: VPN config → checkout → save credentials."""
    _ensure_vpn_config()
    creds = config_mod.load()
    email = creds.get("linkedin_email")
    if not email:
        email = typer.prompt("LinkedIn email (used for Stripe checkout)")
        config_mod.save({"linkedin_email": email})

    credentials = _authenticate(email)
    _save_credentials(credentials)
    console.print("[green]✓[/green] Signed up! Credentials saved.")


@app.command()
def signup() -> None:
    """Sign up for OpenOutreach Premium via Stripe checkout."""
    _run_signup()
    console.print("Run [bold]openoutreach up[/bold] to provision your cloud instance.")


# ── DB path helpers ─────────────────────────────────────────────


def _validate_db_path(path: Path) -> Path:
    """Resolve a data dir or db.sqlite3 path. Exit on error."""
    path = path.expanduser().resolve()
    if path.is_dir():
        path = path / "db.sqlite3"
    if not path.is_file() or path.suffix != ".sqlite3":
        err.print(f"[red]No valid db.sqlite3 at {path}[/red]")
        raise SystemExit(1)
    size_mb = path.stat().st_size / (1024 * 1024)
    console.print(f"Using db: {path} ({size_mb:.1f} MB)")
    return path


def _upload_to_sidecar(info: dict, db_path: Path) -> None:
    """Upload a DB file to the instance's sidecar."""
    with console.status("Uploading database…"):
        sidecar_upload_db(
            droplet_ip=info["droplet_ip"],
            server_cert=info["server_cert"],
            client_cert=info["client_cert"],
            client_key=info["client_key"],
            db_path=db_path,
        )
    console.print("[green]✓[/green] Database uploaded.")


# ── Up ──────────────────────────────────────────────────────────


@app.command()
def up(
    db: Path = typer.Argument(..., help="Path to data directory or db.sqlite3 file."),
    no_logs: bool = typer.Option(False, "--no-logs", help="Skip auto-tailing logs after provisioning."),
) -> None:
    """Provision and start your cloud instance."""
    db_path = _validate_db_path(db)
    instance_config = _ensure_vpn_config()

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

    _upload_to_sidecar(info, db_path)

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


# ── Upload DB (standalone) ──────────────────────────────────────


@app.command("upload-db")
def upload_db_cmd(
    db: Path = typer.Argument(..., help="Path to data directory or db.sqlite3 file."),
) -> None:
    """Re-upload your local database to the running cloud instance."""
    db_path = _validate_db_path(db)
    info = _require_active_instance()
    _upload_to_sidecar(info, db_path)


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
