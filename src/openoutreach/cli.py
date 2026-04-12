"""OpenOutreach CLI — commands: signup, up, upload-db, status, logs, down."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from openoutreach import __version__, client
from openoutreach import config as config_mod
from openoutreach.client import AuthExpiredError, Credentials
from openoutreach.log_stream import (
    download_db as sidecar_download_db,
    stream_logs,
    upload_db as sidecar_upload_db,
)
from openoutreach.prompts import CLOUD_QUESTIONS
from openoutreach.wizard import ask as ask_wizard

_REAUTH_MSG = (
    "[red]Session expired.[/red] Your API token was regenerated on another device.\n"
    "Run [bold]openoutreach signup[/bold] to re-authenticate."
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"openoutreach {__version__}")
        raise typer.Exit()


app = typer.Typer(help="Manage your OpenOutreach Cloud instance.", invoke_without_command=True, add_completion=False)


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
    answers = ask_wizard(CLOUD_QUESTIONS)
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
    """Sign up for OpenOutreach Cloud via Stripe checkout."""
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
    sidecar_upload_db(
        droplet_ip=info["droplet_ip"],
        server_cert=info["server_cert"],
        client_cert=info["client_cert"],
        client_key=info["client_key"],
        db_path=db_path,
    )
    console.print("[green]✓[/green] Database uploaded.")


DEFAULT_DB_FILENAME = "db.sqlite3"


def _default_backup_path() -> Path:
    """Return ``./db.sqlite3`` in the current working directory."""
    return Path.cwd() / DEFAULT_DB_FILENAME


def _download_from_sidecar(info: dict, dest_path: Path) -> None:
    """Stop the remote app and download its db.sqlite3 to *dest_path*."""
    sidecar_download_db(
        droplet_ip=info["droplet_ip"],
        server_cert=info["server_cert"],
        client_cert=info["client_cert"],
        client_key=info["client_key"],
        dest_path=dest_path,
    )


# ── Up ──────────────────────────────────────────────────────────


@app.command()
def up(
    db: Optional[Path] = typer.Argument(
        None,
        help="Path to your OpenOutreach data/ directory or db.sqlite3 file.",
        metavar="DB_PATH",
        show_default=False,
    ),
    no_logs: bool = typer.Option(False, "--no-logs", help="Skip auto-tailing logs after provisioning."),
) -> None:
    """Provision and start your cloud instance.

    Examples:

        openoutreach up ./data/

        openoutreach up ./data/db.sqlite3
    """
    if db is None:
        err.print("[red]Missing DB_PATH.[/red] Pass your data/ directory or db.sqlite3 file.\n")
        err.print("  openoutreach up ./data/")
        err.print("  openoutreach up ./data/db.sqlite3\n")
        raise SystemExit(1)
    db_path = _validate_db_path(db)
    instance_config = _ensure_vpn_config()

    if not config_mod.get_token():
        _run_signup()

    config_mod.require_token()

    try:
        with console.status("Creating instance…"):
            data = client.create_instance(instance_config)
    except AuthExpiredError:
        err.print(_REAUTH_MSG)
        raise SystemExit(1)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            err.print("[red]You already have an active instance.[/red] Run [bold]openoutreach down[/bold] first.")
            raise SystemExit(1)
        raise

    instance_id = data["id"]

    estimated_seconds = 60
    poll_interval = 5
    estimated_ticks = estimated_seconds // poll_interval

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Provisioning instance…", total=estimated_ticks)

        def _on_tick(status: str) -> None:
            progress.update(task, advance=1, description=f"Instance {status}…")

        info = client.poll_instance_running(instance_id, on_tick=_on_tick)
        progress.update(task, completed=estimated_ticks)

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
    try:
        info = client.get_active_instance()
    except AuthExpiredError:
        err.print(_REAUTH_MSG)
        raise SystemExit(1)
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


def _resolve_backup_target(backup_path: Optional[Path]) -> Path:
    """Resolve the user-supplied backup path, or the default, to an absolute path."""
    return (backup_path or _default_backup_path()).expanduser().resolve()


def _confirm_overwrite(path: Path) -> bool:
    """Return True if *path* does not exist, or the user confirms overwrite."""
    if not path.exists():
        return True
    return typer.confirm(f"{path} already exists. Overwrite?")


def _run_download(info: dict, target: Path) -> None:
    """Download the remote DB to *target* and announce success. Raises on failure."""
    _download_from_sidecar(info, target)
    console.print(f"[green]✓[/green] Database saved to {target}")


def _run_destroy(instance_id: int) -> None:
    """Destroy the instance via the hub and announce success. Raises on failure."""
    with console.status("Destroying instance…"):
        client.destroy_instance(instance_id)
    console.print("[green]✓[/green] Instance destroyed.")


@app.command()
def down(
    no_download: bool = typer.Option(
        False, "--no-download", help="Skip downloading the DB before destroying the instance."
    ),
    backup_path: Optional[Path] = typer.Option(
        None,
        "--backup-path",
        help="Where to save the downloaded DB. Default: ./db.sqlite3 (prompts before overwrite).",
    ),
) -> None:
    """Download the cloud DB, then destroy your cloud instance.

    Save-first, destroy-second: the droplet is only destroyed after the DB
    file is on local disk. If download fails, the droplet is left running
    and the command can be retried safely. If the target backup path
    already exists the user is prompted before anything is overwritten.
    """
    target = None if no_download else _resolve_backup_target(backup_path)
    if target is not None and not _confirm_overwrite(target):
        err.print("Aborted.")
        raise SystemExit(1)

    info = _require_active_instance()

    if target is not None:
        _run_download(info, target)

    _run_destroy(info["id"])
