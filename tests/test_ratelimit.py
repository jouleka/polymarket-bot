"""Tests for the token-bucket rate limiter (POL-3 / S1)."""

import pytest

from polybot.ingestion.ratelimit import RateLimiter


def test_allows_a_burst_up_to_capacity_without_delay():
    now = [0.0]
    limiter = RateLimiter(rate_per_sec=10, capacity=5, clock=lambda: now[0])

    assert [limiter.acquire_delay() for _ in range(5)] == [0.0] * 5


def test_delays_once_the_bucket_is_empty():
    now = [0.0]
    limiter = RateLimiter(rate_per_sec=10, capacity=5, clock=lambda: now[0])
    for _ in range(5):
        limiter.acquire_delay()

    # bucket empty: one token at 10/sec -> 0.1s wait
    assert limiter.acquire_delay() == pytest.approx(0.1)


def test_refills_over_elapsed_time():
    now = [0.0]
    limiter = RateLimiter(rate_per_sec=10, capacity=5, clock=lambda: now[0])
    for _ in range(5):
        limiter.acquire_delay()

    now[0] = 1.0  # a second later: +10 tokens, capped at capacity 5
    assert limiter.acquire_delay() == 0.0
