"""Tests for the monotonic observation stamper (POL-3 / S1).

observed_at must be strictly increasing so replay ordering is total and a record
can never appear to have been observed before one ingested earlier.
"""

import threading

from polybot.core.clock import MonotonicStamper


def test_stamps_strictly_increase_under_a_frozen_clock():
    # Underlying clock never advances, yet each stamp must exceed the last.
    stamper = MonotonicStamper(clock=lambda: 1000)

    a, b, c = stamper.stamp(), stamper.stamp(), stamper.stamp()

    assert a < b < c


def test_stamps_track_the_underlying_clock_when_it_advances():
    times = iter([1000, 2000, 3000])
    stamper = MonotonicStamper(clock=lambda: next(times))

    assert [stamper.stamp() for _ in range(3)] == [1000, 2000, 3000]


def test_stamps_stay_unique_and_monotonic_under_concurrent_threads():
    # The shared stamper is used concurrently once sharding runs many collectors.
    # Guards that concurrent stampers stay unique + gapless: a frozen clock forces
    # every call down the +1 path and a start barrier releases all threads together,
    # maximizing contention on the read-modify-write of _last. The Lock in stamp()
    # makes this hold under OS threads regardless of interpreter; on a free-threaded
    # (no-GIL) build this is the canary that would fail if that lock were removed.
    stamper = MonotonicStamper(clock=lambda: 0)
    threads_n, per_thread = 8, 20000
    buckets = [[] for _ in range(threads_n)]
    start = threading.Barrier(threads_n)

    def worker(out):
        start.wait()  # release all threads together to maximize interleaving
        out.extend(stamper.stamp() for _ in range(per_thread))

    threads = [threading.Thread(target=worker, args=(buckets[i],)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stamps = [s for bucket in buckets for s in bucket]
    total = threads_n * per_thread
    assert len(stamps) == total
    assert sorted(stamps) == list(range(1, total + 1))  # unique, gapless, strictly monotonic
