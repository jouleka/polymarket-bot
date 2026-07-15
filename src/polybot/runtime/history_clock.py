"""Restart-safe construction for the process-wide ingestion history clock."""

from __future__ import annotations

import time
from pathlib import Path
import sqlite3

from polybot.core.clock import MonotonicStamper
from polybot.storage.market_memory import EventStore


_STAMP_COLUMNS = frozenset({
    "observed_at", "created_at", "decided_at", "at", "recorded_at",
    "resolved_at", "settled_at", "accepted_at", "delivered_at", "halted_at",
})


def _quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _database_floor(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        floor = 0
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for (table,) in tables:
            columns = connection.execute(
                f"PRAGMA table_info({_quote_identifier(table)})"
            ).fetchall()
            for column in (row[1] for row in columns if row[1] in _STAMP_COLUMNS):
                row = connection.execute(
                    f"SELECT MAX({_quote_identifier(column)}) "
                    f"FROM {_quote_identifier(table)}"
                ).fetchone()
                if row[0] is not None:
                    floor = max(floor, row[0])
        return floor


def make_history_stamper(database_paths, *, clock=time.time_ns):
    """Create a stamper above timestamps in every durable runtime store."""
    paths = (database_paths,) if isinstance(database_paths, str) else tuple(database_paths)
    if not paths:
        raise ValueError("history stamper requires at least one database path")
    # The event schema must exist before the root opens its read-only projection.
    with EventStore(paths[0]):
        pass
    floor = max(
        (_database_floor(path) for path in paths if Path(path).exists()),
        default=0,
    )
    return MonotonicStamper(clock=lambda: max(clock(), floor + 1))
