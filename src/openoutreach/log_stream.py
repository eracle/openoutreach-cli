"""Direct mTLS log streaming from the user's droplet."""

from __future__ import annotations

import contextlib
import socket
import ssl
import tempfile
import time
from pathlib import Path

from rich.console import Console

# ── Constants ─────────────────────────────────────────────────────

LOG_PORT = 2376
BACKOFF_CAP_S = 10.0
IDLE_TIMEOUT_S = 60.0
MAX_WAIT_S = 60.0
MAX_RECONNECT_ATTEMPTS = 10


# ── TLS helpers ───────────────────────────────────────────────────


@contextlib.contextmanager
def _tls_context(server_cert: str, client_cert: str, client_key: str):
    """Yield an mTLS ``ssl.SSLContext`` built from PEM strings."""
    pems = [
        ("client-cert", client_cert),
        ("client-key", client_key),
        ("server-ca", server_cert),
    ]
    paths: list[Path] = []
    try:
        for label, content in pems:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=f"-{label}.pem", delete=False,
            )
            tmp.write(content)
            tmp.close()
            path = Path(tmp.name)
            path.chmod(0o600)
            paths.append(path)

        cert_path, key_path, ca_path = paths

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False  # self-signed, CN won't match IP
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_cert_chain(str(cert_path), str(key_path))
        ctx.load_verify_locations(str(ca_path))
        yield ctx
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def _open_connection(ip: str, port: int, ctx: ssl.SSLContext) -> ssl.SSLSocket:
    raw = socket.create_connection((ip, port), timeout=10)
    return ctx.wrap_socket(raw, server_hostname="logs")


# ── Connection with backoff ──────────────────────────────────────


def _connect(
    ip: str,
    port: int,
    ctx: ssl.SSLContext,
    console: Console,
    *,
    max_attempts: int | None = None,
    deadline: float | None = None,
    label: str = "Waiting for log stream...",
) -> ssl.SSLSocket:
    """Retry connecting with exponential backoff.

    Stops when *max_attempts* is exhausted or *deadline* is passed.
    """
    if max_attempts is None and deadline is None:
        raise ValueError("max_attempts or deadline required")
    delay = 1.0
    attempt = 0
    with console.status(label) as spinner:
        while True:
            try:
                return _open_connection(ip, port, ctx)
            except (OSError, ssl.SSLError):
                attempt += 1
                if max_attempts is not None and attempt >= max_attempts:
                    break
                if deadline is not None and time.monotonic() + delay > deadline:
                    break
                spinner.update(f"{label} retry in {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, BACKOFF_CAP_S)

    console.print(f"[red]Could not connect to {ip}:{port}[/red]")
    raise SystemExit(1)


# ── Read loop ────────────────────────────────────────────────────


def _read_loop(sock: ssl.SSLSocket, console: Console) -> None:
    """Print data from *sock* until the connection drops."""
    sock.settimeout(IDLE_TIMEOUT_S)
    while True:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            console.print("[dim]-- idle --[/dim]", highlight=False)
            continue

        if not data:
            raise ConnectionError("stream ended")
        console.print(data.decode(errors="replace"), end="", highlight=False)


# ── Public API ───────────────────────────────────────────────────


def stream_logs(
    droplet_ip: str,
    server_cert: str,
    client_cert: str,
    client_key: str,
    *,
    console: Console | None = None,
    max_wait: float = MAX_WAIT_S,
) -> None:
    """Stream logs from the droplet's mTLS sidecar until Ctrl-C.

    Args:
        droplet_ip: Droplet public IP address.
        server_cert: PEM server certificate (for pinning).
        client_cert: PEM client certificate.
        client_key: PEM client private key.
        console: Rich console for output (default: new Console).
        max_wait: Max seconds to wait for the sidecar (default: 60).
    """
    if console is None:
        console = Console()

    with _tls_context(server_cert, client_cert, client_key) as ctx:
        deadline = time.monotonic() + max_wait
        sock = _connect(droplet_ip, LOG_PORT, ctx, console, deadline=deadline)
        console.print("[green]Connected.[/green]")

        try:
            while True:
                try:
                    _read_loop(sock, console)
                except (OSError, ssl.SSLError, ConnectionError):
                    sock.close()
                    console.print("[yellow][reconnecting...][/yellow]")
                    sock = _connect(
                        droplet_ip, LOG_PORT, ctx, console,
                        max_attempts=MAX_RECONNECT_ATTEMPTS,
                        label="Reconnecting...",
                    )
                    console.print("[green][reconnected][/green]")
        except KeyboardInterrupt:
            console.print("\n[dim]Log stream detached (instance still running).[/dim]")
        finally:
            sock.close()
