"""Strict local RPC boundary for the propose-only facade."""

from __future__ import annotations

import asyncio
from collections import deque
import errno
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import socket
import time
import uuid
from decimal import Decimal, InvalidOperation


PROTOCOL_VERSION = 1
APPROVED_METHODS = frozenset({
    "propose_trade", "get_market", "get_book", "get_ledger", "get_flags",
})
log = logging.getLogger("polybot.hermes.rpc")


class RpcProtocolError(ValueError):
    """Untrusted request violates the local proposal protocol."""


class RpcRemoteError(RuntimeError):
    """The POL-17 endpoint refused or could not serve a bounded request."""

    def __init__(self, code):
        self.code = code
        super().__init__(f"proposal RPC failed closed: {code}")


def _validated_socket_path(path):
    candidate = Path(path)
    try:
        encoded = os.fsencode(candidate)
    except UnicodeEncodeError as exc:
        raise ValueError("proposal socket path must be valid filesystem text") from exc
    if not candidate.is_absolute() or len(encoded) > 100:
        raise ValueError("proposal socket path must be absolute and at most 100 bytes")
    return candidate


class ProposalRateLimiter:
    """Process-local fixed-window admission gate for untrusted proposal attempts."""

    def __init__(self, maximum, window_seconds, *, clock=time.monotonic):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ValueError("proposal rate maximum must be a positive integer")
        if (type(window_seconds) not in (int, float)
                or not math.isfinite(window_seconds) or window_seconds <= 0):
            raise ValueError("proposal rate window must be finite and positive")
        if not callable(clock):
            raise TypeError("proposal rate clock must be callable")
        self._maximum = maximum
        self._window = window_seconds
        self._clock = clock
        self._attempts = deque()

    def __call__(self):
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise RpcProtocolError("proposal rate clock is invalid")
        cutoff = now - self._window
        while self._attempts and self._attempts[0] <= cutoff:
            self._attempts.popleft()
        if len(self._attempts) >= self._maximum:
            raise RpcProtocolError("proposal rate limit exceeded")
        self._attempts.append(now)


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RpcProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class ProposalRpcDispatcher:
    """Decode, validate, and explicitly route one request to one facade method."""

    def __init__(self, facade, *, max_request_bytes=65_536, proposal_gate=None):
        if (isinstance(max_request_bytes, bool) or not isinstance(max_request_bytes, int)
                or max_request_bytes <= 0):
            raise ValueError("max_request_bytes must be a positive integer")
        self._facade = facade
        self._max_request_bytes = max_request_bytes
        if proposal_gate is not None and not callable(proposal_gate):
            raise TypeError("proposal_gate must be callable")
        self._proposal_gate = proposal_gate

    def handle(self, request):
        if not isinstance(request, bytes):
            raise TypeError("RPC request must be bytes")
        if len(request) > self._max_request_bytes:
            raise RpcProtocolError("RPC request exceeds byte limit")
        if not request.endswith(b"\n") or b"\n" in request[:-1]:
            raise RpcProtocolError("RPC request must contain exactly one newline frame")
        try:
            payload = json.loads(
                request[:-1].decode("utf-8"), object_pairs_hook=_object_without_duplicates,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RpcProtocolError("RPC request is not strict UTF-8 JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {
                "version", "id", "method", "params"}:
            raise RpcProtocolError("RPC request envelope keys are invalid")
        if payload["version"] != PROTOCOL_VERSION or isinstance(payload["version"], bool):
            raise RpcProtocolError("RPC protocol version is unsupported")
        request_id = payload["id"]
        if (not isinstance(request_id, str) or not request_id
                or len(request_id) > 128 or any(ord(char) < 32 for char in request_id)):
            raise RpcProtocolError("RPC request id is invalid")
        method = payload["method"]
        if method not in APPROVED_METHODS:
            raise RpcProtocolError("RPC method is not approved")
        params = payload["params"]
        if not isinstance(params, dict):
            raise RpcProtocolError("RPC params must be an object")

        result = self._dispatch(method, self._validate_params(method, params))
        response = {"version": PROTOCOL_VERSION, "id": request_id, "result": result}
        return (json.dumps(
            response, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ) + "\n").encode("utf-8")

    def _dispatch(self, method, params):
        if method == "propose_trade":
            if self._proposal_gate is not None:
                self._proposal_gate()
            return self._facade.propose_trade(**params)
        if method == "get_market":
            return self._facade.get_market(**params)
        if method == "get_book":
            return self._facade.get_book(**params)
        if method == "get_ledger":
            return self._facade.get_ledger(**params)
        if method == "get_flags":
            return self._facade.get_flags(**params)
        raise AssertionError("approved method has no explicit dispatcher")

    @staticmethod
    def _validate_params(method, params):
        values = dict(params)
        if method == "get_book":
            _exact_keys(values, {"token_id"})
            values["token_id"] = _text(values["token_id"], "token_id", 128)
            return values
        if method == "get_flags":
            _exact_keys(values, set())
            return values
        if method == "get_market":
            _exact_keys(
                values, set(), {"condition_id", "token_id", "offset", "limit"},
            )
            for name in ("condition_id", "token_id"):
                if name in values:
                    values[name] = _text(values[name], name, 128)
            for name in ("offset", "limit"):
                if name in values:
                    values[name] = _integer(values[name], name, minimum=0 if name == "offset" else 1)
            return values
        if method == "get_ledger":
            _exact_keys(values, set(), {"category", "limit"})
            if "category" in values:
                values["category"] = _text(values["category"], "category", 64)
            if "limit" in values:
                values["limit"] = _integer(values["limit"], "limit", minimum=1)
            return values
        if method == "propose_trade":
            required = {
                "intent_id", "token_id", "condition_id", "event_id", "side",
                "target_price", "max_price", "size_usd_suggestion", "p", "p_confidence",
            }
            optional = {"resolution_summary", "thesis", "citations"}
            _exact_keys(values, required, optional)
            for name, maximum in (
                ("intent_id", 128), ("token_id", 128), ("condition_id", 128),
                ("event_id", 128),
            ):
                values[name] = _text(values[name], name, maximum)
            values["side"] = _text(values["side"], "side", 8)
            if values["side"] != "BUY":
                raise RpcProtocolError("proposal side must be BUY")
            for name in ("target_price", "max_price", "p", "p_confidence"):
                values[name] = _decimal(values[name], name, lower=Decimal(0), upper=Decimal(1))
            values["size_usd_suggestion"] = _decimal(
                values["size_usd_suggestion"], "size_usd_suggestion",
                lower=Decimal(0), upper=None, lower_inclusive=False,
            )
            for name, maximum in (("resolution_summary", 4096), ("thesis", 8192)):
                if name in values:
                    values[name] = _text(values[name], name, maximum, allow_empty=True)
            if "citations" in values:
                citations = values["citations"]
                if not isinstance(citations, list) or len(citations) > 32:
                    raise RpcProtocolError("citations must be a bounded list")
                values["citations"] = tuple(
                    _text(citation, "citation", 2048) for citation in citations
                )
            return values
        raise AssertionError("approved method has no parameter schema")


def _exact_keys(values, required, optional=frozenset()):
    keys = set(values)
    missing = required - keys
    extra = keys - required - set(optional)
    if missing or extra:
        raise RpcProtocolError("RPC method parameters do not match the exact schema")


def _text(value, name, maximum, *, allow_empty=False):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RpcProtocolError(f"{name} must be a bounded exact string") from exc
    if (not isinstance(value, str) or (not value and not allow_empty)
            or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise RpcProtocolError(f"{name} must be a bounded exact string")
    return value


def _integer(value, name, *, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RpcProtocolError(f"{name} must be an integer >= {minimum}")
    return value


def _decimal(value, name, *, lower, upper, lower_inclusive=True):
    if (not isinstance(value, str) or len(value) > 128
            or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None):
        raise RpcProtocolError(f"{name} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise RpcProtocolError(f"{name} must be an exact decimal string") from exc
    if not parsed.is_finite():
        raise RpcProtocolError(f"{name} must be a finite decimal string")
    lower_ok = parsed >= lower if lower_inclusive else parsed > lower
    if not lower_ok or (upper is not None and parsed > upper):
        raise RpcProtocolError(f"{name} decimal string is outside its allowed range")
    return parsed


class ProposalRpcServer:
    """One-request-per-connection supervised Unix listener."""

    def __init__(self, path, dispatcher, *, runtime_ready, socket_group=None,
                 socket_mode=0o660, request_timeout_seconds=2.0,
                 max_concurrent_requests=8, max_request_bytes=65_536,
                 max_response_bytes=262_144):
        self._path = _validated_socket_path(path)
        if not isinstance(dispatcher, ProposalRpcDispatcher):
            raise TypeError("dispatcher must be a ProposalRpcDispatcher")
        if not callable(runtime_ready):
            raise TypeError("runtime_ready must be callable")
        if (socket_group is not None
                and (isinstance(socket_group, bool) or not isinstance(socket_group, int)
                     or socket_group < 0)):
            raise ValueError("socket_group must be a non-negative gid")
        if (isinstance(socket_mode, bool) or not isinstance(socket_mode, int)
                or socket_mode != 0o660):
            raise ValueError("proposal socket mode is pinned to 0660")
        if (type(request_timeout_seconds) not in (int, float)
                or request_timeout_seconds <= 0):
            raise ValueError("request timeout must be positive")
        for name, value in (
            ("max_concurrent_requests", max_concurrent_requests),
            ("max_request_bytes", max_request_bytes),
            ("max_response_bytes", max_response_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._dispatcher = dispatcher
        self._runtime_ready = runtime_ready
        self._socket_group = socket_group
        self._socket_mode = socket_mode
        self._request_timeout = request_timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._started = asyncio.Event()
        self._socket_identity = None

    @property
    def started(self):
        return self._started

    async def run(self):
        self._prepare_socket_directory()
        try:
            existing = self._path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(existing.st_mode):
                raise RuntimeError("proposal path collision with existing non-socket")
            self._remove_proven_stale_socket(existing)
        server = await asyncio.start_unix_server(
            self._accept, path=self._path, limit=self._max_request_bytes + 1,
        )
        try:
            os.chmod(self._path, self._socket_mode)
            if self._socket_group is not None:
                os.chown(self._path, -1, self._socket_group)
            socket_stat = self._path.lstat()
            if (not stat.S_ISSOCK(socket_stat.st_mode)
                    or stat.S_IMODE(socket_stat.st_mode) != self._socket_mode
                    or (self._socket_group is not None
                        and socket_stat.st_gid != self._socket_group)):
                raise RuntimeError("proposal socket ownership or mode verification failed")
            self._socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
            self._started.set()
            await server.serve_forever()
            raise RuntimeError("proposal RPC listener returned unexpectedly")
        finally:
            server.close()
            await server.wait_closed()
            self._unlink_owned_socket()

    def _prepare_socket_directory(self):
        parent = self._path.parent
        parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        observed = parent.lstat()
        if (not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or stat.S_IMODE(observed.st_mode) & 0o022
                or (self._socket_group is not None
                    and observed.st_gid != self._socket_group)):
            raise RuntimeError("proposal socket directory is not securely owned")

    async def _accept(self, reader, writer):
        async with self._semaphore:
            try:
                await asyncio.wait_for(
                    self._serve_one(reader, writer), timeout=self._request_timeout,
                )
            except TimeoutError:
                await self._write_error(writer, "request_timeout")
            except Exception:
                log.exception("isolated proposal RPC client failure")
                await self._write_error(writer, "request_rejected")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _serve_one(self, reader, writer):
        frame = await reader.readline()
        if not frame:
            raise RpcProtocolError("RPC client closed before a request")
        if len(frame) > self._max_request_bytes:
            raise RpcProtocolError("RPC request exceeds byte limit")
        ready = self._runtime_ready()
        if not isinstance(ready, bool):
            raise TypeError("runtime readiness must be boolean")
        if not ready:
            await self._write_error(writer, "runtime_not_ready")
            return
        response = self._dispatcher.handle(frame)
        if len(response) > self._max_response_bytes:
            raise RuntimeError("proposal RPC response exceeds byte limit")
        writer.write(response)
        await writer.drain()

    @staticmethod
    async def _write_error(writer, code):
        if writer.is_closing():
            return
        payload = {
            "version": PROTOCOL_VERSION,
            "id": None,
            "error": {"code": code, "message": "proposal request failed closed"},
        }
        writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        try:
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    def _unlink_owned_socket(self):
        if self._socket_identity is None:
            return
        try:
            current = self._path.lstat()
        except FileNotFoundError:
            return
        identity = (current.st_dev, current.st_ino)
        if stat.S_ISSOCK(current.st_mode) and identity == self._socket_identity:
            self._path.unlink()

    def _remove_proven_stale_socket(self, observed):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(self._path))
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                raise RuntimeError("existing proposal socket cannot be proven stale") from exc
        else:
            raise RuntimeError("proposal socket is already accepting connections")
        finally:
            probe.close()
        try:
            current = self._path.lstat()
        except FileNotFoundError:
            return
        if (not stat.S_ISSOCK(current.st_mode)
                or (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino)):
            raise RuntimeError("proposal socket changed during stale recovery")
        self._path.unlink()


class ProposalRpcClient:
    """Capability-minimal async client used by the stdio MCP bridge."""

    def __init__(self, path, *, request_id=None, timeout_seconds=3.0,
                 max_response_bytes=262_144):
        self._path = _validated_socket_path(path)
        self._request_id = (lambda: uuid.uuid4().hex) if request_id is None else request_id
        if not callable(self._request_id):
            raise TypeError("request_id must be callable")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("client timeout must be positive")
        if (isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int)
                or max_response_bytes <= 0):
            raise ValueError("max_response_bytes must be a positive integer")
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def call(self, method, params):
        if method not in APPROVED_METHODS:
            raise RpcProtocolError("RPC method is not approved")
        if not isinstance(params, dict):
            raise RpcProtocolError("RPC params must be an object")
        request_id = self._request_id()
        if not isinstance(request_id, str) or not request_id:
            raise RuntimeError("RPC request ID factory returned an invalid value")
        request = (json.dumps({
            "version": PROTOCOL_VERSION,
            "id": request_id,
            "method": method,
            "params": params,
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()

        async def exchange():
            reader, writer = await asyncio.open_unix_connection(
                self._path, limit=self._max_response_bytes + 1,
            )
            try:
                writer.write(request)
                await writer.drain()
                response = await reader.readline()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            return response

        try:
            response = await asyncio.wait_for(exchange(), timeout=self._timeout)
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise RpcRemoteError("transport_unavailable") from exc
        if not response or len(response) > self._max_response_bytes:
            raise RpcRemoteError("invalid_response")
        try:
            payload = json.loads(
                response.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RpcProtocolError) as exc:
            raise RpcRemoteError("invalid_response") from exc
        if (not isinstance(payload, dict) or payload.get("version") != PROTOCOL_VERSION
                or payload.get("id") not in (request_id, None)):
            raise RpcRemoteError("invalid_response")
        if set(payload) == {"version", "id", "result"} and payload["id"] == request_id:
            return payload["result"]
        if set(payload) == {"version", "id", "error"} and isinstance(payload["error"], dict):
            code = payload["error"].get("code")
            if isinstance(code, str) and code:
                raise RpcRemoteError(code)
        raise RpcRemoteError("invalid_response")
