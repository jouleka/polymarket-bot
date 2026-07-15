"""ERS chokepoint store (S3 / POL-5 slice 2).

The entire safety model: Hermes is given ONLY ``propose_trade(...)``, which does nothing
but INSERT a ``PROPOSED`` row -- it has no ``status`` parameter and no sign/submit/cancel
method, so a confused-deputy Hermes can at worst enqueue a proposal. The deterministic ERS
(NOT Hermes) polls ``pending()``, runs the validator, and ``record_decision`` transitions
the status + appends an IMMUTABLE audit row. Mutable ``pending_intents`` (status lifecycle)
is deliberately separate from the append-only Market-Memory EventStore.
"""

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from polybot.resolution.errors import SettlementConflict

_PROPOSED = "PROPOSED"
_STATUS_FOR_VERDICT = {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "SKIP": "SKIPPED"}
_SHADOW_ROLES = ("MAKER", "SHADOW")

_COLUMNS = (
    "intent_id, status, token_id, condition_id, event_id, side, target_price, max_price, "
    "size_usd_suggestion, p, p_confidence, resolution_summary, thesis, citations, created_at, "
    "decided_at, decision_verdict, decision_stake_usd, decision_price_exec, decision_reason"
)


@dataclass(frozen=True)
class PendingIntent:
    intent_id: str
    status: str
    token_id: str
    condition_id: str
    event_id: str
    side: str
    target_price: Decimal
    max_price: Decimal
    size_usd_suggestion: Decimal
    p: Decimal
    p_confidence: Decimal
    resolution_summary: str
    thesis: str
    citations: tuple
    created_at: int
    decided_at: int | None = None
    decision_verdict: str | None = None
    decision_stake_usd: Decimal | None = None
    decision_price_exec: Decimal | None = None
    decision_reason: str | None = None


@dataclass(frozen=True)
class ShadowExecutionRecord:
    """Canonical filled paper execution persisted with its ERS ACCEPT decision."""

    execution_id: str
    token_id: str
    condition_id: str
    event_id: str
    category: str
    outcome_slot: int
    sibling_token_ids: tuple[str, str]
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal

    def __post_init__(self):
        for name in ("execution_id", "token_id", "condition_id", "event_id", "category"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if (isinstance(self.outcome_slot, bool) or not isinstance(self.outcome_slot, int)
                or self.outcome_slot not in (0, 1)):
            raise ValueError("outcome_slot must be 0 or 1")
        siblings = self.sibling_token_ids
        if (not isinstance(siblings, tuple) or len(siblings) != 2
                or any(not isinstance(value, str) or not value for value in siblings)
                or siblings[0] == siblings[1]):
            raise ValueError("sibling_token_ids must contain two distinct non-empty strings")
        if siblings[self.outcome_slot] != self.token_id:
            raise ValueError("outcome_slot does not select token_id")
        if self.side != "BUY":
            raise ValueError("shadow execution side must be BUY")
        if not self.shares.is_finite() or self.shares <= 0:
            raise ValueError("shares must be finite and > 0")
        for name in ("price_exec", "fill_mid"):
            value = getattr(self, name)
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be finite and in [0, 1]")
        if not self.reward_accrued.is_finite() or self.reward_accrued < 0:
            raise ValueError("reward_accrued must be finite and >= 0")


@dataclass(frozen=True)
class ShadowExecutionOutboxRecord:
    sequence: int
    role: str
    execution: ShadowExecutionRecord

    def __post_init__(self):
        if (isinstance(self.sequence, bool) or not isinstance(self.sequence, int)
                or self.sequence <= 0):
            raise ValueError("outbox sequence must be a positive integer")
        if self.role not in _SHADOW_ROLES:
            raise ValueError("shadow execution outbox role is invalid")
        if not isinstance(self.execution, ShadowExecutionRecord):
            raise TypeError("outbox execution must be a ShadowExecutionRecord")


def _dec(value):
    return None if value is None else Decimal(value)


class IntentStore:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_intents (
                intent_id           TEXT PRIMARY KEY,
                status              TEXT    NOT NULL,
                token_id            TEXT    NOT NULL,
                condition_id        TEXT    NOT NULL,
                event_id            TEXT    NOT NULL,
                side                TEXT    NOT NULL,
                target_price        TEXT    NOT NULL,
                max_price           TEXT    NOT NULL,
                size_usd_suggestion TEXT    NOT NULL,
                p                   TEXT    NOT NULL,
                p_confidence        TEXT    NOT NULL,
                resolution_summary  TEXT    NOT NULL,
                thesis              TEXT    NOT NULL,
                citations           TEXT    NOT NULL,
                created_at          INTEGER NOT NULL,
                decided_at          INTEGER,
                decision_verdict    TEXT,
                decision_stake_usd  TEXT,
                decision_price_exec TEXT,
                decision_reason     TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intent_audit (
                audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id  TEXT    NOT NULL,
                at         INTEGER NOT NULL,
                verdict    TEXT    NOT NULL,
                stake_usd  TEXT,
                price_exec TEXT,
                reason     TEXT    NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS op_audit (
                op_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                at     INTEGER NOT NULL,
                kind   TEXT    NOT NULL,
                reason TEXT    NOT NULL,
                detail TEXT    NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                fill_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                at              INTEGER NOT NULL,
                intent_id       TEXT    NOT NULL,
                token_id        TEXT    NOT NULL,
                condition_id    TEXT    NOT NULL,
                event_id        TEXT    NOT NULL,
                side            TEXT    NOT NULL,
                shares          TEXT    NOT NULL,
                price_exec      TEXT    NOT NULL,
                worst_case_risk TEXT    NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_journal (
                flow_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                at       INTEGER NOT NULL,
                wall_at  REAL    NOT NULL,
                kind     TEXT    NOT NULL,
                token_id TEXT    NOT NULL,
                amount   TEXT    NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                bl_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at           INTEGER NOT NULL,
                target_kind  TEXT    NOT NULL,
                target_value TEXT    NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_executions (
                execution_id      TEXT PRIMARY KEY,
                token_id          TEXT    NOT NULL,
                condition_id      TEXT    NOT NULL,
                event_id          TEXT    NOT NULL,
                category          TEXT    NOT NULL,
                outcome_slot      INTEGER NOT NULL,
                sibling_token_ids TEXT    NOT NULL,
                side              TEXT    NOT NULL,
                shares            TEXT    NOT NULL,
                price_exec        TEXT    NOT NULL,
                fill_mid          TEXT    NOT NULL,
                reward_accrued    TEXT    NOT NULL,
                created_at        INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_execution_outbox (
                sequence      INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id  TEXT NOT NULL REFERENCES shadow_executions(execution_id),
                role          TEXT NOT NULL CHECK (role IN ('MAKER', 'SHADOW')),
                state         TEXT NOT NULL CHECK (state IN ('PENDING', 'DELIVERED')),
                delivered_at  INTEGER,
                UNIQUE (execution_id, role)
            )
            """
        )
        self._conn.commit()
        self._validate_shadow_outbox_integrity()

    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()):
        """Hermes's ONLY write: INSERT a PROPOSED row. No ``status`` parameter (the
        chokepoint); idempotent on ``intent_id``. Returns True if newly inserted, False if
        a duplicate. Numeric fields are stored as exact strings (untrusted hints)."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO pending_intents "
            "(intent_id, status, token_id, condition_id, event_id, side, target_price, "
            "max_price, size_usd_suggestion, p, p_confidence, resolution_summary, thesis, "
            "citations, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (intent_id, _PROPOSED, token_id, condition_id, event_id, side, str(target_price),
             str(max_price), str(size_usd_suggestion), str(p), str(p_confidence),
             resolution_summary, thesis, json.dumps(list(citations)), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_decision(self, intent_id, decision, *, shadow_execution=None):
        """ERS-ONLY (never exposed to Hermes): transition the intent's status per the
        Decision verdict, store the decision, and append an immutable audit row. When a
        canonical filled shadow execution is supplied for ACCEPT, its two-role delivery
        outbox is committed in the same transaction."""
        status = _STATUS_FOR_VERDICT[decision.verdict]
        at = self._stamper.stamp()
        stake = None if decision.stake_usd is None else str(decision.stake_usd)
        price = None if decision.price_exec is None else str(decision.price_exec)
        if shadow_execution is not None:
            if not isinstance(shadow_execution, ShadowExecutionRecord):
                raise TypeError("shadow_execution must be a ShadowExecutionRecord")
            if decision.verdict != "ACCEPT":
                raise ValueError("only ACCEPT may persist a shadow execution")
            if shadow_execution.execution_id != intent_id:
                raise ValueError("shadow execution ID must equal intent ID")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            intent_row = self._conn.execute(
                "SELECT status, token_id, condition_id, event_id FROM pending_intents "
                "WHERE intent_id=?", (intent_id,),
            ).fetchone()
            if intent_row is None:
                raise KeyError(f"no intent {intent_id!r} to decide")
            if shadow_execution is not None and (
                    intent_row[1] != shadow_execution.token_id
                    or intent_row[2] != shadow_execution.condition_id
                    or intent_row[3] != shadow_execution.event_id):
                raise ValueError("shadow execution identity contradicts intent")
            self._conn.execute(
                "UPDATE pending_intents SET status=?, decided_at=?, decision_verdict=?, "
                "decision_stake_usd=?, decision_price_exec=?, decision_reason=? WHERE intent_id=?",
                (status, at, decision.verdict, stake, price, decision.reason, intent_id),
            )
            self._conn.execute(
                "INSERT INTO intent_audit (intent_id, at, verdict, stake_usd, price_exec, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (intent_id, at, decision.verdict, stake, price, decision.reason),
            )
            if shadow_execution is not None:
                execution = shadow_execution
                sibling_json = json.dumps(
                    list(execution.sibling_token_ids), ensure_ascii=False, separators=(",", ":")
                )
                self._conn.execute(
                    "INSERT INTO shadow_executions "
                    "(execution_id, token_id, condition_id, event_id, category, outcome_slot, "
                    "sibling_token_ids, side, shares, price_exec, fill_mid, reward_accrued, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (execution.execution_id, execution.token_id, execution.condition_id,
                     execution.event_id, execution.category, execution.outcome_slot, sibling_json,
                     execution.side, str(execution.shares), str(execution.price_exec),
                     str(execution.fill_mid), str(execution.reward_accrued), at),
                )
                self._conn.executemany(
                    "INSERT INTO shadow_execution_outbox (execution_id, role, state) "
                    "VALUES (?, ?, 'PENDING')",
                    ((execution.execution_id, role) for role in _SHADOW_ROLES),
                )
                self._before_shadow_execution_commit()
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def pending_shadow_executions(self, limit):
        """Return pending canonical paper executions in durable target order."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            "SELECT o.sequence, o.role, e.execution_id, e.token_id, e.condition_id, "
            "e.event_id, e.category, e.outcome_slot, e.sibling_token_ids, e.side, "
            "e.shares, e.price_exec, e.fill_mid, e.reward_accrued "
            "FROM shadow_execution_outbox AS o "
            "JOIN shadow_executions AS e USING (execution_id) "
            "WHERE o.state='PENDING' ORDER BY o.sequence LIMIT ?",
            (limit,),
        ).fetchall()
        records = []
        for row in rows:
            execution = self._shadow_execution_from_row(row[2:])
            records.append(ShadowExecutionOutboxRecord(row[0], row[1], execution))
        return tuple(records)

    def acknowledge_shadow_execution(self, sequence, execution_id, role):
        """Mark one exact target delivery complete; exact replay is idempotent."""
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("outbox sequence must be a positive integer")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution_id must be a non-empty string")
        if role not in _SHADOW_ROLES:
            raise ValueError("shadow execution outbox role is invalid")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT execution_id, role, state FROM shadow_execution_outbox "
                "WHERE sequence=?", (sequence,),
            ).fetchone()
            if row is None or row[0] != execution_id or row[1] != role:
                raise SettlementConflict("outbox acknowledgement identity does not match")
            if row[2] == "DELIVERED":
                self._conn.commit()
                return False
            self._conn.execute(
                "UPDATE shadow_execution_outbox SET state='DELIVERED', delivered_at=? "
                "WHERE sequence=?",
                (self._stamper.stamp(), sequence),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return True

    def _validate_shadow_outbox_integrity(self):
        orphan = self._conn.execute(
            "SELECT 1 FROM shadow_execution_outbox AS o "
            "LEFT JOIN shadow_executions AS e USING (execution_id) "
            "WHERE e.execution_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise SettlementConflict("orphaned shadow execution outbox")
        invalid_state = self._conn.execute(
            "SELECT 1 FROM shadow_execution_outbox "
            "WHERE state IS NULL OR state NOT IN ('PENDING', 'DELIVERED') LIMIT 1"
        ).fetchone()
        if invalid_state is not None:
            raise SettlementConflict("shadow execution outbox state is invalid")
        roles = self._conn.execute(
            "SELECT e.execution_id, COUNT(o.sequence), "
            "SUM(CASE WHEN o.role='MAKER' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN o.role='SHADOW' THEN 1 ELSE 0 END) "
            "FROM shadow_executions AS e "
            "LEFT JOIN shadow_execution_outbox AS o USING (execution_id) "
            "GROUP BY e.execution_id"
        ).fetchall()
        if any(count != 2 or maker != 1 or shadow != 1
               for _execution_id, count, maker, shadow in roles):
            raise SettlementConflict("shadow execution outbox target roles are incomplete")
        identity_drift = self._conn.execute(
            "SELECT 1 FROM shadow_executions AS e "
            "LEFT JOIN pending_intents AS i ON i.intent_id=e.execution_id "
            "WHERE i.intent_id IS NULL OR i.status IS NOT 'ACCEPTED' "
            "OR i.decision_verdict IS NOT 'ACCEPT' OR e.token_id IS NOT i.token_id "
            "OR e.condition_id IS NOT i.condition_id OR e.event_id IS NOT i.event_id LIMIT 1"
        ).fetchone()
        if identity_drift is not None:
            raise SettlementConflict("shadow execution identity contradicts intent")
        rows = self._conn.execute(
            "SELECT execution_id, token_id, condition_id, event_id, category, outcome_slot, "
            "sibling_token_ids, side, shares, price_exec, fill_mid, reward_accrued "
            "FROM shadow_executions ORDER BY execution_id"
        ).fetchall()
        for row in rows:
            self._shadow_execution_from_row(row)

    @staticmethod
    def _shadow_execution_from_row(row):
        try:
            siblings = json.loads(row[6])
            if (not isinstance(siblings, list)
                    or row[6] != json.dumps(
                        siblings, ensure_ascii=False, separators=(",", ":")
                    )):
                raise SettlementConflict("shadow execution has non-canonical sibling JSON")
            return ShadowExecutionRecord(
                execution_id=row[0], token_id=row[1], condition_id=row[2], event_id=row[3],
                category=row[4], outcome_slot=row[5], sibling_token_ids=tuple(siblings),
                side=row[7], shares=Decimal(row[8]), price_exec=Decimal(row[9]),
                fill_mid=Decimal(row[10]), reward_accrued=Decimal(row[11]),
            )
        except SettlementConflict:
            raise
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SettlementConflict("stored shadow execution is not canonical") from exc

    def _before_shadow_execution_commit(self):
        """Failure-injection seam immediately before ACCEPT/outbox commit."""

    def pending(self):
        # FIFO by rowid (insertion order) -- monotonic + restart-stable, unlike a per-process
        # created_at clock. Fairness, not safety: every intent is re-validated against live state.
        return self._query(
            f"SELECT {_COLUMNS} FROM pending_intents WHERE status=? ORDER BY rowid",
            (_PROPOSED,),
        )

    def accepted(self):
        # The ACCEPTED set, ORDER BY rowid -- mirrors pending(); RestartReconciler (S4.5d) reads it
        # to rebuild the in-memory Portfolio at boot. Re-uses _row_to_intent (the decision fields
        # round-trip so each OpenPosition can be reconstructed).
        return self._query(
            f"SELECT {_COLUMNS} FROM pending_intents WHERE status=? ORDER BY rowid",
            ("ACCEPTED",),
        )

    def get(self, intent_id):
        rows = self._query(f"SELECT {_COLUMNS} FROM pending_intents WHERE intent_id=?", (intent_id,))
        return rows[0] if rows else None

    def audit_log(self):
        rows = self._conn.execute(
            "SELECT intent_id, at, verdict, stake_usd, price_exec, reason "
            "FROM intent_audit ORDER BY audit_id"
        ).fetchall()
        return [{"intent_id": r[0], "at": r[1], "verdict": r[2],
                 "stake_usd": _dec(r[3]), "price_exec": _dec(r[4]), "reason": r[5]} for r in rows]

    def record_op_event(self, *, kind, reason, detail=""):
        """Append an IMMUTABLE op/kill/heartbeat audit row (S4.1). ``kind`` in
        {state_change, kill, pause, flatten, heartbeat, cancel_all, caps_swap, l8_command,
        l8_refused, l8_blacklist}; ``reason`` is a REASON_* code or a free-form string.
        Append-only + the shared monotonic stamp, mirroring intent_audit, so the
        restart-reconcile (S4.5) can replay the op timeline crash-consistently."""
        self._conn.execute(
            "INSERT INTO op_audit (at, kind, reason, detail) VALUES (?, ?, ?, ?)",
            (self._stamper.stamp(), kind, reason, detail),
        )
        self._conn.commit()

    def op_audit_log(self):
        rows = self._conn.execute(
            "SELECT at, kind, reason, detail FROM op_audit ORDER BY op_id"
        ).fetchall()
        return [{"at": r[0], "kind": r[1], "reason": r[2], "detail": r[3]} for r in rows]

    def record_fill(self, *, intent_id, token_id, condition_id, event_id, side, shares,
                    price_exec, worst_case_risk):
        """Append an IMMUTABLE fill row -- the durable INTERNAL leg the S4.5 reconcile + restart
        replays. Append-only + the shared monotonic stamp (mirrors record_op_event); every Decimal
        is stored as an exact string. ``side`` is "BUY" for a long entry."""
        self._conn.execute(
            "INSERT INTO fills (at, intent_id, token_id, condition_id, event_id, side, shares, "
            "price_exec, worst_case_risk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._stamper.stamp(), intent_id, token_id, condition_id, event_id, side,
             str(shares), str(price_exec), str(worst_case_risk)),
        )
        self._conn.commit()

    def fills_log(self):
        rows = self._conn.execute(
            "SELECT at, intent_id, token_id, condition_id, event_id, side, shares, price_exec, "
            "worst_case_risk FROM fills ORDER BY fill_id"
        ).fetchall()
        return [{"at": r[0], "intent_id": r[1], "token_id": r[2], "condition_id": r[3],
                 "event_id": r[4], "side": r[5], "shares": Decimal(r[6]),
                 "price_exec": Decimal(r[7]), "worst_case_risk": Decimal(r[8])} for r in rows]

    def record_flow_event(self, *, kind, token_id, amount, wall_at):
        """Append an IMMUTABLE flow-journal row (S4.7): ``kind`` in {accept, realized}.
        Dual-stamped: ``at`` = the shared monotonic stamp (cross-table ordering; NOT
        cross-restart comparable), ``wall_at`` = the caller-supplied wall clock in epoch
        seconds (windowing; the ONLY cross-restart-comparable time in the store). ``amount``
        is stored as an exact string: accept => worst_case_risk (+); realized => signed
        PnL (+win / -loss). Commit per write, mirroring record_op_event."""
        self._conn.execute(
            "INSERT INTO flow_journal (at, wall_at, kind, token_id, amount) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._stamper.stamp(), wall_at, kind, token_id, str(amount)),
        )
        self._conn.commit()

    def flow_log(self):
        rows = self._conn.execute(
            "SELECT at, wall_at, kind, token_id, amount FROM flow_journal ORDER BY flow_id"
        ).fetchall()
        return [{"at": r[0], "wall_at": r[1], "kind": r[2], "token_id": r[3],
                 "amount": Decimal(r[4])} for r in rows]

    def record_blacklist(self, *, target_kind, target_value):
        """Append an IMMUTABLE blacklist row (S4.6d). The store is DUMB: it records ANY
        target_kind string -- the TelegramController.__apply validates the kind in
        {wallet, market, source} and raises BEFORE calling this. Append-only + the shared
        monotonic stamp; commit per write (mirrors record_op_event / record_fill)."""
        self._conn.execute(
            "INSERT INTO blacklist (at, target_kind, target_value) VALUES (?, ?, ?)",
            (self._stamper.stamp(), target_kind, target_value),
        )
        self._conn.commit()

    def blacklist_log(self):
        rows = self._conn.execute(
            "SELECT at, target_kind, target_value FROM blacklist ORDER BY bl_id"
        ).fetchall()
        return [{"at": r[0], "target_kind": r[1], "target_value": r[2]} for r in rows]

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _query(self, sql, params=()):
        return [self._row_to_intent(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row_to_intent(r):
        return PendingIntent(
            intent_id=r[0], status=r[1], token_id=r[2], condition_id=r[3], event_id=r[4],
            side=r[5], target_price=Decimal(r[6]), max_price=Decimal(r[7]),
            size_usd_suggestion=Decimal(r[8]), p=Decimal(r[9]), p_confidence=Decimal(r[10]),
            resolution_summary=r[11], thesis=r[12], citations=tuple(json.loads(r[13])),
            created_at=r[14], decided_at=r[15], decision_verdict=r[16],
            decision_stake_usd=_dec(r[17]), decision_price_exec=_dec(r[18]), decision_reason=r[19],
        )
