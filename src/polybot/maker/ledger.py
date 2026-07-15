"""Maker fill/settlement ledger (S8 / POL-10).

Append-only, point-in-time SQLite store of the maker's OWN fills and their eventual
settlements -- the substrate MakerTracker derives every net-PnL leg from. Mirrors the
calibration ForecastLedger exactly (WAL + synchronous=FULL, stamper timestamps,
Decimals stored as exact strings). Like that ledger it cannot be backfilled, so garbage
must never enter it; DISPUTED/VOID rows are kept but excluded from the honest net
sample by the tracker (whale-flip immunity).
"""

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from polybot.resolution.models import (
    DisputeState,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.errors import ConditionAlreadyTerminal, SettlementConflict

# Honest win/loss vs the two statuses excluded from the net sample: a whale-captured UMA
# dispute (DISPUTED) and a refund/50-50 (VOID) must not poison the maker's net-PnL.
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")

_COLUMNS = ("fill_id, token_id, condition_id, category, side, shares, price_exec, "
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


def _decode_identity(*, event_id, token_id, outcome_slot, sibling_json, terminal_id,
                     condition_id, category):
    identity = (event_id, outcome_slot, sibling_json)
    if all(value is None for value in identity):
        if terminal_id is not None:
            raise SettlementConflict("maker row has terminal state without canonical identity")
        return None
    if any(value is None for value in identity):
        raise SettlementConflict("maker row has mixed canonical identity")
    try:
        decoded_siblings = json.loads(sibling_json)
        if (not isinstance(decoded_siblings, list) or len(decoded_siblings) != 2
                or any(not isinstance(value, str) for value in decoded_siblings)
                or sibling_json != json.dumps(
                    decoded_siblings, ensure_ascii=False, separators=(",", ":")
                )):
            raise ValueError("sibling token identity is not a canonical JSON array")
        siblings = tuple(decoded_siblings)
        subject = ResolutionSubject(event_id, condition_id, siblings, category)
    except (TypeError, ValueError) as exc:
        raise SettlementConflict("maker row has invalid canonical identity") from exc
    if (isinstance(outcome_slot, bool) or outcome_slot not in (0, 1)
            or subject.token_ids[outcome_slot] != token_id):
        raise SettlementConflict("maker row identity slot does not match token")
    return subject.token_ids


def _terminal_projection(terminal, slot):
    numerator = terminal.payout.numerators[slot]
    denominator = terminal.payout.denominator
    if terminal.dispute is not DisputeState.CLEAR:
        return "DISPUTED", None, numerator, denominator
    value = str(terminal.payout.decimal_for(slot))
    if numerator == denominator:
        status = "WON"
    elif numerator == 0:
        status = "LOST"
    else:
        status = "SETTLED"
    return status, value, numerator, denominator


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
    event_id: str | None = None
    outcome_slot: int | None = None
    sibling_token_ids: tuple[str, str] | None = None
    resolution_numerator: int | None = None
    resolution_denominator: int | None = None
    terminal_id: str | None = None


class MakerLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
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
            row[1] for row in self._conn.execute("PRAGMA table_info(maker_fills)").fetchall()
        }
        for name, sql_type in _POL15_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE maker_fills ADD COLUMN {name} {sql_type}")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_receipts (
                condition_id TEXT PRIMARY KEY,
                terminal_id  TEXT UNIQUE NOT NULL,
                payload      BLOB NOT NULL
            )
            """
        )
        self._conn.commit()

    def require_condition_open(self, condition_id):
        receipt = self._conn.execute(
            "SELECT 1 FROM resolution_receipts WHERE condition_id=?", (condition_id,)
        ).fetchone()
        if receipt is not None:
            raise ConditionAlreadyTerminal(
                f"condition {condition_id!r} already has a terminal receipt"
            )

    def apply_shadow_execution(self, execution):
        """Project one typed POL-16 paper execution into the maker ledger."""
        from polybot.ers.intent_store import ShadowExecutionRecord

        if not isinstance(execution, ShadowExecutionRecord):
            raise TypeError("execution must be a ShadowExecutionRecord")
        try:
            changed = self.record_fill(
                execution.execution_id,
                token_id=execution.token_id,
                condition_id=execution.condition_id,
                category=execution.category,
                side=execution.side,
                shares=execution.shares,
                price_exec=execution.price_exec,
                fill_mid=execution.fill_mid,
                reward_accrued=execution.reward_accrued,
                event_id=execution.event_id,
                outcome_slot=execution.outcome_slot,
                sibling_token_ids=execution.sibling_token_ids,
            )
        except ConditionAlreadyTerminal:
            return self._apply_shadow_execution_after_terminal(execution)
        if not changed:
            row = self._query(
                f"SELECT {_COLUMNS} FROM maker_fills WHERE fill_id=?",
                (execution.execution_id,),
            )[0]
            actual = (
                row.token_id, row.condition_id, row.category, row.side, row.shares,
                row.price_exec, row.fill_mid, row.reward_accrued, row.event_id,
                row.outcome_slot, row.sibling_token_ids,
            )
            expected = (
                execution.token_id, execution.condition_id, execution.category,
                execution.side, execution.shares, execution.price_exec, execution.fill_mid,
                execution.reward_accrued, execution.event_id, execution.outcome_slot,
                execution.sibling_token_ids,
            )
            if actual != expected:
                raise SettlementConflict("maker row contradicts shadow execution")
        return changed

    def _apply_shadow_execution_after_terminal(self, execution):
        from polybot.resolution.store import decode_terminal

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            receipt = self._conn.execute(
                "SELECT terminal_id, payload FROM resolution_receipts WHERE condition_id=?",
                (execution.condition_id,),
            ).fetchone()
            if receipt is None:
                raise SettlementConflict("maker terminal receipt disappeared during replay")
            terminal = decode_terminal(receipt[0], receipt[1])
            subject = terminal.subject
            if (subject.event_id != execution.event_id
                    or subject.condition_id != execution.condition_id
                    or subject.category != execution.category
                    or subject.token_ids != execution.sibling_token_ids
                    or subject.token_ids[execution.outcome_slot] != execution.token_id):
                raise SettlementConflict("maker terminal subject contradicts shadow execution")
            status, value, numerator, denominator = _terminal_projection(
                terminal, execution.outcome_slot
            )
            sibling_json = json.dumps(
                list(execution.sibling_token_ids), ensure_ascii=False, separators=(",", ":")
            )
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO maker_fills "
                "(fill_id, token_id, condition_id, category, side, shares, price_exec, "
                "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at, "
                "event_id, outcome_slot, sibling_token_ids, resolution_numerator, "
                "resolution_denominator, terminal_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (execution.execution_id, execution.token_id, execution.condition_id,
                 execution.category, execution.side, str(execution.shares),
                 str(execution.price_exec), str(execution.fill_mid),
                 str(execution.reward_accrued), self._stamper.stamp(), status, value,
                 self._stamper.stamp(), execution.event_id, execution.outcome_slot,
                 sibling_json, str(numerator), str(denominator), terminal.terminal_id),
            )
            row = self._query(
                f"SELECT {_COLUMNS} FROM maker_fills WHERE fill_id=?",
                (execution.execution_id,),
            )[0]
            actual = (
                row.token_id, row.condition_id, row.category, row.side, row.shares,
                row.price_exec, row.fill_mid, row.reward_accrued, row.event_id,
                row.outcome_slot, row.sibling_token_ids, row.status, row.resolution_value,
                row.resolution_numerator, row.resolution_denominator, row.terminal_id,
            )
            expected = (
                execution.token_id, execution.condition_id, execution.category,
                execution.side, execution.shares, execution.price_exec, execution.fill_mid,
                execution.reward_accrued, execution.event_id, execution.outcome_slot,
                execution.sibling_token_ids, status,
                None if value is None else Decimal(value), numerator, denominator,
                terminal.terminal_id,
            )
            if actual != expected:
                raise SettlementConflict("maker row contradicts terminal shadow execution")
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return cur.rowcount > 0

    def record_fill(self, fill_id, *, token_id, condition_id, category, side, shares,
                    price_exec, fill_mid, reward_accrued, event_id=None,
                    outcome_slot=None, sibling_token_ids=None):
        """INSERT a fill (idempotent on ``fill_id``). Returns True if newly inserted,
        False if a duplicate (original preserved). Decimals stored as exact strings.

        Fail LOUD at the door: the maker's net-PnL substrate cannot be backfilled, so a
        bad side, a non-positive/non-finite size, an out-of-[0,1] price, or a negative
        reward must never enter it (mirrors the calibration ledger's H1 guard)."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if not shares.is_finite() or shares <= 0:
            raise ValueError(f"shares must be a finite Decimal > 0, got {shares}")
        for name, value in (("price_exec", price_exec), ("fill_mid", fill_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite price in [0, 1], got {value}")
        if not reward_accrued.is_finite() or reward_accrued < 0:
            raise ValueError(
                f"reward_accrued must be a finite Decimal >= 0, got {reward_accrued}")
        identity = (event_id, outcome_slot, sibling_token_ids)
        if any(value is not None for value in identity):
            if any(value is None for value in identity):
                raise ValueError("canonical maker identity must be all-or-none")
            subject = ResolutionSubject(
                event_id=event_id,
                condition_id=condition_id,
                token_ids=sibling_token_ids,
                category=category,
            )
            if (isinstance(outcome_slot, bool) or not isinstance(outcome_slot, int)
                    or outcome_slot not in (0, 1)):
                raise ValueError("canonical maker outcome slot must be 0 or 1")
            if subject.token_ids[outcome_slot] != token_id:
                raise ValueError("canonical maker slot does not match selected token")
            sibling_json = json.dumps(
                list(subject.token_ids), ensure_ascii=False, separators=(",", ":")
            )
        else:
            sibling_json = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self.require_condition_open(condition_id)
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO maker_fills "
                "(fill_id, token_id, condition_id, category, side, shares, price_exec, "
                "fill_mid, reward_accrued, created_at, event_id, outcome_slot, "
                "sibling_token_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fill_id, token_id, condition_id, category, side, str(shares),
                 str(price_exec), str(fill_mid), str(reward_accrued), self._stamper.stamp(),
                 event_id, outcome_slot, sibling_json),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return cur.rowcount > 0

    def record_settlement(self, fill_id, *, status, resolution_value):
        """Set the fill's settlement (overwrites -- a UMA dispute can flip an apparent
        WON to DISPUTED later; the flip clears the stale resolution value). Fails LOUD:
        unknown status or fill_id; a resolution_value inconsistent with the status --
        WON/LOST REQUIRE a finite Decimal in [0, 1] (canonically 1/0 but any settle mark
        accepted); DISPUTED/VOID REQUIRE None (they are excluded from the net sample, so
        a value here is a caller bug)."""
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
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT event_id, outcome_slot, sibling_token_ids, terminal_id "
                "FROM maker_fills WHERE fill_id=?", (fill_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no maker fill {fill_id!r} to settle")
            if any(value is not None for value in row):
                raise SettlementConflict("legacy maker mutator cannot settle canonical rows")
            self._conn.execute(
                "UPDATE maker_fills SET status=?, resolution_value=?, settled_at=? "
                "WHERE fill_id=?",
                (status, None if resolution_value is None else str(resolution_value),
                 self._stamper.stamp(), fill_id),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def apply_terminal(self, terminal):
        """Project a classified terminal into maker rows and an immutable receipt."""
        if not isinstance(terminal, TerminalResolution):
            raise TypeError("terminal must be a TerminalResolution")
        if terminal.dispute not in (
                DisputeState.CLEAR, DisputeState.DISPUTED, DisputeState.MANUAL):
            raise ValueError("maker terminal path must be classified")
        terminal_id = terminal.terminal_id
        payload = terminal.canonical_bytes

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            receipt = self._conn.execute(
                "SELECT terminal_id, payload FROM resolution_receipts WHERE condition_id=?",
                (terminal.subject.condition_id,),
            ).fetchone()
            if receipt is not None:
                if receipt[0] != terminal_id or bytes(receipt[1]) != payload:
                    raise SettlementConflict("maker receipt contradicts terminal payload")
            stored_rows = self._conn.execute(
                "SELECT fill_id, token_id, category, event_id, outcome_slot, "
                "sibling_token_ids, status, settled_at, resolution_value, "
                "resolution_numerator, resolution_denominator, terminal_id FROM maker_fills "
                "WHERE condition_id=?",
                (terminal.subject.condition_id,),
            ).fetchall()
            rows = []
            for (fill_id, token_id, category, event_id, slot, sibling_json, status,
                 settled_at, stored_value, stored_numerator, stored_denominator,
                 row_terminal_id) in stored_rows:
                identity = (event_id, slot, sibling_json)
                if all(value is None for value in identity):
                    if row_terminal_id is not None:
                        raise SettlementConflict("maker terminal state lacks identity")
                    continue
                siblings = _decode_identity(
                    event_id=event_id, token_id=token_id, outcome_slot=slot,
                    sibling_json=sibling_json, terminal_id=row_terminal_id,
                    condition_id=terminal.subject.condition_id, category=category,
                )
                subject = terminal.subject
                if (category != subject.category or event_id != subject.event_id
                        or siblings != subject.token_ids
                        or subject.token_ids[slot] != token_id):
                    raise SettlementConflict("maker identity contradicts terminal subject")
                expected = _terminal_projection(terminal, slot)
                if row_terminal_id is None:
                    if any(value is not None for value in (
                            status, settled_at, stored_value, stored_numerator,
                            stored_denominator)):
                        raise SettlementConflict("maker pending row has settled state")
                    rows.append((fill_id, slot))
                    continue
                if receipt is None:
                    raise SettlementConflict("maker settled row has no terminal receipt")
                expected_status, expected_value, numerator, denominator = expected
                if (row_terminal_id != terminal_id or status != expected_status
                        or settled_at is None or stored_value != expected_value
                        or stored_numerator != str(numerator)
                        or stored_denominator != str(denominator)):
                    raise SettlementConflict("maker settled projection contradicts terminal")
            if receipt is not None:
                if rows:
                    raise SettlementConflict("maker receipt coexists with pending rows")
                self._conn.commit()
                return 0
            self._conn.execute(
                "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
                "VALUES (?, ?, ?)",
                (terminal.subject.condition_id, terminal_id, payload),
            )
            for fill_id, slot in rows:
                status, resolution_value, numerator, denominator = _terminal_projection(
                    terminal, slot
                )
                self._conn.execute(
                    "UPDATE maker_fills SET status=?, resolution_value=?, settled_at=?, "
                    "resolution_numerator=?, resolution_denominator=?, terminal_id=? "
                    "WHERE fill_id=?",
                    (
                        status, resolution_value, self._stamper.stamp(), str(numerator),
                        str(denominator), terminal_id, fill_id,
                    ),
                )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return len(rows)

    def settled(self, category=None):
        sql = f"SELECT {_COLUMNS} FROM maker_fills WHERE status IS NOT NULL"
        params = ()
        if category is not None:
            sql += " AND category=?"
            params = (category,)
        return self._query(sql + " ORDER BY rowid", params)

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
        siblings = _decode_identity(
            event_id=r[13], token_id=r[1], outcome_slot=r[14], sibling_json=r[15],
            terminal_id=r[18], condition_id=r[2], category=r[3],
        )
        return MakerFillRecord(
            fill_id=r[0], token_id=r[1], condition_id=r[2], category=r[3], side=r[4],
            shares=Decimal(r[5]), price_exec=Decimal(r[6]), fill_mid=Decimal(r[7]),
            reward_accrued=Decimal(r[8]), created_at=r[9], status=r[10],
            resolution_value=None if r[11] is None else Decimal(r[11]), settled_at=r[12],
            event_id=r[13], outcome_slot=r[14],
            sibling_token_ids=siblings,
            resolution_numerator=None if r[16] is None else int(r[16]),
            resolution_denominator=None if r[17] is None else int(r[17]), terminal_id=r[18],
        )
