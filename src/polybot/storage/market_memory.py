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


class EventQueryTooBroad(RuntimeError):
    """A citation lookup exceeded its fixed fail-closed result bound."""


def _recent_by_sources(conn, source_names, *, offset, limit,
                       max_content_chars, max_event_id_chars,
                       priority_sources=()):
    sources = tuple(source_names)
    if (not sources or len(sources) != len(set(sources))
            or any(not isinstance(source, str) or not source for source in sources)):
        raise ValueError("recent event sources must be non-empty unique strings")
    priority = tuple(priority_sources)
    if (len(priority) != len(set(priority))
            or any(not isinstance(source, str) or not source for source in priority)
            or not set(priority).issubset(sources)):
        raise ValueError("priority event sources must be a unique source subset")
    if (isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1000
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50):
        raise ValueError("recent event pagination must be bounded positive integers")
    if (isinstance(max_content_chars, bool) or not isinstance(max_content_chars, int)
            or not 1 <= max_content_chars <= 4096
            or isinstance(max_event_id_chars, bool)
            or not isinstance(max_event_id_chars, int)
            or not 1 <= max_event_id_chars <= 2048):
        raise ValueError("recent event field bounds are invalid")
    placeholders = ",".join("?" for _source in sources)
    if priority:
        priority_slots = ",".join("?" for _source in priority)
        priority_order = f"CASE WHEN source IN ({priority_slots}) THEN 0 ELSE 1 END, "
    else:
        priority_order = ""
    rows = conn.execute(
        "SELECT observed_at, source, source_tier, event_id, "
        "substr(content, 1, ?), published_at, '[]', '[]', 'UNTRUSTED' "
        f"FROM events WHERE source IN ({placeholders}) "
        "AND trust = 'UNTRUSTED' AND length(event_id) <= ? "
        f"ORDER BY {priority_order}observed_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (max_content_chars, *sources, max_event_id_chars, *priority, limit, offset),
    ).fetchall()
    return [EventStore._row_to_envelope(row) for row in rows]


def _matching_citations(conn, citations, source_names, *, max_matches):
    citation_values = tuple(citations)
    sources = tuple(source_names)
    if (len(citation_values) > 32 or len(citation_values) != len(set(citation_values))
            or any(not isinstance(value, str) or not value for value in citation_values)):
        raise ValueError("citation lookup values must be bounded unique strings")
    if (len(sources) != len(set(sources))
            or any(not isinstance(source, str) or not source for source in sources)):
        raise ValueError("citation lookup sources must be unique strings")
    if (isinstance(max_matches, bool) or not isinstance(max_matches, int)
            or not 1 <= max_matches <= 4096):
        raise ValueError("citation lookup result bound is invalid")
    if not citation_values or not sources:
        return []
    source_slots = ",".join("?" for _source in sources)
    citation_slots = ",".join("?" for _citation in citation_values)
    rows = conn.execute(
        "SELECT observed_at, source, source_tier, "
        "CASE WHEN length(event_id) <= 2048 THEN event_id ELSE '' END, "
        "'', published_at, COALESCE(("
        "SELECT json_group_array(entity.value) "
        "FROM json_each(events.entities) AS entity "
        f"WHERE entity.value IN ({citation_slots})"
        "), '[]'), '[]', 'UNTRUSTED' FROM events "
        f"WHERE source IN ({source_slots}) AND ("
        f"event_id IN ({citation_slots}) OR EXISTS ("
        "SELECT 1 FROM json_each(events.entities) AS entity "
        f"WHERE entity.value IN ({citation_slots})"
        ")) AND trust = 'UNTRUSTED' ORDER BY observed_at, rowid LIMIT ?",
        (*citation_values, *sources,
         *citation_values, *citation_values, max_matches + 1),
    ).fetchall()
    if len(rows) > max_matches:
        raise EventQueryTooBroad("citation lookup exceeded its fixed result bound")
    return [EventStore._row_to_envelope(row) for row in rows]


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

    def recent_by_sources(self, source_names, *, offset, limit,
                          max_content_chars=4096, max_event_id_chars=2048,
                          priority_sources=()):
        """Return one bounded newest-first page for exact configured sources."""
        return _recent_by_sources(
            self._conn, source_names, offset=offset, limit=limit,
            max_content_chars=max_content_chars,
            max_event_id_chars=max_event_id_chars,
            priority_sources=priority_sources,
        )

    def matching_citations(self, citations, source_names, *, max_matches=1024):
        """Return only bounded exact citation matches for configured sources."""
        return _matching_citations(
            self._conn, citations, source_names, max_matches=max_matches,
        )

    def max_observed_at(self):
        """Return the durable history floor, or zero for an empty store."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(observed_at), 0) FROM events"
        ).fetchone()
        return row[0]

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

    def recent_by_sources(self, source_names, *, offset, limit,
                          max_content_chars=4096, max_event_id_chars=2048,
                          priority_sources=()):
        """Return one bounded newest-first page for exact configured sources."""
        return _recent_by_sources(
            self._conn, source_names, offset=offset, limit=limit,
            max_content_chars=max_content_chars,
            max_event_id_chars=max_event_id_chars,
            priority_sources=priority_sources,
        )

    def matching_citations(self, citations, source_names, *, max_matches=1024):
        """Return only bounded exact citation matches for configured sources."""
        return _matching_citations(
            self._conn, citations, source_names, max_matches=max_matches,
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
