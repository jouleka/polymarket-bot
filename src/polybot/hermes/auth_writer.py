"""Root-only, one-request persistence boundary for Hermes's native auth store."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import struct
from typing import Callable


_AUTH_WRITER_SOCKET = Path("/run/polymarket-hermes-auth-writer.sock")
_ROOT_AUTH_FILE = Path("/root/.hermes/auth.json")
_MAX_PAYLOAD_BYTES = 1024 * 1024
_HEADER = struct.Struct("!I")
_PEER_CREDENTIALS = struct.Struct("3i")
_ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "active_provider",
    "credential_pool",
    "providers",
    "suppressed_sources",
    "updated_at",
    "version",
})


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError("auth writer connection closed early")
        chunks.extend(chunk)
    return bytes(chunks)


def _validate_store(auth_store: object) -> dict:
    if not isinstance(auth_store, dict) or not set(auth_store) <= _ALLOWED_TOP_LEVEL_KEYS:
        raise RuntimeError("auth writer rejected invalid store shape")
    if not isinstance(auth_store.get("providers"), dict):
        raise RuntimeError("auth writer rejected invalid provider store")
    pool = auth_store.get("credential_pool")
    if pool is not None and not isinstance(pool, dict):
        raise RuntimeError("auth writer rejected invalid credential pool")
    return auth_store


def write_auth_store(auth_store: dict, target_path: Path | None = None) -> Path:
    """Ask the socket-activated writer to perform one native atomic save."""
    target = _ROOT_AUTH_FILE if target_path is None else Path(target_path)
    if target != _ROOT_AUTH_FILE:
        raise RuntimeError("auth writer target violates the reviewed contract")
    store = _validate_store(auth_store)
    payload = json.dumps(store, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise RuntimeError("auth writer payload exceeds the reviewed bound")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20.0)
        connection.connect(str(_AUTH_WRITER_SOCKET))
        connection.sendall(_HEADER.pack(len(payload)) + payload)
        response = _recv_exact(connection, 2)
    if response != b"OK":
        raise RuntimeError("native auth writer refused persistence")
    return _ROOT_AUTH_FILE


def serve_connection(
        connection: socket.socket,
        *,
        save_auth_store: Callable[..., Path],
) -> None:
    """Validate one root peer/request and invoke the pinned native save."""
    try:
        peer = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            _PEER_CREDENTIALS.size,
        )
        _pid, uid, _gid = _PEER_CREDENTIALS.unpack(peer)
        if uid != 0:
            raise RuntimeError("auth writer rejected non-root peer")
        length = _HEADER.unpack(_recv_exact(connection, _HEADER.size))[0]
        if length == 0 or length > _MAX_PAYLOAD_BYTES:
            raise RuntimeError("auth writer rejected payload length")
        payload = _recv_exact(connection, length)
        store = _validate_store(json.loads(payload.decode("utf-8")))
        save_auth_store(store, target_path=_ROOT_AUTH_FILE)
    except Exception:
        try:
            connection.sendall(b"ER")
        except OSError:
            pass
        raise
    connection.sendall(b"OK")


def main() -> int:
    from hermes_cli.auth import _save_auth_store

    with socket.socket(fileno=os.dup(0)) as connection:
        serve_connection(connection, save_auth_store=_save_auth_store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
