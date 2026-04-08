"""Direct mTLS log streaming from the user's droplet sidecar."""

from __future__ import annotations

import ssl
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from rich.console import Console

SIDECAR_PORT = 2376
BACKOFF_CAP_S = 10.0
MAX_WAIT_S = 60.0


@contextmanager
def _mtls_context(server_cert: str, client_cert: str, client_key: str):
    """Yield an ``ssl.SSLContext`` configured for mTLS from PEM strings.

    Writes certs to owner-only temp files (httpx needs paths), cleans up on exit.
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
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_cert_chain(str(cert_path), str(key_path))
        ctx.load_verify_locations(str(ca_path))
        yield ctx
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def _sidecar_url(ip: str, path: str) -> str:
    return f"https://{ip}:{SIDECAR_PORT}{path}"


def _retry(fn, *, max_wait=MAX_WAIT_S, errors=(httpx.ConnectError, httpx.RemoteProtocolError)):
    """Call *fn* repeatedly with exponential backoff until it succeeds or *max_wait* expires."""
    deadline = time.monotonic() + max_wait
    delay = 1.0
    while True:
        try:
            return fn()
        except errors:
            if time.monotonic() + delay > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, BACKOFF_CAP_S)


def stream_logs(
    droplet_ip: str,
    server_cert: str,
    client_cert: str,
    client_key: str,
    *,
    console: Console | None = None,
    max_wait: float = MAX_WAIT_S,
) -> None:
    """Stream logs from the droplet's mTLS sidecar until Ctrl-C."""
    if console is None:
        console = Console()

    with _mtls_context(server_cert, client_cert, client_key) as ctx:

        def _connect():
            with httpx.stream(
                "GET",
                _sidecar_url(droplet_ip, "/logs"),
                verify=ctx,
                timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
            ) as resp:
                resp.raise_for_status()
                console.print("[green]Connected.[/green]")
                try:
                    for chunk in resp.iter_text():
                        console.print(chunk, end="", highlight=False)
                except KeyboardInterrupt:
                    console.print("\n[dim]Log stream detached (instance still running).[/dim]")
                    return

            console.print("\n[yellow]Instance stopped.[/yellow]")

        _retry(
            _connect,
            max_wait=max_wait,
            errors=(httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.HTTPStatusError),
        )


def upload_db(
    droplet_ip: str,
    server_cert: str,
    client_cert: str,
    client_key: str,
    db_path: Path,
    *,
    max_wait: float = MAX_WAIT_S,
) -> None:
    """Upload db.sqlite3 directly to the sidecar via mTLS POST."""
    with _mtls_context(server_cert, client_cert, client_key) as ctx:

        def _post():
            with db_path.open("rb") as f:
                resp = httpx.post(
                    _sidecar_url(droplet_ip, "/db-upload"),
                    content=f,
                    headers={"Content-Length": str(db_path.stat().st_size)},
                    verify=ctx,
                    timeout=120,
                )
            resp.raise_for_status()

        _retry(_post, max_wait=max_wait)
