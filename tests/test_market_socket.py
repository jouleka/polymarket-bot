"""Tests for the async CLOB market socket loop (POL-3 / S1).

Uses a FakeTransport and asyncio.run so the loop logic — subscribe, dispatch,
reconnect-with-backoff, and per-frame resilience — is exercised with no network
and no extra dependency.
"""

import asyncio
import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_socket import MarketSocket
from polybot.ingestion.market_stream import MarketStream


class FakeDisconnect(Exception):
    """Stand-in for websockets.ConnectionClosed."""


def _book_event(asset_id, best_bid, best_ask):
    return {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": best_bid, "size": "100"}],
        "asks": [{"price": best_ask, "size": "100"}],
    }


def _book_frame(asset_id, best_bid, best_ask):
    return json.dumps(_book_event(asset_id, best_bid, best_ask))


class FakeTransport:
    """Async-iterable of frames with a recording send(), like a ws connection.

    A frame that is an exception instance is raised mid-stream (simulating a
    real disconnect); everything else is yielded as a text frame.
    """

    def __init__(self, frames):
        self._frames = frames
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def __aiter__(self):
        for frame in self._frames:
            if isinstance(frame, BaseException):
                raise frame
            yield frame


class RecordingSleep:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def _stream():
    return MarketStream(MonotonicStamper(clock=lambda: 1))


def test_socket_subscribes_then_dispatches_frames():
    transport = FakeTransport([_book_frame("A", "0.60", "0.62")])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"])

    asyncio.run(socket.run(max_connections=1))

    assert len(transport.sent) == 1
    assert "A" in transport.sent[0]
    assert stream.book_for("A").best_bid() == Decimal("0.60")


def test_socket_ingests_batched_array_frames():
    # The initial multi-asset subscribe response arrives as a JSON array.
    frame = json.dumps([_book_event("A", "0.60", "0.62"), _book_event("B", "0.40", "0.45")])
    transport = FakeTransport([frame])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A", "B"])

    asyncio.run(socket.run(max_connections=1))

    assert stream.book_for("A").best_bid() == Decimal("0.60")
    assert stream.book_for("B").best_bid() == Decimal("0.40")


def test_socket_skips_malformed_frame_without_dropping_connection():
    transport = FakeTransport(["this is not json", _book_frame("A", "0.60", "0.62")])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"])

    asyncio.run(socket.run(max_connections=1))  # must not raise

    assert stream.book_for("A").best_bid() == Decimal("0.60")  # good frame still applied


def test_socket_reconnects_with_backoff_after_a_disconnect():
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), FakeDisconnect()])
    t2 = FakeTransport([_book_frame("A", "0.61", "0.63")])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = _stream()
    socket = MarketSocket(
        connect, stream, asset_ids=["A"], reconnect_on=(FakeDisconnect,), sleep=sleep
    )

    asyncio.run(socket.run(max_connections=2))

    assert len(t1.sent) == 1 and len(t2.sent) == 1  # re-subscribed on reconnect (resync)
    assert stream.book_for("A").best_bid() == Decimal("0.61")  # latest snapshot wins
    assert sleep.delays and sleep.delays[0] > 0  # backoff applied, not a hot loop


def test_socket_marks_books_stale_on_disconnect():
    # Book built on t1, then a disconnect; t2 reconnects but sends no resync yet,
    # so the book must read stale (ERS must not size off it until a fresh snapshot).
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), FakeDisconnect()])
    t2 = FakeTransport([])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    stream = _stream()
    socket = MarketSocket(
        connect, stream, asset_ids=["A"], reconnect_on=(FakeDisconnect,), sleep=RecordingSleep()
    )

    asyncio.run(socket.run(max_connections=2))

    assert stream.book_for("A").is_stale()
