"""Market-Memory event store (SQLite).

Append-only, point-in-time store of canonical Envelopes. Persists across restart
and replays strictly in observed_at order so backtests never see the future.
"""

import json
import sqlite3

from polybot.core.models import Envelope

_COLUMNS = (
    "observed_at, source, source_tier, event_id, content, "
    "published_at, entities, market_links, trust"
)


class EventStore:
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        # Durability for a substrate that cannot be backfilled: WAL keeps readers
        # non-blocking and survives an app crash; NORMAL fsyncs at checkpoints
        # (full per-write fsync is too slow for live minute-bars / WS events).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                observed_at  INTEGER NOT NULL,
                source       TEXT    NOT NULL,
                source_tier  TEXT    NOT NULL,
                event_id     TEXT    NOT NULL,
                content      TEXT    NOT NULL,
                published_at INTEGER,
                entities     TEXT    NOT NULL,
                market_links TEXT    NOT NULL,
                trust        TEXT    NOT NULL,
                UNIQUE (source, event_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_observed_at ON events (observed_at)"
        )
        self._conn.commit()

    def append(self, envelope):
        self._conn.execute(
            f"INSERT OR IGNORE INTO events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                envelope.observed_at,
                envelope.source,
                envelope.source_tier,
                envelope.event_id,
                envelope.content,
                envelope.published_at,
                json.dumps(list(envelope.entities)),
                json.dumps(list(envelope.market_links)),
                envelope.trust,
            ),
        )
        self._conn.commit()

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM events ORDER BY observed_at, rowid")

    def replay_until(self, observed_at_cutoff):
        return self._query(
            f"SELECT {_COLUMNS} FROM events WHERE observed_at <= ? "
            "ORDER BY observed_at, rowid",
            (observed_at_cutoff,),
        )

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _query(self, sql, params=()):
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_envelope(row) for row in rows]

    @staticmethod
    def _row_to_envelope(row):
        return Envelope(
            observed_at=row[0],
            source=row[1],
            source_tier=row[2],
            event_id=row[3],
            content=row[4],
            published_at=row[5],
            entities=tuple(json.loads(row[6])),
            market_links=tuple(json.loads(row[7])),
            trust=row[8],
        )
