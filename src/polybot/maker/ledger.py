"""Maker fill/settlement ledger (S8 / POL-10).

Append-only, point-in-time SQLite store of the maker's OWN fills and their eventual
settlements -- the substrate MakerTracker derives every net-PnL leg from. Mirrors the
calibration ForecastLedger exactly (WAL + synchronous=NORMAL, stamper timestamps,
Decimals stored as exact strings). Like that ledger it cannot be backfilled, so garbage
must never enter it; DISPUTED/VOID rows are kept but excluded from the honest net
sample by the tracker (whale-flip immunity).
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

# Honest win/loss vs the two statuses excluded from the net sample: a whale-captured UMA
# dispute (DISPUTED) and a refund/50-50 (VOID) must not poison the maker's net-PnL.
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")

_COLUMNS = ("fill_id, token_id, condition_id, category, side, shares, price_exec, "
            "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at")


@dataclass(frozen=True)
class MakerFillRecord:
    fill_id: str
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal
    created_at: int
    status: str | None = None
    resolution_value: Decimal | None = None
    settled_at: int | None = None


class MakerLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maker_fills (
                fill_id          TEXT PRIMARY KEY,
                token_id         TEXT    NOT NULL,
                condition_id     TEXT    NOT NULL,
                category         TEXT    NOT NULL,
                side             TEXT    NOT NULL,
                shares           TEXT    NOT NULL,
                price_exec       TEXT    NOT NULL,
                fill_mid         TEXT    NOT NULL,
                reward_accrued   TEXT    NOT NULL,
                created_at       INTEGER NOT NULL,
                status           TEXT,
                resolution_value TEXT,
                settled_at       INTEGER
            )
            """
        )
        self._conn.commit()

    def record_fill(self, fill_id, *, token_id, condition_id, category, side, shares,
                    price_exec, fill_mid, reward_accrued):
        """INSERT a fill (idempotent on ``fill_id``). Returns True if newly inserted,
        False if a duplicate (original preserved). Decimals stored as exact strings."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO maker_fills "
            "(fill_id, token_id, condition_id, category, side, shares, price_exec, "
            "fill_mid, reward_accrued, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fill_id, token_id, condition_id, category, side, str(shares),
             str(price_exec), str(fill_mid), str(reward_accrued), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM maker_fills ORDER BY rowid")

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
        return MakerFillRecord(
            fill_id=r[0], token_id=r[1], condition_id=r[2], category=r[3], side=r[4],
            shares=Decimal(r[5]), price_exec=Decimal(r[6]), fill_mid=Decimal(r[7]),
            reward_accrued=Decimal(r[8]), created_at=r[9], status=r[10],
            resolution_value=None if r[11] is None else Decimal(r[11]), settled_at=r[12],
        )
