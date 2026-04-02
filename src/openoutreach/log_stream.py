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
COUNTDOWN_S = 15
MAX_RECONNECT_ATTEMPTS = 10


# ── TLS helpers ───────────────────────────────────────────────────


@contextlib.contextmanager
def _tls_context(server_cert: str, client_cert: str, client_key: str):
    """Yield an mTLS ``ssl.SSLContext`` built from PEM strings.

    Writes certs to temporary files (required by the ``ssl`` module),
    restricted to ``0o600``, and cleans them up on exit.
    """
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
        ctx.verify_mode = ssl.CERT_REQUIRED  # still verify the cert chain
        ctx.load_cert_chain(str(cert_path), str(key_path))
        ctx.load_verify_locations(str(ca_path))
        yield ctx
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def _open_connection(ip: str, port: int, ctx: ssl.SSLContext) -> ssl.SSLSocket:
    """Open a single mTLS connection to *ip*:*port*."""
    raw = socket.create_connection((ip, port), timeout=10)
    return ctx.wrap_socket(raw, server_hostname="logs")


# ── Backoff helper ────────────────────────────────────────────────


class Backoff:
    """Exponential backoff with a cap, resettable."""

    def __init__(self, initial: float = 1.0, cap: float = BACKOFF_CAP_S):
        self._initial = initial
        self._cap = cap
        self._current = initial

    def wait(self) -> None:
        time.sleep(self._current)
        self._current = min(self._current * 2, self._cap)

    @property
    def current(self) -> float:
        return self._current

    def reset(self) -> None:
        self._current = self._initial


# ── Connection loop ───────────────────────────────────────────────


def _wait_for_connection(
    ip: str,
    port: int,
    ctx: ssl.SSLContext,
    console: Console,
    deadline: float | None,
) -> ssl.SSLSocket:
    """Retry until the sidecar accepts a connection, or *deadline* expires."""
    backoff = Backoff()
    with console.status("Waiting for log stream...") as spinner:
        while True:
            try:
                return _open_connection(ip, port, ctx)
            except (OSError, ssl.SSLError):
                if deadline is not None and time.monotonic() + backoff.current > deadline:
                    console.print(
                        f"[red]Could not connect to log stream at {ip}:{port}[/red]",
                    )
                    raise SystemExit(1)
                spinner.update(f"Waiting for log stream... retry in {backoff.current:.0f}s")
                backoff.wait()


def _reconnect(
    ip: str, port: int, ctx: ssl.SSLContext, console: Console,
) -> ssl.SSLSocket:
    """Re-establish a dropped connection with backoff.

    Gives up after ``MAX_RECONNECT_ATTEMPTS`` consecutive failures,
    which likely means the droplet was destroyed.
    """
    console.print("[yellow][reconnecting...][/yellow]")
    backoff = Backoff()
    for _ in range(MAX_RECONNECT_ATTEMPTS):
        try:
            sock = _open_connection(ip, port, ctx)
            console.print("[green][reconnected][/green]")
            return sock
        except (OSError, ssl.SSLError):
            backoff.wait()
    console.print("[red]Lost connection to instance.[/red]")
    raise SystemExit(1)


# ── Read loop ─────────────────────────────────────────────────────


def _read_loop(sock: ssl.SSLSocket, console: Console) -> None:
    """Print data from *sock* until the connection drops.

    Prints an idle indicator if nothing arrives for ``IDLE_TIMEOUT_S``.
    Raises ``ConnectionError`` when the stream ends or breaks.
    """
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


# ── Countdown ─────────────────────────────────────────────────────


def _countdown(seconds: int, console: Console) -> None:
    """Visual countdown giving cloud-init time to build the sidecar."""
    with console.status("") as spinner:
        for remaining in range(seconds, 0, -1):
            spinner.update(f"Connecting to log stream in {remaining}s...")
            time.sleep(1)


# ── Public API ────────────────────────────────────────────────────


def stream_logs(
    droplet_ip: str,
    server_cert: str,
    client_cert: str,
    client_key: str,
    *,
    console: Console | None = None,
    max_wait: float | None = None,
    countdown: int = COUNTDOWN_S,
) -> None:
    """Stream logs from the droplet's mTLS sidecar until Ctrl-C.

    Args:
        droplet_ip: Droplet public IP address.
        server_cert: PEM server certificate (for pinning).
        client_cert: PEM client certificate.
        client_key: PEM client private key.
        console: Rich console for output (default: new Console).
        max_wait: Max seconds to wait for the sidecar. ``None`` = forever.
        countdown: Seconds to wait before the first attempt, giving
            cloud-init time to build the sidecar image.
    """
    if console is None:
        console = Console()

    with _tls_context(server_cert, client_cert, client_key) as ctx:
        if countdown > 0:
            _countdown(countdown, console)

        deadline = None if max_wait is None else time.monotonic() + max_wait
        sock = _wait_for_connection(droplet_ip, LOG_PORT, ctx, console, deadline)
        console.print("[green]Connected.[/green]")

        try:
            while True:
                try:
                    _read_loop(sock, console)
                except (OSError, ssl.SSLError, ConnectionError):
                    sock.close()
                    sock = _reconnect(droplet_ip, LOG_PORT, ctx, console)
        except KeyboardInterrupt:
            console.print("\n[dim]Log stream detached (instance still running).[/dim]")
        finally:
            sock.close()
