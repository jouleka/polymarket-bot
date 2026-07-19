"""Tests for the real network-adapter constants (POL-3 / S1).

The httpx/websockets glue in transport.py is verified live (mocking it would
test the mock), but the blessed ``reconnect_on`` tuple IS unit-checkable and is
safety-critical: a real Polymarket disconnect raises ``websockets.ConnectionClosed``,
which is NOT an ``OSError`` — so a socket wired with only ``(OSError,)`` would let
the normal disconnect escape ``run`` uncaught (no reconnect, no mark_all_stale).
"""

import asyncio

from websockets.exceptions import ConnectionClosed

from polybot.ingestion import transport
from polybot.ingestion.transport import WS_RECONNECT_ON


def test_ws_reconnect_on_covers_real_disconnect_types():
    # ConnectionClosed is the normal end-of-connection on the live transport and
    # is not an OSError; both must be in the blessed reconnect set.
    assert ConnectionClosed in WS_RECONNECT_ON
    assert OSError in WS_RECONNECT_ON
    assert not issubclass(ConnectionClosed, OSError)  # documents WHY OSError alone is insufficient


def test_live_ws_bounds_each_shards_received_frame_queue(monkeypatch):
    calls = []
    connection = object()

    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        return connection

    monkeypatch.setattr(transport.websockets, "connect", connect)

    assert asyncio.run(transport.open_market_ws("wss://market.test")) is connection
    assert calls == [(
        "wss://market.test",
        {"ping_interval": None, "max_queue": 1},
    )]
