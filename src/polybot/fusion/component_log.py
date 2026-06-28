"""Per-signal component log (S6 / POL-8, DESIGN §4.6).

Append-only, point-in-time SQLite sidecar of the per-signal fusion breakdown, keyed by
``forecast_id`` (= ``intent_id``): the four raw inputs ``{p_news, p_base, p_micro, p_flow}``
plus ``w_news_effective``, ``corroborated`` and the contemporaneous market ``mid``.

This is the substrate the DEFERRED adaptive per-signal calibration slice (EMA w_i, auto-zero,
isotonic recalibrator) needs to grade each signal on S6-era data. Like the Market-Memory
EventStore and the ForecastLedger it CANNOT be backfilled, so it is written from day one.

ISOLATION: this is a SIDECAR. It deliberately does NOT import, subclass, or touch POL-7's tested
``calibration/ledger.py`` ForecastLedger -- it mirrors that store's idempotent-INSERT + fail-loud
validation patterns in its own table so the 377 existing tests stay green. It shares ONLY the one
process-wide MonotonicStamper (the global total-order contract, core/clock.py).
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

_COLUMNS = ("forecast_id, p_news, p_base, p_micro, p_flow, "
            "w_news_effective, corroborated, mid, recorded_at")


@dataclass(frozen=True)
class ComponentRecord:
    forecast_id: str
    p_news: Decimal
    p_base: Decimal
    p_micro: Decimal
    p_flow: Decimal
    w_news_effective: float
    corroborated: bool
    mid: Decimal
    recorded_at: int


class ComponentLog:
    def __init__(self, path, *, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS components (
                forecast_id       TEXT PRIMARY KEY,
                p_news            TEXT    NOT NULL,
                p_base            TEXT    NOT NULL,
                p_micro           TEXT    NOT NULL,
                p_flow            TEXT    NOT NULL,
                w_news_effective  REAL    NOT NULL,
                corroborated      INTEGER NOT NULL,
                mid               TEXT    NOT NULL,
                recorded_at       INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def record(self, forecast_id, *, p_news, p_base, p_micro, p_flow,
               w_news_effective, corroborated, mid):
        """Append the per-signal breakdown for ``forecast_id`` (idempotent on it). Returns True
        if newly inserted, False on a duplicate. Numeric probabilities stored as exact strings.

        Fails LOUD on a non-finite or out-of-[0,1] probability/price: the calibration substrate
        cannot be backfilled, so a NaN/Inf component must never enter it."""
        for name, value in (("p_news", p_news), ("p_base", p_base),
                            ("p_micro", p_micro), ("p_flow", p_flow), ("mid", mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite probability in [0, 1], got {value}")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO components "
            "(forecast_id, p_news, p_base, p_micro, p_flow, "
            " w_news_effective, corroborated, mid, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (forecast_id, str(p_news), str(p_base), str(p_micro), str(p_flow),
             float(w_news_effective), 1 if corroborated else 0, str(mid),
             self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM components ORDER BY rowid")

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _query(self, sql, params=()):
        return tuple(self._row(r) for r in self._conn.execute(sql, params).fetchall())

    @staticmethod
    def _row(r):
        return ComponentRecord(
            forecast_id=r[0], p_news=Decimal(r[1]), p_base=Decimal(r[2]),
            p_micro=Decimal(r[3]), p_flow=Decimal(r[4]),
            w_news_effective=float(r[5]), corroborated=bool(r[6]),
            mid=Decimal(r[7]), recorded_at=r[8],
        )
