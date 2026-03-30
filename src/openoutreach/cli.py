"""OpenOutreach CLI — 4 commands: signup, up, status, down."""

from __future__ import annotations

import webbrowser

import httpx
import typer
from rich.console import Console

from openoutreach import client, config
from openoutreach.prompts import PREMIUM_QUESTIONS
from openoutreach.wizard import ask as ask_wizard

app = typer.Typer(help="Manage your OpenOutreach Premium cloud instance.")


@app.callback()
def main(local: bool = typer.Option(False, "--local", hidden=True)) -> None:
    if local:
        config.LOCAL = True
console = Console()
err = Console(stderr=True)


@app.command()
def signup() -> None:
    """Sign up for OpenOutreach Premium via Stripe checkout."""
    answers = ask_wizard(PREMIUM_QUESTIONS)
    if answers is None:
        err.print("Cancelled.")
        raise SystemExit(1)

    answers["vpn_location"] = answers.pop("vpn_city") or answers.pop("vpn_country")
    answers.pop("vpn_country", None)

    with console.status("Creating checkout session…"):
        data = client.create_checkout(**answers)

    checkout_url = data["checkout_url"]
    session_id = data["session_id"]

    console.print(f"Opening checkout: [link={checkout_url}]{checkout_url}[/link]")
    webbrowser.open(checkout_url)

    with console.status("Waiting for payment…"):
        result = client.poll_auth_status(session_id)

    config.save({"api_token": result["api_token"], "customer_id": result["customer_id"]})
    console.print("[green]✓[/green] Signed up! Credentials saved.")
    console.print("Run [bold]openoutreach up[/bold] to provision your cloud instance.")


@app.command()
def up() -> None:
    """Provision and start your cloud instance."""
    config.require_token()

    try:
        with console.status("Creating instance…"):
            data = client.create_instance()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            err.print("[red]You already have an active instance.[/red] Run [bold]openoutreach down[/bold] first.")
            raise SystemExit(1)
        raise

    instance_id = data["id"]
    config.save({"instance_id": instance_id})

    with console.status("Waiting for instance to start…"):
        info = client.poll_instance_running(instance_id)

    console.print(f"[green]✓[/green] Instance running — region: {info['region']}")


@app.command()
def status() -> None:
    """Show current instance status."""
    creds = config.load()
    instance_id = creds.get("instance_id")
    if not instance_id:
        err.print("[red]No instance found.[/red] Run [bold]openoutreach up[/bold] first.")
        raise SystemExit(1)

    config.require_token()
    info = client.get_instance(instance_id)

    console.print(f"Status:  [bold]{info['status']}[/bold]")
    console.print(f"Region:  {info['region']}")
    if info.get("uptime"):
        console.print(f"Uptime:  {info['uptime']}")


@app.command()
def down() -> None:
    """Destroy your cloud instance."""
    creds = config.load()
    instance_id = creds.get("instance_id")
    if not instance_id:
        err.print("[red]No instance found.[/red]")
        raise SystemExit(1)

    config.require_token()

    with console.status("Destroying instance…"):
        client.destroy_instance(instance_id)

    config.save({"instance_id": None})
    console.print("[green]✓[/green] Instance destroyed.")
