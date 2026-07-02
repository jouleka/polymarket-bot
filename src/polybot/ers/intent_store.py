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
from decimal import Decimal

_PROPOSED = "PROPOSED"
_STATUS_FOR_VERDICT = {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "SKIP": "SKIPPED"}

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


def _dec(value):
    return None if value is None else Decimal(value)


class IntentStore:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
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
        self._conn.commit()

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

    def record_decision(self, intent_id, decision):
        """ERS-ONLY (never exposed to Hermes): transition the intent's status per the
        Decision verdict, store the decision, and append an immutable audit row."""
        status = _STATUS_FOR_VERDICT[decision.verdict]
        at = self._stamper.stamp()
        stake = None if decision.stake_usd is None else str(decision.stake_usd)
        price = None if decision.price_exec is None else str(decision.price_exec)
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
        self._conn.commit()

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
        {state_change, kill, pause, flatten, heartbeat, cancel_all, caps_swap}; ``reason`` is a REASON_* code
        or a free-form string. Append-only + the shared monotonic stamp, mirroring intent_audit,
        so the restart-reconcile (S4.5) can replay the op timeline crash-consistently."""
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
