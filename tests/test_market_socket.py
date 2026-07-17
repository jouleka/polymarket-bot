"""Tests for the async CLOB market socket loop (POL-3 / S1).

Uses a FakeTransport and asyncio.run so the loop logic — subscribe, dispatch,
reconnect-with-backoff, and per-frame resilience — is exercised with no network
and no extra dependency.
"""

import asyncio
import json
from decimal import Decimal

import pytest

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


class IdleAfterFramesTransport:
    """Yields the given frames, then stays open and idle forever (blocks).

    Models a real, quiet connection: after the initial book the venue may send
    nothing for a long time, yet the socket must stay alive — so the client has
    to send its own keepalive. ``__aiter__`` never raises StopAsyncIteration and
    never closes, so ``run`` only returns when the test cancels it.
    """

    def __init__(self, frames):
        self._frames = frames
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def __aiter__(self):
        for frame in self._frames:
            yield frame
        await asyncio.Event().wait()  # open but idle: never another frame, never closed


class PingSendFailsTransport:
    """Yields its frames, stays open briefly, then closes cleanly — but every
    ``send("PING")`` raises OSError (a flaky/dying socket). The brief open window
    lets the keepalive fire at least once before the clean close.
    """

    def __init__(self, frames):
        self._frames = frames
        self.sent = []
        self.ping_attempts = 0

    async def send(self, message):
        if message == "PING":
            self.ping_attempts += 1
            raise OSError("simulated keepalive send failure on a dying socket")
        self.sent.append(message)

    async def __aiter__(self):
        for frame in self._frames:
            yield frame
        await asyncio.sleep(0.1)  # stay open long enough for a PING to fire, then close cleanly


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


def test_socket_sends_periodic_ping_keepalive():
    # The venue keepalive is CLIENT-driven (verified live 2026-06-25): the client
    # must send a bare "PING" every ~10s or the venue eventually drops an idle
    # socket. With ping_interval tiny and an idle-but-open transport, the socket
    # must emit RECURRING "PINGs" after the subscribe while it keeps the book.
    # (Wall-clock poll with a generous bound: the outcome is deterministic — no
    # false-pass is possible, and a false-fail needs a multi-second loop stall.)
    transport = IdleAfterFramesTransport([_book_frame("A", "0.60", "0.62")])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], ping_interval=0.01)

    async def drive():
        task = asyncio.create_task(socket.run(max_connections=1))
        try:
            for _ in range(300):  # ~3s bound (>= 200 ping periods); breaks once two PINGs land
                if transport.sent.count("PING") >= 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(drive())

    assert transport.sent[0] == json.dumps({"type": "market", "assets_ids": ["A"]})  # subscribe first
    assert transport.sent.count("PING") >= 2  # recurring keepalive, not a one-shot
    assert stream.book_for("A").best_bid() == Decimal("0.60")  # receive loop still applied the book


def test_socket_restarts_keepalive_on_each_connection():
    # The keepalive task is per-connection (created inside the reconnect loop), so
    # after a disconnect+resync the FRESH socket must get its own PINGs — otherwise
    # a long-lived process stops keepaliving after its first drop and silently dies.
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), FakeDisconnect()])
    t2 = IdleAfterFramesTransport([_book_frame("A", "0.61", "0.63")])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    stream = _stream()
    socket = MarketSocket(
        connect, stream, asset_ids=["A"], reconnect_on=(FakeDisconnect,),
        sleep=RecordingSleep(), ping_interval=0.01,
    )

    async def drive():
        task = asyncio.create_task(socket.run(max_connections=2))
        try:
            for _ in range(300):  # wait for the SECOND connection's keepalive to fire
                if "PING" in t2.sent:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(drive())

    assert t2.sent[0] == json.dumps({"type": "market", "assets_ids": ["A"]})  # re-subscribed (resync)
    assert "PING" in t2.sent[1:]  # a fresh keepalive runs on the reconnected socket


def test_socket_runs_unbounded_when_max_connections_is_none():
    # 24/7 operation: max_connections=None reconnects forever. Prove it makes a
    # SECOND connection after a disconnect (the bounded default would stop at one),
    # so a flapping shard can never silently exhaust a budget and go dark.
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), FakeDisconnect()])
    t2 = IdleAfterFramesTransport([_book_frame("A", "0.61", "0.63")])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    stream = _stream()
    socket = MarketSocket(
        connect, stream, asset_ids=["A"], reconnect_on=(FakeDisconnect,), sleep=RecordingSleep(),
    )

    async def drive():
        task = asyncio.create_task(socket.run(max_connections=None))
        try:
            for _ in range(300):
                if t2.sent:  # the reconnected socket re-subscribed
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(drive())

    assert t2.sent and "A" in t2.sent[0]  # reconnected past the first connection -> unbounded


def test_socket_skips_pong_keepalive_reply_without_halt():
    # The venue replies to our client "PING" with a bare "PONG" text frame. It is
    # not JSON, so it must be skipped (not forwarded to stream.ingest, where an
    # unknown event_type would HALT) and the loop must keep consuming. Locks this
    # safety property: a keepalive reply must never wedge 24/7 ingestion.
    transport = FakeTransport(["PONG", _book_frame("A", "0.60", "0.62"), "PONG"])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"])

    asyncio.run(socket.run(max_connections=1))  # must not raise (no HALT)

    book = stream.book_for("A")
    assert book.best_bid() == Decimal("0.60")  # frame after the leading PONG applied
    assert book.best_ask() == Decimal("0.62")  # ...and the loop kept consuming past the trailing PONG


def test_socket_keepalive_send_failure_does_not_trigger_spurious_reconnect():
    # The keepalive is best-effort: if a PING send fails, that must NOT be re-raised
    # through run's teardown and mistaken for a disconnect. The receive loop is the
    # sole authority on connection liveness — here it closes CLEANLY, so the book
    # must NOT be marked stale and no backoff must be applied.
    transport = PingSendFailsTransport([_book_frame("A", "0.60", "0.62")])

    async def connect():
        return transport

    sleep = RecordingSleep()
    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep, ping_interval=0.01)

    asyncio.run(socket.run(max_connections=1))  # must not raise

    assert transport.ping_attempts >= 1            # the keepalive really did try to PING
    assert not stream.book_for("A").is_stale()     # clean close: no spurious disconnect handling
    assert sleep.delays == []                       # no backoff applied on a clean close


def test_socket_rejects_nonpositive_ping_interval():
    # A 0 / negative ping_interval would turn the keepalive into a hot loop that
    # floods the venue and pins a core — refuse it at construction (fail loud).
    import pytest

    stream = _stream()
    with pytest.raises(ValueError):
        MarketSocket(lambda: None, stream, asset_ids=["A"], ping_interval=0)
    with pytest.raises(ValueError):
        MarketSocket(lambda: None, stream, asset_ids=["A"], ping_interval=-1.0)


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


def test_socket_normal_close_stales_before_production_reconnect():
    # websockets iteration ends normally for close codes 1000/1001. In unbounded
    # production mode that is still a reconnect boundary: retain old levels only
    # for diagnostics, mark them stale, back off, and ignore a raced delta until
    # this connection's replacement snapshot arrives.
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    raced_delta = _price_change_frame("A", "0.70", "BUY", "50",
                                       best_bid="0.70", best_ask="0.72")
    clean = _price_change_frame("A", "0.71", "BUY", "50",
                                best_bid="0.71", best_ask="0.72")
    t2 = FakeTransport([raced_delta, _book_frame("A", "0.70", "0.72"), clean])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), asset_ids=["A"])
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep, max_resyncs=1)

    asyncio.run(socket.run(max_connections=2))

    book = stream.book_for("A")
    assert not book.is_stale()
    assert book.best_bid() == Decimal("0.71")
    assert book.best_ask() == Decimal("0.72")
    assert sleep.delays == [0.5]  # normal-close reconnect cannot hot-loop


def _price_change_frame(asset_id, price, side, size, best_bid, best_ask,
                        market="0xmarket", timestamp="1"):
    return json.dumps({
        "event_type": "price_change",
        "market": market,
        "timestamp": timestamp,
        "price_changes": [{
            "asset_id": asset_id, "price": price, "side": side, "size": size,
            "best_bid": best_bid, "best_ask": best_ask,
        }],
    })


def test_socket_reconnects_to_resync_on_a_midstream_sequence_gap():
    # The socket stays UP, but a price_change reveals our reconstructed top-of-book
    # diverged from the venue's authoritative best_bid/best_ask (a dropped delta).
    # Recovery reuses the PROVEN resync path: force a reconnect so subscribe-on-
    # connect pulls a fresh snapshot. (Re-subscribing on the SAME live socket does
    # NOT reliably resnapshot — confirmed live 2026-06-25 — so we reconnect.)
    gap = _price_change_frame("A", "0.61", "BUY", "50", best_bid="0.99", best_ask="0.62")
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), gap])
    t2 = FakeTransport([_book_frame("A", "0.61", "0.63")])  # resync snapshot after reconnect
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep)

    asyncio.run(socket.run(max_connections=2))

    assert len(t1.sent) == 1 and len(t2.sent) == 1  # reconnected and re-subscribed (resync)
    assert stream.book_for("A").best_bid() == Decimal("0.61")  # fresh snapshot applied
    assert not stream.book_for("A").is_stale()                 # recovered after the resync snapshot
    assert sleep.delays == [0.5]  # one resync -> a floor backoff (never a zero-delay hot-loop)


def test_socket_reconnect_waits_for_snapshot_when_delta_arrives_first():
    # Production repeatedly exhausted the resync gate because a reconnect retained
    # the old stale book and a live delta could race ahead of that asset's replacement
    # snapshot. The delta is observable but non-authoritative; only the snapshot may
    # make the book fresh again, after which clean deltas resume normally.
    gap = _price_change_frame("A", "0.61", "BUY", "50",
                              best_bid="0.99", best_ask="0.62")
    raced_delta = _price_change_frame("A", "0.70", "BUY", "50",
                                       best_bid="0.70", best_ask="0.72")
    clean = _price_change_frame("A", "0.71", "BUY", "50",
                                best_bid="0.71", best_ask="0.72")
    t1 = FakeTransport([_book_frame("A", "0.60", "0.62"), gap])
    t2 = FakeTransport([
        raced_delta,
        _book_frame("A", "0.70", "0.72"),
        clean,
    ])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), asset_ids=["A"])
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep, max_resyncs=2)

    asyncio.run(socket.run(max_connections=2))

    book = stream.book_for("A")
    assert not book.is_stale()
    assert book.best_bid() == Decimal("0.71")
    assert book.best_ask() == Decimal("0.72")
    assert sleep.delays == [0.5]  # no manufactured second resync before the snapshot


def test_socket_recovery_preserves_sibling_and_halts_atomically_on_format_change():
    gap_a = _price_change_frame("A", "0.61", "BUY", "50",
                                best_bid="0.99", best_ask="0.62")
    raced_b = _price_change_frame("B", "0.90", "BUY", "50",
                                  best_bid="0.90", best_ask="0.92")
    malformed = json.dumps({
        "event_type": "price_change",
        "market": "0xmarket",
        "timestamp": "3",
        "price_changes": [
            {"asset_id": "A", "price": "0.61", "side": "BUY", "size": "50",
             "best_bid": "0.61", "best_ask": "0.62"},
            {"asset_id": "B", "price": "0.41", "side": "RENAMED_SIDE", "size": "50",
             "best_bid": "0.41", "best_ask": "0.45"},
        ],
    })
    t1 = FakeTransport([
        _book_frame("A", "0.60", "0.62"),
        _book_frame("B", "0.40", "0.45"),
        gap_a,
    ])
    t2 = FakeTransport([
        raced_b,
        _book_frame("A", "0.60", "0.62"),
        _book_frame("B", "0.40", "0.45"),
        malformed,
    ])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    stream = MarketStream(MonotonicStamper(clock=lambda: 1), asset_ids=["A", "B"])
    socket = MarketSocket(connect, stream, asset_ids=["A", "B"], sleep=RecordingSleep())

    with pytest.raises(ValueError, match="unknown price_change side"):
        asyncio.run(socket.run(max_connections=2))

    assert stream.book_for("A").best_bid() == Decimal("0.60")  # no partial A apply
    assert stream.book_for("B").best_bid() == Decimal("0.40")  # raced B was non-authoritative
    assert not stream.book_for("A").is_stale()
    assert not stream.book_for("B").is_stale()


def test_socket_resync_request_without_detail_halts():
    stream = _stream()
    stream._resync_requested = True
    transport = FakeTransport([_book_frame("A", "0.60", "0.62")])

    async def connect():
        return transport

    socket = MarketSocket(connect, stream, asset_ids=["A"])

    with pytest.raises(RuntimeError, match="without divergence detail"):
        asyncio.run(socket.run(max_connections=1))


def test_socket_resync_storm_backs_off_then_halts():
    # A book that re-diverges on EVERY fresh snapshot must not hot-loop the gateway:
    # back off each consecutive resync and then HALT loudly (a never-reconcilable book
    # is itself a fail-loud format-change signal), instead of an unbounded zero-delay
    # connect->snapshot->diverge->reconnect storm.
    gap = _price_change_frame("A", "0.61", "BUY", "50", best_bid="0.99", best_ask="0.62")

    def make():
        return FakeTransport([_book_frame("A", "0.60", "0.62"), gap])

    transports = iter([make() for _ in range(10)])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep, max_resyncs=3)

    with pytest.raises(RuntimeError, match="resync"):
        asyncio.run(socket.run(max_connections=None))  # 24/7 mode: only the HALT stops it

    assert sleep.delays == [0.5, 1.0]  # backed off (exp) before HALTing on the 3rd consecutive resync


def test_socket_resync_halt_reports_bounded_asset_divergence():
    # Overnight production HALTs were unactionable because the exception discarded
    # the asset and expected/actual top. The terminal error must carry only bounded
    # diagnostic fields -- never a raw frame -- without weakening the retry gate.
    gap = _price_change_frame("A", "0.61", "BUY", "50",
                              best_bid="0.99", best_ask="0.62",
                              market="0xmarket", timestamp="123")

    def make():
        return FakeTransport([_book_frame("A", "0.60", "0.62"), gap])

    transports = iter([make() for _ in range(3)])

    async def connect():
        return next(transports)

    socket = MarketSocket(connect, _stream(), asset_ids=["A", "B"],
                          sleep=RecordingSleep(), max_resyncs=2)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(socket.run(max_connections=None))

    message = str(excinfo.value)
    assert "asset_id='A'" in message
    assert "market='0xmarket'" in message
    assert "timestamp='123'" in message
    assert "reconstructed='0.61'/'0.62'" in message
    assert "venue='0.99'/'0.62'" in message
    assert "shard_assets=2" in message
    assert "price_changes" not in message  # no raw frame persistence-by-log


def test_socket_resync_diagnostic_escapes_and_bounds_untrusted_fields():
    long_market = "\n" + ("m" * 140) + "MARKET-INJECTION"
    long_timestamp = "\n" + ("t" * 140) + "TIMESTAMP-INJECTION"
    long_venue_bid = "\n0." + ("9" * 100) + "\n"  # valid Decimal despite whitespace
    gap = _price_change_frame("A", "0.61", "BUY", "50",
                              best_bid=long_venue_bid, best_ask="0.62",
                              market=long_market, timestamp=long_timestamp)
    transports = iter([
        FakeTransport([_book_frame("A", "0.60", "0.62"), gap]),
    ])

    async def connect():
        return next(transports)

    socket = MarketSocket(connect, _stream(), asset_ids=["A"],
                          sleep=RecordingSleep(), max_resyncs=1)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(socket.run(max_connections=None))

    message = str(excinfo.value)
    assert "\n" not in message
    assert "\\n" in message
    assert "MARKET-INJECTION" not in message
    assert "TIMESTAMP-INJECTION" not in message
    assert "9" * 65 not in message


def test_socket_resync_halt_reports_each_consecutive_attempt_in_order():
    gap_a = _price_change_frame("A", "0.61", "BUY", "50",
                                best_bid="0.99", best_ask="0.62", timestamp="1")
    gap_b = _price_change_frame("B", "0.41", "BUY", "50",
                                best_bid="0.88", best_ask="0.45", timestamp="2")
    transports = iter([
        FakeTransport([_book_frame("A", "0.60", "0.62"), gap_a]),
        FakeTransport([_book_frame("B", "0.40", "0.45"), gap_b]),
    ])

    async def connect():
        return next(transports)

    socket = MarketSocket(connect, MarketStream(MonotonicStamper(clock=lambda: 1),
                                                 asset_ids=["A", "B"]),
                          asset_ids=["A", "B"], sleep=RecordingSleep(), max_resyncs=2)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(socket.run(max_connections=None))

    message = str(excinfo.value)
    assert "attempts=[" in message
    assert message.index("asset_id='A'") < message.index("asset_id='B'")
    assert "timestamp='1'" in message
    assert "timestamp='2'" in message


def test_socket_resyncs_separated_by_clean_progress_do_not_halt():
    # A transient gap that RECOVERS (the reconnect then streams a clean delta) must not
    # accrue toward the storm HALT: a clean applied price_change resets the counter, so
    # rare isolated gaps never escalate even over a long run.
    clean = _price_change_frame("A", "0.61", "BUY", "50", best_bid="0.61", best_ask="0.62")
    gap = _price_change_frame("A", "0.62", "BUY", "50", best_bid="0.99", best_ask="0.62")

    def make():
        return FakeTransport([_book_frame("A", "0.60", "0.62"), clean, gap])

    transports = iter([make() for _ in range(5)])

    async def connect():
        return next(transports)

    sleep = RecordingSleep()
    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=sleep, max_resyncs=3)

    asyncio.run(socket.run(max_connections=5))  # must NOT raise despite 5 gaps

    assert sleep.delays == [0.5, 0.5, 0.5, 0.5, 0.5]  # each resync stays at the floor (counter reset)


class GapThenCloseRaises:
    """Yields its frames (a gap forces a resync close), and whose async close()
    raises — modelling closing a socket that is already going down."""

    def __init__(self, frames):
        self._frames = frames
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def __aiter__(self):
        for frame in self._frames:
            yield frame

    async def close(self):
        raise OSError("close on a socket that is already going down")


def test_socket_safe_close_tolerates_a_failing_close_on_resync():
    # _safe_close must swallow a failing close() on the abandoned transport so the
    # resync reconnect still proceeds (the close error is irrelevant — we reconnect).
    gap = _price_change_frame("A", "0.61", "BUY", "50", best_bid="0.99", best_ask="0.62")
    t1 = GapThenCloseRaises([_book_frame("A", "0.60", "0.62"), gap])
    t2 = FakeTransport([_book_frame("A", "0.61", "0.63")])
    transports = iter([t1, t2])

    async def connect():
        return next(transports)

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"], sleep=RecordingSleep())

    asyncio.run(socket.run(max_connections=2))  # must not raise despite close() failing

    assert stream.book_for("A").best_bid() == Decimal("0.61")  # resync recovered after the failed close
    assert not stream.book_for("A").is_stale()


def test_socket_does_not_resync_without_a_gap():
    # A consistent price_change (our book matches the venue's reported top-of-book)
    # must NOT trigger a reconnect — exactly one subscribe for the single connection.
    consistent = _price_change_frame("A", "0.61", "BUY", "50", best_bid="0.61", best_ask="0.62")
    transport = FakeTransport([_book_frame("A", "0.60", "0.62"), consistent])

    async def connect():
        return transport

    stream = _stream()
    socket = MarketSocket(connect, stream, asset_ids=["A"])

    asyncio.run(socket.run(max_connections=1))

    assert transport.sent == [json.dumps({"type": "market", "assets_ids": ["A"]})]  # no resync
    assert not stream.book_for("A").is_stale()
