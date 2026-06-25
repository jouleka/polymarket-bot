"""Tests for the monotonic observation stamper (POL-3 / S1).

observed_at must be strictly increasing so replay ordering is total and a record
can never appear to have been observed before one ingested earlier.
"""

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
