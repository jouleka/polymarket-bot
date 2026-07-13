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

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

# Honest win/loss vs the two statuses excluded from the net sample: a whale-captured UMA
# dispute (DISPUTED) and a refund/50-50 (VOID) must not poison the shadow net-PnL.
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")

_COLUMNS = ("trade_id, token_id, condition_id, category, side, shares, fill_price, "
            "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at, "
            "event_id, outcome_slot, sibling_token_ids, resolution_numerator, "
            "resolution_denominator, terminal_id")

_POL15_COLUMNS = (
    ("event_id", "TEXT"),
    ("outcome_slot", "INTEGER"),
    ("sibling_token_ids", "TEXT"),
    ("resolution_numerator", "TEXT"),
    ("resolution_denominator", "TEXT"),
    ("terminal_id", "TEXT"),
)


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
    event_id: str | None = None
    outcome_slot: int | None = None
    sibling_token_ids: tuple[str, str] | None = None
    resolution_numerator: int | None = None
    resolution_denominator: int | None = None
    terminal_id: str | None = None


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
                settled_at       INTEGER,
                event_id         TEXT,
                outcome_slot     INTEGER,
                sibling_token_ids TEXT,
                resolution_numerator TEXT,
                resolution_denominator TEXT,
                terminal_id      TEXT
            )
            """
        )
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(shadow_trades)").fetchall()
        }
        for name, sql_type in _POL15_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE shadow_trades ADD COLUMN {name} {sql_type}")
        self._conn.commit()

    def record_trade(self, trade_id, *, token_id, condition_id, category, side, shares,
                     fill_price, fill_mid, reward_accrued):
        """INSERT a simulated trade (idempotent on ``trade_id``). Returns True if newly
        inserted, False if a duplicate (original preserved). Decimals stored as exact
        strings.

        Fail LOUD at the door (mirrors ``MakerLedger.record_fill``): the shadow net-PnL
        substrate cannot be backfilled, so a bad side, a non-positive/non-finite size, an
        out-of-[0,1] price, or a negative reward must never enter it."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if not shares.is_finite() or shares <= 0:
            raise ValueError(f"shares must be a finite Decimal > 0, got {shares}")
        for name, value in (("fill_price", fill_price), ("fill_mid", fill_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite price in [0, 1], got {value}")
        if not reward_accrued.is_finite() or reward_accrued < 0:
            raise ValueError(
                f"reward_accrued must be a finite Decimal >= 0, got {reward_accrued}")
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
        WON to DISPUTED later; the flip clears the stale resolution value). Fails LOUD:
        unknown status or trade_id; a resolution_value inconsistent with the status --
        WON/LOST REQUIRE a finite Decimal in [0, 1] (canonically 1/0 but any settle mark
        accepted); DISPUTED/VOID REQUIRE None (they are excluded from the net sample, so a
        value here is a caller bug)."""
        if status not in VALID_STATUSES:
            raise ValueError(
                f"invalid settlement status {status!r}; expected one of {VALID_STATUSES}")
        if status in ("WON", "LOST"):
            if (resolution_value is None or not resolution_value.is_finite()
                    or not (Decimal(0) <= resolution_value <= Decimal(1))):
                raise ValueError(
                    f"resolution_value must be a finite Decimal in [0, 1] for {status}, "
                    f"got {resolution_value}")
        elif resolution_value is not None:
            raise ValueError(
                f"resolution_value must be None for {status}, got {resolution_value}")
        cur = self._conn.execute(
            "UPDATE shadow_trades SET status=?, resolution_value=?, settled_at=? "
            "WHERE trade_id=?",
            (status, None if resolution_value is None else str(resolution_value),
             self._stamper.stamp(), trade_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"no shadow trade {trade_id!r} to settle")

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
            event_id=r[13], outcome_slot=r[14],
            sibling_token_ids=None if r[15] is None else tuple(json.loads(r[15])),
            resolution_numerator=None if r[16] is None else int(r[16]),
            resolution_denominator=None if r[17] is None else int(r[17]), terminal_id=r[18],
        )
