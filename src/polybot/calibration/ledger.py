"""Forecast->outcome ledger (S5 / POL-7).

Append-only, point-in-time SQLite store of the bot's OWN forecasts and their eventual
resolutions -- the substrate the calibration tracker scores over. Records the contemporaneous
market mid alongside each forecast (the just-quote-the-market baseline). Like the Market-Memory
EventStore it cannot be backfilled. The forecast SOURCE (the ERS on a real proposal / Hermes) is
wired in S6; here the ledger is built + tested standalone.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from polybot.resolution.models import DisputeState, ResolutionSubject, TerminalResolution
from polybot.resolution.errors import ConditionAlreadyTerminal, SettlementConflict

# Honest win/loss vs the two non-honest outcomes excluded from Brier/k: a whale-captured UMA
# flip (DISPUTED_LOST) and a refund/50-50 (VOID) must not poison calibration.
VALID_STATUSES = ("WON", "LOST", "DISPUTED_LOST", "VOID")

_COLUMNS = ("forecast_id, category, condition_id, p, market_mid, created_at, "
            "resolution_status, resolved_at, event_id, token_id, outcome_slot, "
            "sibling_token_ids, resolution_value, resolution_numerator, "
            "resolution_denominator, terminal_id")

_POL15_COLUMNS = (
    ("event_id", "TEXT"),
    ("token_id", "TEXT"),
    ("outcome_slot", "INTEGER"),
    ("sibling_token_ids", "TEXT"),
    ("resolution_value", "TEXT"),
    ("resolution_numerator", "TEXT"),
    ("resolution_denominator", "TEXT"),
    ("terminal_id", "TEXT"),
)


def _decode_identity(*, event_id, token_id, outcome_slot, sibling_json, terminal_id,
                     condition_id, category):
    identity = (event_id, token_id, outcome_slot, sibling_json)
    if all(value is None for value in identity):
        if terminal_id is not None:
            raise SettlementConflict("forecast row has terminal state without canonical identity")
        return None
    if any(value is None for value in identity):
        raise SettlementConflict("forecast row has mixed canonical identity")
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
        raise SettlementConflict("forecast row has invalid canonical identity") from exc
    if (isinstance(outcome_slot, bool) or outcome_slot not in (0, 1)
            or subject.token_ids[outcome_slot] != token_id):
        raise SettlementConflict("forecast row identity slot does not match token")
    return subject.token_ids


def _terminal_projection(terminal, slot):
    numerator = terminal.payout.numerators[slot]
    denominator = terminal.payout.denominator
    if terminal.dispute is not DisputeState.CLEAR:
        return "DISPUTED_LOST", None, numerator, denominator
    value = str(terminal.payout.decimal_for(slot))
    if numerator == denominator:
        status = "WON"
    elif numerator == 0:
        status = "LOST"
    else:
        status = "VOID"
    return status, value, numerator, denominator


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
    event_id: str | None = None
    token_id: str | None = None
    outcome_slot: int | None = None
    sibling_token_ids: tuple[str, str] | None = None
    resolution_value: Decimal | None = None
    resolution_numerator: int | None = None
    resolution_denominator: int | None = None
    terminal_id: str | None = None


class ForecastLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
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
                resolved_at       INTEGER,
                event_id          TEXT,
                token_id          TEXT,
                outcome_slot      INTEGER,
                sibling_token_ids TEXT,
                resolution_value  TEXT,
                resolution_numerator TEXT,
                resolution_denominator TEXT,
                terminal_id       TEXT
            )
            """
        )
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(forecasts)").fetchall()
        }
        for name, sql_type in _POL15_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE forecasts ADD COLUMN {name} {sql_type}")
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

    @contextmanager
    def signing_guard(self, condition_id):
        """Serialize the final open-condition check with terminal receipt insertion.

        The caller must keep this context open through its irreversible signing action. An
        ``IMMEDIATE`` transaction takes SQLite's writer reservation before re-checking the
        receipt, so a competing settlement writer is ordered either wholly before the check
        (and signing is refused) or wholly after the guarded action.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self.require_condition_open(condition_id)
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def record_forecast(self, forecast_id, *, category, condition_id, p, market_mid,
                        event_id=None, token_id=None, outcome_slot=None,
                        sibling_token_ids=None):
        """INSERT a pending forecast (idempotent on ``forecast_id``). Returns True if newly
        inserted, False if a duplicate. Numeric fields stored as exact strings.

        Fail LOUD on a non-finite or out-of-[0,1] probability/price: the calibration substrate
        cannot be backfilled, so garbage (a NaN/Inf forecast) must never enter it (review H1)."""
        for name, value in (("p", p), ("market_mid", market_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite probability in [0, 1], got {value}")
        identity = (event_id, token_id, outcome_slot, sibling_token_ids)
        if any(value is not None for value in identity):
            if any(value is None for value in identity):
                raise ValueError("canonical forecast identity must be all-or-none")
            subject = ResolutionSubject(
                event_id=event_id,
                condition_id=condition_id,
                token_ids=sibling_token_ids,
                category=category,
            )
            if (isinstance(outcome_slot, bool) or not isinstance(outcome_slot, int)
                    or outcome_slot not in (0, 1)):
                raise ValueError("canonical forecast outcome slot must be 0 or 1")
            if subject.token_ids[outcome_slot] != token_id:
                raise ValueError("canonical forecast slot does not match selected token")
            sibling_json = json.dumps(
                list(subject.token_ids), ensure_ascii=False, separators=(",", ":")
            )
        else:
            sibling_json = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self.require_condition_open(condition_id)
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO forecasts "
                "(forecast_id, category, condition_id, p, market_mid, created_at, event_id, "
                "token_id, outcome_slot, sibling_token_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    forecast_id, category, condition_id, str(p), str(market_mid),
                    self._stamper.stamp(), event_id, token_id, outcome_slot, sibling_json,
                ),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return cur.rowcount > 0

    def record_resolution(self, forecast_id, status):
        """Set the forecast's resolution (overwrites — a UMA dispute can flip an apparent WON to
        DISPUTED_LOST later). Fails LOUD on an unknown status or an unknown forecast_id."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid resolution status {status!r}; expected one of {VALID_STATUSES}")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT event_id, token_id, outcome_slot, sibling_token_ids, terminal_id "
                "FROM forecasts WHERE forecast_id=?", (forecast_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no forecast {forecast_id!r} to resolve")
            if any(value is not None for value in row):
                raise SettlementConflict("legacy forecast mutator cannot resolve canonical rows")
            self._conn.execute(
                "UPDATE forecasts SET resolution_status=?, resolved_at=? WHERE forecast_id=?",
                (status, self._stamper.stamp(), forecast_id),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def apply_terminal(self, terminal):
        """Apply one classified terminal and its immutable receipt in one transaction."""
        if not isinstance(terminal, TerminalResolution):
            raise TypeError("terminal must be a TerminalResolution")
        if terminal.dispute not in (
                DisputeState.CLEAR, DisputeState.DISPUTED, DisputeState.MANUAL):
            raise ValueError("forecast terminal path must be classified")
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
                    raise SettlementConflict("forecast receipt contradicts terminal payload")
            stored_rows = self._conn.execute(
                "SELECT forecast_id, category, event_id, token_id, outcome_slot, "
                "sibling_token_ids, resolution_status, resolved_at, resolution_value, "
                "resolution_numerator, resolution_denominator, terminal_id FROM forecasts "
                "WHERE condition_id=?",
                (terminal.subject.condition_id,),
            ).fetchall()
            rows = []
            for (forecast_id, category, event_id, token_id, slot, sibling_json, status,
                 resolved_at, stored_value, stored_numerator, stored_denominator,
                 row_terminal_id) in stored_rows:
                identity = (event_id, token_id, slot, sibling_json)
                if all(value is None for value in identity):
                    if row_terminal_id is not None:
                        raise SettlementConflict("forecast terminal state lacks identity")
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
                    raise SettlementConflict("forecast identity contradicts terminal subject")
                expected = _terminal_projection(terminal, slot)
                if row_terminal_id is None:
                    if any(value is not None for value in (
                            status, resolved_at, stored_value, stored_numerator,
                            stored_denominator)):
                        raise SettlementConflict("forecast pending row has settled state")
                    rows.append((forecast_id, slot))
                    continue
                if receipt is None:
                    raise SettlementConflict("forecast settled row has no terminal receipt")
                expected_status, expected_value, numerator, denominator = expected
                if (row_terminal_id != terminal_id or status != expected_status
                        or resolved_at is None or stored_value != expected_value
                        or stored_numerator != str(numerator)
                        or stored_denominator != str(denominator)):
                    raise SettlementConflict("forecast settled projection contradicts terminal")
            if receipt is not None:
                if rows:
                    raise SettlementConflict("forecast receipt coexists with pending rows")
                self._conn.commit()
                return 0
            self._conn.execute(
                "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
                "VALUES (?, ?, ?)",
                (
                    terminal.subject.condition_id,
                    terminal_id,
                    payload,
                ),
            )
            for forecast_id, slot in rows:
                status, resolution_value, numerator, denominator = _terminal_projection(
                    terminal, slot
                )
                self._conn.execute(
                    "UPDATE forecasts SET resolution_status=?, resolved_at=?, "
                    "resolution_value=?, resolution_numerator=?, resolution_denominator=?, "
                    "terminal_id=? WHERE forecast_id=?",
                    (
                        status, self._stamper.stamp(), resolution_value,
                        str(numerator), str(denominator), terminal_id, forecast_id,
                    ),
                )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return len(rows)

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
        siblings = _decode_identity(
            event_id=r[8], token_id=r[9], outcome_slot=r[10], sibling_json=r[11],
            terminal_id=r[15], condition_id=r[2], category=r[1],
        )
        return ForecastRecord(
            forecast_id=r[0], category=r[1], condition_id=r[2], p=Decimal(r[3]),
            market_mid=Decimal(r[4]), created_at=r[5], resolution_status=r[6], resolved_at=r[7],
            event_id=r[8], token_id=r[9], outcome_slot=r[10],
            sibling_token_ids=siblings,
            resolution_value=None if r[12] is None else Decimal(r[12]),
            resolution_numerator=None if r[13] is None else int(r[13]),
            resolution_denominator=None if r[14] is None else int(r[14]), terminal_id=r[15],
        )
