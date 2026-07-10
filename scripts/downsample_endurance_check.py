#!/usr/bin/env python3
"""Bounded public-data storage-rate gate for downsampled ingestion.

The live runner is added after its result contract is pinned. These helpers are
pure so projection and SQLite DB/WAL/SHM accounting stay unit-testable.
"""

GIB = 1024 ** 3
SECONDS_PER_DAY = 86400


def projected_gib_per_day(total_bytes, elapsed_seconds):
    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be > 0")
    return total_bytes / elapsed_seconds * SECONDS_PER_DAY / GIB


def footprint(paths):
    return sum(path.stat().st_size for path in paths if path.exists())
