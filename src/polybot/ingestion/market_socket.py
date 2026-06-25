"""Async CLOB market-channel socket loop.

Drives a transport (anything that is an async-iterator of raw text frames and has
``async send``) — the real ``websockets`` connection satisfies this directly.
On each (re)connect it subscribes to the configured assets, which makes the
server resend a fresh ``book`` snapshot — i.e. reconnect == resync. Decoded
frames are handed to a MarketStream dispatcher.

Resilience:
- a disconnect (any ``reconnect_on`` exception) triggers reconnect with
  exponential backoff (don't blind-retry / hot-loop the gateway);
- a single malformed frame (bad JSON / missing keys) is skipped, not fatal;
- an unknown ``event_type`` is a format change and propagates as a HALT (it is
  deliberately NOT in the skip set), surfacing out of ``run``.

Keepalive: the venue's market channel uses a CLIENT-driven keepalive — the client
sends a bare ``"PING"`` text frame on an interval and the server replies bare
``"PONG"`` (verified live; the server never initiates pings). A per-connection
keepalive task sends those PINGs so a long-running, idle socket is not dropped;
the ``"PONG"`` reply is non-JSON and is dropped by the malformed-frame skip.
"""

import asyncio
import json
from json import JSONDecodeError


class MarketSocket:
    def __init__(
        self,
        connect,
        stream,
        asset_ids,
        *,
        # Real-transport callers MUST pass transport.WS_RECONNECT_ON: a live
        # disconnect is websockets.ConnectionClosed, which is NOT an OSError. Keep
        # these Exception-rooted, never BaseException — _keepalive's
        # `except self._reconnect_on` must not swallow CancelledError.
        reconnect_on=(OSError,),
        sleep=asyncio.sleep,
        backoff_base=0.5,
        backoff_cap=30.0,
        ping_interval=10.0,
    ):
        self._connect = connect  # async () -> transport (async-iterable + .send)
        self._stream = stream
        self._asset_ids = list(asset_ids)
        self._reconnect_on = tuple(reconnect_on)
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        if ping_interval <= 0:
            raise ValueError("ping_interval must be > 0 (a non-positive value hot-loops the keepalive)")
        self._ping_interval = ping_interval

    async def run(self, max_connections=1):
        connections = 0
        failures = 0
        while connections < max_connections:
            connections += 1
            try:
                transport = await self._connect()
                await transport.send(self._subscribe_message())
                keepalive = asyncio.create_task(self._keepalive(transport))
                try:
                    async for frame in transport:
                        self._dispatch(frame)
                finally:
                    keepalive.cancel()
                    try:
                        await keepalive
                    except asyncio.CancelledError:
                        pass
                failures = 0  # a clean close resets the backoff
            except self._reconnect_on:
                self._stream.mark_all_stale()  # books untrusted until the resync snapshot
                failures += 1
                await self._sleep(self._backoff_delay(failures))

    async def _keepalive(self, transport):
        """Client-driven WS keepalive: the venue expects the CLIENT to send a bare
        ``"PING"`` text frame (~every 10s) and replies with a bare ``"PONG"``
        (verified live 2026-06-25; the server never initiates pings, so this is a
        sender, not a pong-responder). The ``"PONG"`` reply is not JSON, so the
        receive loop drops it via the malformed-frame skip in ``_dispatch`` — it
        never reaches ``stream.ingest`` and so cannot HALT.

        Best-effort: a failed send means the socket is going down, which the
        receive loop will observe and route through reconnect+``mark_all_stale``.
        The keepalive must not re-raise that through ``run``'s teardown and have it
        mistaken for (or mask) the receive loop's own disconnect handling, so it
        swallows ``reconnect_on`` errors and stops. (Detecting a *half-open* socket
        — where send buffers and recv stalls — is out of scope here; that is the
        stale-mark watchdog's job, per DECISIONS-S0.)
        """
        try:
            while True:
                await asyncio.sleep(self._ping_interval)
                await transport.send("PING")
        except self._reconnect_on:
            return

    def _dispatch(self, frame):
        try:
            payload = json.loads(frame)
        except JSONDecodeError:
            return  # poison frame: skip, keep the connection alive
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            try:
                self._stream.ingest(event)
            except KeyError:
                continue  # malformed event (missing keys): skip just this one
            # NB: a ValueError from ingest (unknown event_type = format change) is
            # intentionally NOT caught -> it propagates out of run() as a HALT.

    def _backoff_delay(self, failures):
        return min(self._backoff_cap, self._backoff_base * (2 ** (failures - 1)))

    def _subscribe_message(self):
        return json.dumps({"type": "market", "assets_ids": self._asset_ids})
