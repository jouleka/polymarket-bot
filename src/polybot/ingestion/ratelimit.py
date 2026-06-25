"""Token-bucket rate limiter.

Paces client requests well under a venue ceiling (e.g. Data API positions
~150 req/10s). Pure + clock-injected: ``acquire_delay()`` returns how long to
wait before the request may go (0.0 if a token is available now), deducting the
token. The caller does the actual sleeping, so this stays synchronous/testable.
"""


import time


class RateLimiter:
    def __init__(self, rate_per_sec, capacity, clock=None):
        self._rate = rate_per_sec
        self._capacity = capacity
        self._clock = clock or time.monotonic
        self._tokens = float(capacity)
        self._last = self._clock()

    def acquire_delay(self, cost=1):
        self._refill()
        delay = 0.0 if self._tokens >= cost else (cost - self._tokens) / self._rate
        # Let the balance go negative; refill over `delay` brings it back toward 0,
        # which paces the next caller correctly.
        self._tokens -= cost
        return delay

    def _refill(self):
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now
