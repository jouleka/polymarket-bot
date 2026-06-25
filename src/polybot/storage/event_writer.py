"""Off-loop single-writer wrapper around the Market-Memory EventStore (POL-12 / C2).

``EventStore.append`` commits per frame. Called from the WS sink it runs ON the
asyncio event loop, so with many shards one shard's commit stalls every sibling
receive loop + keepalive (idle-drop risk) — the reason prod shards are capped at 2.

``QueuedEventWriter`` preserves the sink-MUST-be-synchronous invariant (``append``
never awaits, so ``MarketStream.ingest``'s stamp->mutate->persist stays atomic on the
loop and cross-shard ``observed_at`` ordering holds) while moving the slow SQLite I/O
off the loop: ``append`` only enqueues onto a FIFO queue; a single dedicated writer
thread drains it and commits per row on its own thread. One writer + one FIFO queue
keeps insertion strictly ordered (and the store sorts by ``observed_at`` regardless).

DURABILITY: each row is committed individually the instant the writer processes it, so
a graceful ``close()`` — which drains the queue before returning — loses nothing. The
only loss window is a HARD crash (kill -9 / power loss) discarding rows still in the
in-memory queue: bounded by ``max_queued`` in the worst case, but ~0 in steady state
because the writer drains far faster than the WS frame rate. This is a deliberate trade
on a no-backfill store — strictly better than the on-loop commit it replaces, which lost
MORE data by stalling keepalives into idle-disconnects. Durability thus relies on a
graceful shutdown reaching ``close()``.
"""

import queue
import threading

_SENTINEL = object()  # enqueued by close() to stop the writer thread after draining


class QueuedEventWriter:
    def __init__(self, store, *, max_queued=100_000):
        if max_queued <= 0:
            raise ValueError("max_queued must be > 0")
        self._store = store
        self._max_queued = max_queued
        self._queue = queue.Queue()  # unbounded: put() + the close() sentinel never block
        self._error = None  # the writer thread's fatal exception, surfaced fail-loud
        self._closed = False
        # _pending = appended-but-not-yet-committed, the live backlog the ceiling caps.
        # _lock guards BOTH _pending and _error: append (caller thread) and _run (writer
        # thread) race on them, and correctness must not rest on the GIL (cf.
        # MonotonicStamper, free-threaded 3.13t). Uncontended on the hot path (~tens of ns).
        self._pending = 0
        self._peak_pending = 0  # high-water backlog, for 24/7 observability / endurance checks
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
        self._thread.start()

    def append(self, envelope):
        # Synchronous + non-blocking. The _error check + ceiling check + increment are
        # ONE critical section, so a concurrent writer death is observed atomically.
        # The put() happens after the lock is released — unbounded queue, never blocks,
        # no I/O, no await — so the event loop never stalls.
        with self._lock:
            if self._error is not None:
                # The writer thread already died; HALT on the hot path instead of
                # feeding a dead writer until shutdown.
                raise RuntimeError("event-writer thread failed; ingestion HALTED") from self._error
            if self._pending >= self._max_queued:
                # Fail loud: the writer can't keep up. HALT rather than grow memory
                # without bound or drop rows on a store that cannot be backfilled.
                raise RuntimeError(
                    f"event-writer backlog exceeded {self._max_queued} rows; ingestion HALTED "
                    "(the writer thread is not draining fast enough)"
                )
            self._pending += 1
            if self._pending > self._peak_pending:
                self._peak_pending = self._pending
        self._queue.put(envelope)

    def _run(self):
        # Only ``Exception`` is captured + surfaced fail-loud. A BaseException
        # (KeyboardInterrupt/SystemExit) is not delivered to a worker thread in normal
        # operation; sqlite / disk-full errors are all Exception-derived. If one ever did
        # terminate this thread, the backlog ceiling is the backstop (append HALTs once
        # the queue stops draining).
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            try:
                self._store.append(item)
            except Exception as exc:
                with self._lock:
                    self._error = exc
                    self._pending -= 1  # the row left the queue (failed) — keep the count honest
                return
            with self._lock:
                self._pending -= 1

    def close(self):
        # Idempotent: a 24/7 service may close via both a `with` block and an explicit
        # call, so a second close must be a true no-op (not a double store-close).
        # Drain-on-close: the sentinel is FIFO-after every queued row, so the writer
        # commits all pending rows before it stops. After join() the writer thread is
        # finished, so _error is fully published (join is a happens-before barrier).
        if self._closed:
            return
        self._closed = True
        self._queue.put(_SENTINEL)
        self._thread.join()
        self._store.close()
        if self._error is not None:
            raise RuntimeError("event-writer thread failed; ingestion HALTED") from self._error

    def peak_pending(self):
        """High-water backlog (max rows ever queued-but-not-yet-committed). ~0 in
        steady state; rising toward max_queued means the writer can't keep up."""
        with self._lock:
            return self._peak_pending

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Always drain + close. But if the `with` body is already raising, don't let the
        # writer's HALT mask the original (often more diagnostic) cause: swallow only our
        # own re-raise so the body exception wins.
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
        return False
