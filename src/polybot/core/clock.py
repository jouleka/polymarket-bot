"""Monotonic observation clock.

Every ingested record is stamped with a strictly-increasing ``observed_at`` (ns)
at receive time. Strict monotonicity gives a total order over observations even
when the underlying clock has coarse resolution, which is what makes replay
deterministic and look-ahead impossible.

CONTRACT: all collectors (WS, Data API, on-chain) MUST share ONE process-wide
stamper instance so observed_at is globally unique and totally ordered. A
per-collector stamper would let two feeds emit the same observed_at, making the
store's replay order depend on insertion race rather than observation order.
``stamp()`` is currently safe for a single calling thread; thread-safety (a lock)
and a concurrency stress harness land with the collector slice (POL-3 networked),
where concurrent callers first appear and a real interleave test is feasible.
"""


import time


class MonotonicStamper:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic_ns
        self._last = 0

    def stamp(self):
        now = self._clock()
        if now <= self._last:
            now = self._last + 1
        self._last = now
        return now
