"""Monotonic observation clock.

Every ingested record is stamped with a strictly-increasing ``observed_at`` (ns)
at receive time. Strict monotonicity gives a total order over observations even
when the underlying clock has coarse resolution, which is what makes replay
deterministic and look-ahead impossible.

CONTRACT: all collectors (WS, Data API, on-chain) MUST share ONE process-wide
stamper instance so observed_at is globally unique and totally ordered. A
per-collector stamper would let two feeds emit the same observed_at, making the
store's replay order depend on insertion race rather than observation order.

Concurrency: the sharded collectors run as asyncio tasks in ONE event loop, where
``stamp()`` has no ``await`` and so is atomic w.r.t. task switches. A ``Lock`` ALSO
guards the read-modify-write of ``_last`` so the strict-monotonic invariant holds
under concurrent OS threads and free-threaded (no-GIL) builds — not only on a
stock GIL where this happens to be safe (and even there only with no active
trace/profile hook). The cost is one uncontended lock per frame (~tens of ns),
negligible against frame decode; correctness of this process-wide ordering
primitive must not rest on an interpreter implementation detail.
"""


import threading
import time


class MonotonicStamper:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic_ns
        self._last = 0
        self._lock = threading.Lock()

    def stamp(self):
        with self._lock:
            now = self._clock()
            if now <= self._last:
                now = self._last + 1
            self._last = now
            return now
