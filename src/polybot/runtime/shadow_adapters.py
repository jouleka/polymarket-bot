"""Production network adapters owned by the POL-17 composition root."""

from __future__ import annotations

import fcntl
import os
import socket

import httpx

from polybot.ingestion.gamma import normalize_market
from polybot.resolution.rpc import JsonRpcClient, JsonRpcResolutionProvider
from polybot.resolution.errors import ResolutionUnavailable
from polybot.runtime.discovery import discover_universe
from polybot.runtime.registry_provider import RegistryRefreshUnavailable


class SingletonLock:
    """Process-lifetime nonblocking advisory lock."""

    def __init__(self, path):
        self._path = path
        self._fd = None

    def acquire(self):
        if self._fd is not None:
            raise RuntimeError("shadow runtime lock is already acquired")
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError("shadow runtime is already running") from exc
        self._fd = fd

    def release(self):
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class SystemdReadiness:
    """Minimal sd_notify client; absence of systemd is a deliberate no-op."""

    def __init__(self, *, environ=None, socket_factory=socket.socket):
        environ = os.environ if environ is None else environ
        self._address = environ.get("NOTIFY_SOCKET")
        self._socket_factory = socket_factory

    def ready(self):
        self._notify(b"READY=1")

    def stopping(self):
        self._notify(b"STOPPING=1")

    def status(self, message):
        if not isinstance(message, str) or not message or "\n" in message:
            raise ValueError("systemd status must be one non-empty line")
        self._notify(("STATUS=" + message).encode())

    def _notify(self, payload):
        if not self._address:
            return
        address = self._address
        if address.startswith("@"):
            address = "\0" + address[1:]
        elif not address.startswith("/"):
            raise ValueError("NOTIFY_SOCKET must be an absolute or abstract Unix socket")
        with self._socket_factory(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(payload)


class StopAwareResolutionProvider:
    """Prevent a serialized resolution worker from starting RPCs after shutdown."""

    def __init__(self, provider, should_stop):
        self.provider_id = provider.provider_id
        self._provider = provider
        self._should_stop = should_stop

    def _call(self, name, *args):
        if self._should_stop():
            raise ResolutionUnavailable("resolution runtime is stopping")
        return getattr(self._provider, name)(*args)

    def chain_id(self):
        return self._call("chain_id")

    def latest_block(self):
        return self._call("latest_block")

    def block_hash(self, block_number):
        return self._call("block_hash", block_number)

    def observe(self, subject, block_number):
        return self._call("observe", subject, block_number)

    def verify_terminal(self, terminal):
        return self._call("verify_terminal", terminal)


class _GammaSnapshotFetcher:
    def __init__(self, config, client, *, owned):
        self._config = config
        self._client = client
        self._owned = owned
        self._condition_ids = None

    def __call__(self):
        if self._condition_ids is None:
            params = {
                "limit": self._config.universe_max_markets * 3,
                "closed": "false",
                "active": "true",
                "order": "volume24hr",
                "ascending": "false",
            }
            candidates = self._get_list("/markets", params)
            selected_tokens = frozenset(discover_universe(
                lambda _params: candidates, self._config
            ))
            markets = []
            for row in candidates:
                try:
                    tokens = {outcome.token_id for outcome in normalize_market(row).outcomes}
                except Exception:
                    continue
                if tokens and tokens <= selected_tokens:
                    markets.append(row)
            self._condition_ids = tuple(row["conditionId"] for row in markets)
        else:
            markets = self._get_list(
                "/markets", {"condition_ids": self._condition_ids}
            )
        event_ids = tuple(dict.fromkeys(
            str(event["id"])
            for market in markets
            for event in market["events"]
        ))
        events = self._get_list("/events", {"id": event_ids})
        return markets, events

    def _get_list(self, path, params):
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
        except httpx.TransportError as exc:
            raise RegistryRefreshUnavailable("Gamma transport unavailable") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 or 500 <= status < 600:
                raise RegistryRefreshUnavailable("Gamma server unavailable") from exc
            raise
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError(f"Gamma {path} response must be a list")
        return payload

    def close(self):
        if self._owned:
            self._client.close()


def make_gamma_snapshot_fetch(config, *, client=None, timeout=30.0):
    """Return a callable coherent snapshot that freezes its first selected universe."""
    owned = client is None
    if client is None:
        client = httpx.Client(
            base_url=config.gamma_url,
            timeout=timeout,
            headers={"user-agent": "polybot/0.1"},
        )
    return _GammaSnapshotFetcher(config, client, owned=owned)


def make_resolution_providers(config, *, client_factory=httpx.Client):
    """Build two independently owned, timeout-bounded read-only RPC providers."""
    clients = []
    try:
        providers = []
        for provider_config in config.polygon_providers:
            client = client_factory(timeout=config.rpc_timeout_seconds)
            clients.append(client)
            providers.append(JsonRpcResolutionProvider(
                provider_config.provider_id,
                JsonRpcClient(provider_config.url, client),
            ))
    except Exception:
        for client in reversed(clients):
            client.close()
        raise

    def close():
        for client in reversed(clients):
            client.close()

    return tuple(providers), close
