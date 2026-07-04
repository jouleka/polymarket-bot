"""Shadow trade ledger (S9 / POL-11).

Append-only, point-in-time SQLite store of the harness's SIMULATED maker trades and their
eventual resolutions -- the substrate ``pnl.window_net`` and the evidence evaluator window
over. Mirrors the S8 ``MakerLedger`` exactly (WAL + synchronous=NORMAL, stamper timestamps,
Decimals stored as exact strings, INSERT OR IGNORE idempotency) so a shadow trade is
recorded with the same no-backfill honesty as a real maker fill: garbage must never enter,
and DISPUTED/VOID rows are kept but excluded from the honest net sample downstream
(whale-flip immunity). The ONLY differences from ``MakerLedger``: the table is
``shadow_trades``, the record is ``ShadowTradeRecord``, and ``settled()`` orders by
``settled_at`` then ``rowid`` (a shadow trade's *resolution* time is its window key).
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

_COLUMNS = ("trade_id, token_id, condition_id, category, side, shares, fill_price, "
            "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at")


@dataclass(frozen=True)
class ShadowTradeRecord:
    trade_id: str
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    fill_price: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal
    created_at: int
    status: str | None = None
    resolution_value: Decimal | None = None
    settled_at: int | None = None


class ShadowLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_trades (
                trade_id         TEXT PRIMARY KEY,
                token_id         TEXT    NOT NULL,
                condition_id     TEXT    NOT NULL,
                category         TEXT    NOT NULL,
                side             TEXT    NOT NULL,
                shares           TEXT    NOT NULL,
                fill_price       TEXT    NOT NULL,
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

    def record_trade(self, trade_id, *, token_id, condition_id, category, side, shares,
                     fill_price, fill_mid, reward_accrued):
        """INSERT a simulated trade (idempotent on ``trade_id``). Returns True if newly
        inserted, False if a duplicate (original preserved). Decimals stored as exact
        strings."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO shadow_trades "
            "(trade_id, token_id, condition_id, category, side, shares, fill_price, "
            "fill_mid, reward_accrued, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, token_id, condition_id, category, side, str(shares),
             str(fill_price), str(fill_mid), str(reward_accrued), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_settlement(self, trade_id, *, status, resolution_value):
        """Set the trade's resolution (overwrites -- a UMA dispute can flip an apparent
        WON to DISPUTED later; the flip clears the stale resolution value)."""
        self._conn.execute(
            "UPDATE shadow_trades SET status=?, resolution_value=?, settled_at=? "
            "WHERE trade_id=?",
            (status, None if resolution_value is None else str(resolution_value),
             self._stamper.stamp(), trade_id),
        )
        self._conn.commit()

    def settled(self, category=None):
        sql = f"SELECT {_COLUMNS} FROM shadow_trades WHERE status IS NOT NULL"
        params = ()
        if category is not None:
            sql += " AND category=?"
            params = (category,)
        return self._query(sql + " ORDER BY settled_at, rowid", params)

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM shadow_trades ORDER BY rowid")

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
        return ShadowTradeRecord(
            trade_id=r[0], token_id=r[1], condition_id=r[2], category=r[3], side=r[4],
            shares=Decimal(r[5]), fill_price=Decimal(r[6]), fill_mid=Decimal(r[7]),
            reward_accrued=Decimal(r[8]), created_at=r[9], status=r[10],
            resolution_value=None if r[11] is None else Decimal(r[11]), settled_at=r[12],
        )
