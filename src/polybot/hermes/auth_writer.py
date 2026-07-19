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


def _non_codex_projection(auth_store: dict) -> dict:
    projection = {}
    for key, value in auth_store.items():
        if key in {"updated_at", "version"}:
            continue
        if key == "providers":
            projection[key] = {
                provider: state for provider, state in value.items()
                if provider != "openai-codex"
            }
        elif key == "credential_pool":
            non_codex_pool = {
                provider: entries for provider, entries in value.items()
                if provider != "openai-codex"
            }
            if non_codex_pool:
                projection[key] = non_codex_pool
        else:
            projection[key] = value
    return projection


def _validate_codex_only_update(current: dict, requested: dict) -> None:
    current = _validate_store(current)
    requested = _validate_store(requested)
    current_codex = current["providers"].get("openai-codex")
    requested_codex = requested["providers"].get("openai-codex")
    current_pool = current.get("credential_pool", {}).get("openai-codex")
    requested_pool = requested.get("credential_pool", {}).get("openai-codex")
    if (
        not isinstance(current_codex, dict)
        or not isinstance(requested_codex, dict)
        or (current_pool is not None and not isinstance(current_pool, list))
        or not isinstance(requested_pool, list)
        or _non_codex_projection(current) != _non_codex_projection(requested)
    ):
        raise RuntimeError("auth writer rejected non-Codex store mutation")


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
        connection.settimeout(5.0)
        connection.connect(str(_AUTH_WRITER_SOCKET))
        # Once connected, retain the caller's shared auth.lock until the
        # bounded writer exits or replies. A response timeout could release
        # the lock while a disk-stalled writer later overwrites newer state.
        connection.settimeout(None)
        connection.sendall(_HEADER.pack(len(payload)) + payload)
        response = _recv_exact(connection, 2)
    if response != b"OK":
        raise RuntimeError("native auth writer refused persistence")
    return _ROOT_AUTH_FILE


def serve_connection(
        connection: socket.socket,
        *,
        load_auth_store: Callable[..., dict],
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
        current = load_auth_store(_ROOT_AUTH_FILE)
        _validate_codex_only_update(current, store)
        save_auth_store(store, target_path=_ROOT_AUTH_FILE)
    except Exception:
        try:
            connection.sendall(b"ER")
        except OSError:
            pass
        raise
    connection.sendall(b"OK")


def main() -> int:
    from hermes_cli.auth import _load_auth_store, _save_auth_store

    with socket.socket(fileno=os.dup(0)) as connection:
        serve_connection(
            connection,
            load_auth_store=_load_auth_store,
            save_auth_store=_save_auth_store,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
