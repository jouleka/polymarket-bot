"""Forecast->outcome ledger (S5 / POL-7).

Append-only, point-in-time SQLite store of the bot's OWN forecasts and their eventual
resolutions -- the substrate the calibration tracker scores over. Records the contemporaneous
market mid alongside each forecast (the just-quote-the-market baseline). Like the Market-Memory
EventStore it cannot be backfilled. The forecast SOURCE (the ERS on a real proposal / Hermes) is
wired in S6; here the ledger is built + tested standalone.
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

# Honest win/loss vs the two non-honest outcomes excluded from Brier/k: a whale-captured UMA
# flip (DISPUTED_LOST) and a refund/50-50 (VOID) must not poison calibration.
VALID_STATUSES = ("WON", "LOST", "DISPUTED_LOST", "VOID")

_COLUMNS = ("forecast_id, category, condition_id, p, market_mid, created_at, "
            "resolution_status, resolved_at")


@dataclass(frozen=True)
class ForecastRecord:
    forecast_id: str
    category: str
    condition_id: str
    p: Decimal
    market_mid: Decimal
    created_at: int
    resolution_status: str | None = None
    resolved_at: int | None = None


class ForecastLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forecasts (
                forecast_id       TEXT PRIMARY KEY,
                category          TEXT    NOT NULL,
                condition_id      TEXT    NOT NULL,
                p                 TEXT    NOT NULL,
                market_mid        TEXT    NOT NULL,
                created_at        INTEGER NOT NULL,
                resolution_status TEXT,
                resolved_at       INTEGER
            )
            """
        )
        self._conn.commit()

    def record_forecast(self, forecast_id, *, category, condition_id, p, market_mid):
        """INSERT a pending forecast (idempotent on ``forecast_id``). Returns True if newly
        inserted, False if a duplicate. Numeric fields stored as exact strings.

        Fail LOUD on a non-finite or out-of-[0,1] probability/price: the calibration substrate
        cannot be backfilled, so garbage (a NaN/Inf forecast) must never enter it (review H1)."""
        for name, value in (("p", p), ("market_mid", market_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite probability in [0, 1], got {value}")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO forecasts "
            "(forecast_id, category, condition_id, p, market_mid, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (forecast_id, category, condition_id, str(p), str(market_mid), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_resolution(self, forecast_id, status):
        """Set the forecast's resolution (overwrites — a UMA dispute can flip an apparent WON to
        DISPUTED_LOST later). Fails LOUD on an unknown status or an unknown forecast_id."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid resolution status {status!r}; expected one of {VALID_STATUSES}")
        cur = self._conn.execute(
            "UPDATE forecasts SET resolution_status=?, resolved_at=? WHERE forecast_id=?",
            (status, self._stamper.stamp(), forecast_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"no forecast {forecast_id!r} to resolve")

    def resolved(self, category=None):
        sql = f"SELECT {_COLUMNS} FROM forecasts WHERE resolution_status IS NOT NULL"
        params = ()
        if category is not None:
            sql += " AND category=?"
            params = (category,)
        return self._query(sql + " ORDER BY rowid", params)

    def get(self, forecast_id):
        rows = self._query(f"SELECT {_COLUMNS} FROM forecasts WHERE forecast_id=?", (forecast_id,))
        return rows[0] if rows else None

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM forecasts ORDER BY rowid")

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _query(self, sql, params=()):
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row(r):
        return ForecastRecord(
            forecast_id=r[0], category=r[1], condition_id=r[2], p=Decimal(r[3]),
            market_mid=Decimal(r[4]), created_at=r[5], resolution_status=r[6], resolved_at=r[7],
        )
