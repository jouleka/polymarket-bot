"""Tests for the off-loop single-writer EventStore wrapper (POL-12 / C2).

The CLOB market WS runs many shard receive-loops + keepalives on one asyncio loop.
``EventStore.append`` commits per frame synchronously; on the loop that stalls every
sibling shard (idle-drop risk), which is why prod shards are capped at 2. The
``QueuedEventWriter`` keeps the sink SYNCHRONOUS (the ordering invariant) but FAST:
``append`` only enqueues, and a dedicated background thread drains the FIFO queue and
commits off the loop. These tests pin: drain-on-close, FIFO order, the off-loop
property (a slow store never blocks the caller), fail-loud on writer-thread death,
the fail-loud high-water ceiling, and the real-store persist/restart path.
"""

import threading

import pytest

from polybot.core.models import Envelope
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore


def _env(event_id, observed_at=0):
    return Envelope(
        source="clob-ws",
        source_tier="VENUE",
        event_id=event_id,
        observed_at=observed_at,
        content="{}",
    )


class RecordingStore:
    """In-memory stand-in for EventStore: records appends + counts close() calls."""

    def __init__(self):
        self.appended = []
        self.closed = False
        self.close_count = 0

    def append(self, envelope):
        self.appended.append(envelope)

    def close(self):
        self.closed = True
        self.close_count += 1


def test_context_manager_drains_and_closes():
    store = RecordingStore()
    with QueuedEventWriter(store) as writer:
        writer.append(_env("1"))
        writer.append(_env("2"))
    # __exit__ drained the queue, joined the thread, and closed the wrapped store.
    assert [e.event_id for e in store.appended] == ["1", "2"]
    assert store.closed


def test_close_is_idempotent():
    # A 24/7 service may close via both a `with` block and an explicit call; the second
    # close must be a TRUE no-op — not just "doesn't raise", but it must not close the
    # wrapped store a second time (a non-idempotent store close would corrupt state).
    store = RecordingStore()
    writer = QueuedEventWriter(store)
    writer.append(_env("1"))
    writer.close()
    writer.close()  # must not hang, raise, or re-close the store
    assert [e.event_id for e in store.appended] == ["1"]
    assert store.close_count == 1  # the wrapped store was closed exactly once


def test_context_manager_does_not_mask_a_body_exception():
    # If the `with` body raises, __exit__ must still drain + close, but must NOT let the
    # writer's HALT replace the body's exception — the original (often more diagnostic)
    # cause must win, not be silently swapped for "event-writer thread failed".
    store = ExplodingStore(RuntimeError("writer boom"))
    with pytest.raises(ValueError, match="body went wrong"):
        with QueuedEventWriter(store) as writer:
            writer.append(_env("1"))
            raise ValueError("body went wrong")


def test_writes_through_to_a_real_event_store_and_survives_restart(tmp_path):
    # Acceptance: off-loop writes land durably in real SQLite, in order, and survive a
    # restart. The writer thread drives a connection opened check_same_thread=False; a
    # fresh main-thread reader (== process restart) sees every committed row.
    path = str(tmp_path / "mm.db")
    with QueuedEventWriter(EventStore(path, check_same_thread=False)) as writer:
        writer.append(_env("a", observed_at=10))
        writer.append(_env("b", observed_at=20))
        writer.append(_env("c", observed_at=30))

    with EventStore(path) as reopened:
        assert [e.event_id for e in reopened.all()] == ["a", "b", "c"]


def test_is_a_drop_in_target_for_the_persisting_sink(tmp_path):
    # PersistingSink calls target.append(envelope); the writer is a drop-in, so the
    # whole WS pipeline persists off-loop with NO change to the synchronous-sink
    # contract MarketStream.ingest relies on.
    from polybot.core.clock import MonotonicStamper
    from polybot.ingestion.market_stream import MarketStream
    from polybot.ingestion.persistence import PersistingSink

    path = str(tmp_path / "mm.db")
    writer = QueuedEventWriter(EventStore(path, check_same_thread=False))
    stream = MarketStream(MonotonicStamper(clock=lambda: 5), sink=PersistingSink(writer))

    stream.ingest({
        "event_type": "book", "asset_id": "A",
        "bids": [{"price": "0.60", "size": "100"}],
        "asks": [{"price": "0.62", "size": "100"}],
    })
    writer.close()

    with EventStore(path) as reopened:
        rows = reopened.all()
    assert len(rows) == 1
    assert rows[0].market_links == ("A",)
    assert rows[0].source == "clob-ws"


class BlockingStore:
    """A store whose ``append`` blocks until released, so a test can prove the
    caller (the event loop) is NOT stalled behind the SQLite write."""

    def __init__(self):
        self.appended = []
        self.entered = threading.Event()  # set when a writer reaches the blocked append
        self.release = threading.Event()  # the test sets this to let appends proceed
        self.closed = False

    def append(self, envelope):
        self.entered.set()
        self.release.wait(timeout=1.0)  # finite so a SYNC impl fails fast, not hangs
        self.appended.append(envelope)

    def close(self):
        self.closed = True


def test_append_is_drained_to_the_store_on_close():
    store = RecordingStore()
    writer = QueuedEventWriter(store)

    writer.append(_env("1"))
    writer.close()

    assert [e.event_id for e in store.appended] == ["1"]
    assert store.closed  # closing the writer closes the wrapped store too


class ExplodingStore:
    """A store whose ``append`` always raises, to exercise fail-loud propagation."""

    def __init__(self, exc):
        self._exc = exc
        self.failed = threading.Event()  # set the instant append raises
        self.closed = False

    def append(self, envelope):
        self.failed.set()
        raise self._exc

    def close(self):
        self.closed = True


def test_writer_thread_failure_surfaces_on_close():
    # Fail loud: a write that raises (disk full, sqlite error, no-backfill store) must
    # NOT be swallowed by a silently-dead background thread. close() re-raises it,
    # chaining the original cause, so the operator HALTs instead of losing data blind.
    boom = RuntimeError("disk full")
    writer = QueuedEventWriter(ExplodingStore(boom))

    writer.append(_env("1"))

    with pytest.raises(RuntimeError) as excinfo:
        writer.close()
    assert excinfo.value.__cause__ is boom


def test_append_after_writer_failure_halts_promptly():
    # In production the loop calls append() continuously. Once the writer thread has
    # died, the very next append must HALT rather than silently feed a dead writer
    # until shutdown. (Joining the writer's own thread is a deterministic barrier:
    # _error is set just before the thread returns.)
    boom = RuntimeError("disk full")
    writer = QueuedEventWriter(ExplodingStore(boom))

    writer.append(_env("1"))
    writer._thread.join(timeout=2)  # wait for the writer thread to raise + exit

    with pytest.raises(RuntimeError) as excinfo:
        writer.append(_env("2"))
    assert excinfo.value.__cause__ is boom


class WedgedStore:
    """A store whose ``append`` blocks indefinitely until released — to drive the
    backlog past the writer's high-water ceiling."""

    def __init__(self):
        self.appended = []
        self.release = threading.Event()

    def append(self, envelope):
        self.release.wait()  # blocks until the test releases it
        self.appended.append(envelope)

    def close(self):
        pass


def test_append_halts_when_backlog_exceeds_high_water_mark():
    # Fail loud, not OOM / silent drop: a writer that can't keep up must HALT once the
    # backlog crosses the ceiling, never grow memory without bound or drop rows on a
    # store that cannot be backfilled.
    store = WedgedStore()
    writer = QueuedEventWriter(store, max_queued=3)
    try:
        accepted = 0
        with pytest.raises(RuntimeError, match="backlog"):
            for i in range(100):
                writer.append(_env(str(i)))
                accepted += 1
        assert accepted == 3  # exactly the ceiling, then HALT — not unbounded
    finally:
        store.release.set()  # let the wedged writer drain so close() can join
        writer.close()


def test_peak_pending_tracks_the_high_water_backlog():
    # Observability for the 24/7 writer: peak_pending() reports the high-water backlog so
    # an operator (and the shard-endurance check) can see headroom against the ceiling.
    # Wedge the store so nothing drains, append N, and the peak must equal N.
    store = WedgedStore()
    writer = QueuedEventWriter(store)
    try:
        for i in range(5):
            writer.append(_env(str(i)))
        assert writer.peak_pending() == 5
    finally:
        store.release.set()
        writer.close()


def test_append_does_not_block_on_a_slow_store_write():
    # The whole point of POL-12: the WS sink runs on the event loop, so append must
    # return immediately even when the SQLite write is slow. The writer thread takes
    # the first item and blocks inside store.append; the rest queue up; the CALLER is
    # never blocked behind the I/O.
    store = BlockingStore()
    writer = QueuedEventWriter(store)

    for i in range(3):
        writer.append(_env(str(i), observed_at=i))  # each must return without blocking

    assert store.entered.wait(timeout=2)  # the writer thread reached the (blocked) append
    assert store.appended == []           # ...nothing committed yet: the caller did not wait

    store.release.set()  # let the off-loop writer drain everything
    writer.close()       # drains the queue + joins the thread

    assert [e.event_id for e in store.appended] == ["0", "1", "2"]
