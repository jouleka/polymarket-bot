"""Tests for the fate-isolated file Heartbeat (S4.3 / POL-6).

beat() writes a monotonically-increasing counter + a timestamp to a FILE so the
out-of-band supervisor can read liveness from a DIFFERENT process. Staleness is
computed against an injected `now`, so these units need no real sleep.
"""

import math

from polybot.ers.heartbeat import Heartbeat


def test_missing_file_is_infinitely_stale(tmp_path):
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    # Never beaten -> the file does not exist -> age is +inf, not alive at any timeout.
    assert hb.last_beat_age(now=100.0) == math.inf
    assert not hb.is_alive(now=100.0, timeout=5.0)


def test_fresh_beat_is_alive_and_zero_age(tmp_path):
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    hb.beat()
    assert hb.last_beat_age(now=100.0) == 0.0
    assert hb.is_alive(now=100.0, timeout=5.0)


def test_age_grows_with_now_and_goes_stale_past_timeout(tmp_path):
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    hb.beat()                                   # stamped at t=100
    assert hb.last_beat_age(now=103.0) == 3.0
    assert hb.is_alive(now=104.9, timeout=5.0)  # 4.9s old, under the 5s timeout
    assert not hb.is_alive(now=106.0, timeout=5.0)  # 6s old -> stale


def test_counter_increases_monotonically_across_beats(tmp_path):
    # The counter proves a NEW beat landed even if two beats share a coarse clock tick.
    ticks = iter([100.0, 100.0, 100.0])
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: next(ticks))
    hb.beat(); c1 = hb.read_counter()
    hb.beat(); c2 = hb.read_counter()
    hb.beat(); c3 = hb.read_counter()
    assert c1 < c2 < c3


def test_read_is_out_of_process_safe_via_a_fresh_handle(tmp_path):
    # A reader constructed AFTER the writer (no shared in-memory state) sees the beat:
    # this is exactly what the supervisor in another process does.
    path = str(tmp_path / "hb")
    writer = Heartbeat(path, clock=lambda: 200.0)
    writer.beat()
    reader = Heartbeat(path, clock=lambda: 999.0)   # different "clock", own handle
    assert reader.last_beat_age(now=205.0) == 5.0   # age uses the STORED 200, not the reader clock
    assert reader.is_alive(now=205.0, timeout=10.0)
