"""Restart-safe construction for the process-wide ingestion history clock."""

from __future__ import annotations

import time

from polybot.core.clock import MonotonicStamper
from polybot.storage.market_memory import EventStore


def make_history_stamper(db_path, *, clock=time.time_ns):
    """Create a stamper whose first value is above all durable observations."""
    with EventStore(db_path) as store:
        floor = store.max_observed_at()
    return MonotonicStamper(clock=lambda: max(clock(), floor + 1))
