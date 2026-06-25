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
        reconnect_on=(OSError,),
        sleep=asyncio.sleep,
        backoff_base=0.5,
        backoff_cap=30.0,
    ):
        self._connect = connect  # async () -> transport (async-iterable + .send)
        self._stream = stream
        self._asset_ids = list(asset_ids)
        self._reconnect_on = tuple(reconnect_on)
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    async def run(self, max_connections=1):
        connections = 0
        failures = 0
        while connections < max_connections:
            connections += 1
            try:
                transport = await self._connect()
                await transport.send(self._subscribe_message())
                async for frame in transport:
                    self._dispatch(frame)
                failures = 0  # a clean close resets the backoff
            except self._reconnect_on:
                failures += 1
                await self._sleep(self._backoff_delay(failures))

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
