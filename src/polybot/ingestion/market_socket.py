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
        # After this many CONSECUTIVE resyncs with no clean delta in between, HALT:
        # a book the resync can never reconcile is itself a fail-loud format-change
        # signal, not something to reconnect against forever.
        max_resyncs=8,
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
        if max_resyncs <= 0:
            raise ValueError("max_resyncs must be > 0")
        self._max_resyncs = max_resyncs

    async def run(self, max_connections=1):
        # max_connections=None => reconnect forever (the 24/7 production mode); a
        # finite count bounds reconnect attempts (used by tests to terminate).
        connections = 0
        failures = 0
        resync_failures = 0  # CONSECUTIVE resyncs with no clean delta between them
        while max_connections is None or connections < max_connections:
            connections += 1
            try:
                transport = await self._connect()
                await transport.send(self._subscribe_message())
                keepalive = asyncio.create_task(self._keepalive(transport))
                resync = False
                resync_detail = None
                try:
                    async for frame in transport:
                        self._dispatch(frame)
                        if self._stream.consume_resync_request():
                            resync_detail = self._stream.consume_resync_detail()
                            if resync_detail is None:
                                raise RuntimeError(
                                    "order book resync requested without divergence detail - HALT"
                                )
                            resync = True
                            break  # leave the receive loop to force a reconnect == resync
                        if self._stream.consume_clean_progress():
                            resync_failures = 0  # a reconciling delta clears the storm counter
                finally:
                    keepalive.cancel()
                    try:
                        await keepalive
                    except asyncio.CancelledError:
                        pass
                if resync:
                    # A mid-stream sequence gap (a book diverged from the venue's
                    # reported top-of-book) marked a book stale. Recover via the
                    # PROVEN resync path: close and reconnect so subscribe-on-connect
                    # pulls a fresh snapshot. Re-subscribing on the SAME live socket
                    # does NOT reliably resnapshot (confirmed live 2026-06-25).
                    resync_failures += 1
                    self._stream.mark_all_stale()  # untrusted until the resync snapshot
                    await self._safe_close(transport)
                    if resync_failures >= self._max_resyncs:
                        raise RuntimeError(
                            f"order book failed to resync after {resync_failures} consecutive "
                            f"attempts - HALT (irreconcilable divergence / likely format change); "
                            f"{self._format_divergence(resync_detail)}"
                        )
                    # Back off so a persistently re-diverging book can never become a
                    # zero-delay reconnect hot-loop against the gateway; a single
                    # transient gap is just the floor delay and is reset by the next
                    # clean delta (consume_clean_progress above).
                    await self._sleep(self._backoff_delay(resync_failures))
                    failures = 0  # the connection was streaming; disconnect-backoff resets
                    continue
                failures = 0          # a clean close resets the backoff
                resync_failures = 0
                await self._safe_close(transport)  # don't leak a cleanly-exhausted transport
            except self._reconnect_on:
                self._stream.mark_all_stale()  # books untrusted until the resync snapshot
                failures += 1
                resync_failures = 0  # a real disconnect is not a resync storm
                await self._sleep(self._backoff_delay(failures))

    async def _safe_close(self, transport):
        """Close a transport we are abandoning for a resync. Tolerates a transport
        with no ``close`` (the in-memory test double) and a close that fails because
        the socket is already going down — the reconnect resyncs regardless."""
        close = getattr(transport, "close", None)
        if close is None:
            return
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass  # CancelledError (BaseException) is not swallowed

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

    def _format_divergence(self, detail):
        return (
            f"shard_assets={len(self._asset_ids)}; "
            f"asset_id={self._bounded_repr(detail.asset_id)}; "
            f"market={self._bounded_repr(detail.market)}; "
            f"timestamp={self._bounded_repr(detail.timestamp)}; "
            f"reconstructed={self._bounded_value(detail.reconstructed_bid)}/"
            f"{self._bounded_value(detail.reconstructed_ask)}; "
            f"venue={self._bounded_value(detail.venue_bid)}/"
            f"{self._bounded_value(detail.venue_ask)}"
        )

    @staticmethod
    def _bounded_repr(value):
        return repr(value if value is None else str(value)[:128])

    @staticmethod
    def _bounded_value(value):
        return "None" if value is None else str(value)[:64]

    def _subscribe_message(self):
        return json.dumps({"type": "market", "assets_ids": self._asset_ids})
