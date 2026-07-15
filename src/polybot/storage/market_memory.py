"""Market-Memory event store (SQLite).

Append-only, point-in-time store of canonical Envelopes. Persists across restart
and replays strictly in observed_at order so backtests never see the future.
"""

import json
from pathlib import Path
import sqlite3

from polybot.core.models import Envelope

_COLUMNS = (
    "observed_at, source, source_tier, event_id, content, "
    "published_at, entities, market_links, trust"
)


class EventStore:
    def __init__(self, path, *, check_same_thread=True):
        # check_same_thread=False lets the off-loop single-writer (POL-12) drive
        # this connection from its dedicated writer thread (the connection is created
        # on one thread, used on another). Default True keeps SQLite's thread-affinity
        # guard for the direct, same-thread callers.
        # NOTE: False DISABLES that guard, so the caller must serialize access itself —
        # QueuedEventWriter owns the connection on its writer thread, and reads (all/
        # replay_until) happen only after close()/join (see the live scripts). Never read
        # this store from another thread while the writer is still running.
        self._conn = sqlite3.connect(path, check_same_thread=check_same_thread)
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
        """MUST return a re-iterable, materialized sequence (a list), never a generator/
        cursor: `ers/reconcile.py make_recon_provider` and `ers/restart.py` each feed ONE
        .all() result to BOTH the clob and onchain leg parsers -- an iterator would be
        silently exhausted by the first parser and the on-chain leg would read empty (a
        silent under-halt)."""
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


class ReadOnlyEventStore:
    """Independent WAL reader for live ERS consumers; deliberately no append API."""

    def __init__(self, path):
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.execute("PRAGMA query_only=ON")

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
        return [EventStore._row_to_envelope(row) for row in rows]
