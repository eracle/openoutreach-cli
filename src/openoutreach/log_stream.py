"""Direct mTLS log streaming from the user's droplet."""

from __future__ import annotations

import os
import socket
import ssl
import tempfile
import time
from pathlib import Path

LOG_PORT = 8443
MAX_RETRIES = 5


def _write_temp_pem(pem_data: str) -> Path:
    """Write PEM data to a temp file with owner-only permissions (0o600).

    Returns the path. Caller is responsible for cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, pem_data.encode())
    finally:
        os.close(fd)
    return Path(path)


def _build_ssl_context(
    server_cert: str,
    client_cert: str,
    client_key: str,
) -> ssl.SSLContext:
    """Build an mTLS SSL context using PEM strings.

    - server_cert: pinned CA to verify the droplet (loaded from string via cadata)
    - client_cert + client_key: our identity (loaded from temp files because
      ssl.SSLContext.load_cert_chain requires file paths)
    """
    client_cert_path = _write_temp_pem(client_cert)
    client_key_path = _write_temp_pem(client_key)

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=server_cert)
        ctx.load_cert_chain(certfile=str(client_cert_path), keyfile=str(client_key_path))
    finally:
        client_cert_path.unlink(missing_ok=True)
        client_key_path.unlink(missing_ok=True)

    return ctx


def stream_logs(
    droplet_ip: str,
    server_cert: str,
    client_cert: str,
    client_key: str,
) -> None:
    """Connect to the droplet via mTLS and print log lines until interrupted.

    Retries with exponential backoff if the sidecar isn't ready yet.
    Raises ConnectionError after all retries are exhausted.
    """
    ctx = _build_ssl_context(server_cert, client_cert, client_key)

    for attempt in range(MAX_RETRIES):
        try:
            raw_sock = socket.create_connection((droplet_ip, LOG_PORT), timeout=10)
            sock = ctx.wrap_socket(raw_sock, server_hostname="logs")
            break
        except (ConnectionRefusedError, OSError, ssl.SSLError):
            if attempt == MAX_RETRIES - 1:
                raise ConnectionError(
                    f"Could not connect to {droplet_ip}:{LOG_PORT} "
                    f"after {MAX_RETRIES} attempts"
                )
            delay = 2 ** attempt
            print(f"Waiting for log stream... retrying in {delay}s")
            time.sleep(delay)

    try:
        for line in sock.makefile():
            print(line, end="")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
