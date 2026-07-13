"""Durable central authority store for POL-15 resolution state."""

import json
import re
import sqlite3
from dataclasses import dataclass

from polybot.resolution.errors import IntegrityHalted, RecoveryRequired, SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TERMINAL_ID = re.compile(r"[0-9a-f]{64}\Z")
_UINT_TEXT = re.compile(r"0|[1-9][0-9]*\Z")
_ROLES = ("FORECAST", "MAKER", "SHADOW")


@dataclass(frozen=True)
class ResolutionAssessment:
    subject: ResolutionSubject
    phase: LifecyclePhase
    dispute: DisputeState
    payout: PayoutVector | None
    block_number: int
    block_hash: str
    detail: str

    def __post_init__(self):
        if not isinstance(self.subject, ResolutionSubject):
            raise TypeError("assessment subject must be a ResolutionSubject")
        if not isinstance(self.phase, LifecyclePhase):
            raise TypeError("assessment phase must be a LifecyclePhase")
        if not isinstance(self.dispute, DisputeState):
            raise TypeError("assessment dispute must be a DisputeState")
        if self.dispute is not DisputeState.UNKNOWN:
            raise ValueError("non-terminal assessments must have UNKNOWN dispute state")
        if (isinstance(self.block_number, bool) or not isinstance(self.block_number, int)
                or self.block_number < 0):
            raise ValueError("assessment block_number must be a non-negative integer")
        if not isinstance(self.block_hash, str) or _BYTES32.fullmatch(self.block_hash) is None:
            raise ValueError("assessment block_hash must be a canonical lowercase bytes32")
        if not isinstance(self.detail, str):
            raise TypeError("assessment detail must be a string")
        if self.phase is LifecyclePhase.UNRESOLVED:
            if self.payout is not None:
                raise ValueError("unresolved assessments cannot carry terminal evidence")
        elif not isinstance(self.payout, PayoutVector):
            raise ValueError("finalized assessments require a payout")


@dataclass(frozen=True)
class OutboxRecord:
    sequence: int
    terminal: TerminalResolution
    role: str

    def __post_init__(self):
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence <= 0:
            raise ValueError("outbox sequence must be a positive integer")
        if not isinstance(self.terminal, TerminalResolution):
            raise TypeError("outbox terminal must be a TerminalResolution")
        if self.role not in _ROLES:
            raise ValueError("outbox role is invalid")


class ResolutionStore:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_subjects (
                condition_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                token_ids TEXT NOT NULL,
                category TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_assessments (
                condition_id TEXT PRIMARY KEY
                    REFERENCES resolution_subjects(condition_id),
                phase TEXT NOT NULL,
                dispute TEXT NOT NULL,
                payout_numerator_0 TEXT,
                payout_numerator_1 TEXT,
                payout_denominator TEXT,
                block_number INTEGER NOT NULL,
                block_hash TEXT NOT NULL,
                detail TEXT NOT NULL,
                observed_at INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_terminals (
                condition_id TEXT PRIMARY KEY
                    REFERENCES resolution_subjects(condition_id),
                terminal_id TEXT UNIQUE NOT NULL,
                payload BLOB NOT NULL,
                accepted_at INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id TEXT NOT NULL
                    REFERENCES resolution_terminals(terminal_id),
                role TEXT NOT NULL CHECK (role IN ('FORECAST', 'MAKER', 'SHADOW')),
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'DELIVERED')),
                delivered_at INTEGER,
                UNIQUE (terminal_id, role)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_integrity_halt (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                reason TEXT NOT NULL,
                halted_at INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()
        self._validate_outbox_integrity()
        self._recovery_required = self._conn.execute(
            "SELECT 1 FROM resolution_outbox WHERE state='PENDING' LIMIT 1"
        ).fetchone() is not None

    @property
    def recovery_required(self):
        return self._recovery_required

    def record_assessment(self, assessment):
        if not isinstance(assessment, ResolutionAssessment):
            raise TypeError("assessment must be a ResolutionAssessment")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_healthy_in_transaction()
            self._ensure_subject(assessment.subject)
            terminal = self._conn.execute(
                "SELECT 1 FROM resolution_terminals WHERE condition_id=?",
                (assessment.subject.condition_id,),
            ).fetchone()
            if terminal is not None:
                raise SettlementConflict("condition already has an immutable terminal")
            payout = assessment.payout
            self._conn.execute(
                """
                INSERT INTO resolution_assessments (
                    condition_id, phase, dispute, payout_numerator_0,
                    payout_numerator_1, payout_denominator, block_number, block_hash,
                    detail, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(condition_id) DO UPDATE SET
                    phase=excluded.phase,
                    dispute=excluded.dispute,
                    payout_numerator_0=excluded.payout_numerator_0,
                    payout_numerator_1=excluded.payout_numerator_1,
                    payout_denominator=excluded.payout_denominator,
                    block_number=excluded.block_number,
                    block_hash=excluded.block_hash,
                    detail=excluded.detail,
                    observed_at=excluded.observed_at
                """,
                (
                    assessment.subject.condition_id,
                    assessment.phase.value,
                    assessment.dispute.value,
                    None if payout is None else str(payout.numerators[0]),
                    None if payout is None else str(payout.numerators[1]),
                    None if payout is None else str(payout.denominator),
                    assessment.block_number,
                    assessment.block_hash,
                    assessment.detail,
                    self._stamper.stamp(),
                ),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def assessment_for(self, condition_id):
        _validate_condition_id(condition_id)
        row = self._conn.execute(
            """
            SELECT s.event_id, s.token_ids, s.category, a.phase, a.dispute,
                   a.payout_numerator_0, a.payout_numerator_1, a.payout_denominator,
                   a.block_number, a.block_hash, a.detail
            FROM resolution_assessments AS a
            JOIN resolution_subjects AS s USING (condition_id)
            WHERE a.condition_id=?
            """,
            (condition_id,),
        ).fetchone()
        if row is None:
            return None
        if self._conn.execute(
                "SELECT 1 FROM resolution_terminals WHERE condition_id=?", (condition_id,)
                ).fetchone() is not None:
            raise SettlementConflict("assessment coexists with immutable terminal authority")
        try:
            tokens = json.loads(row[1])
            canonical_tokens = json.dumps(tokens, ensure_ascii=False, separators=(",", ":"))
            if (not isinstance(tokens, list) or len(tokens) != 2
                    or row[1] != canonical_tokens):
                raise ValueError("noncanonical subject token encoding")
            subject = ResolutionSubject(row[0], condition_id, tuple(tokens), row[2])
            phase = LifecyclePhase(row[3])
            dispute = DisputeState(row[4])
            payout_values = row[5:8]
            if all(value is None for value in payout_values):
                payout = None
            elif any(value is None for value in payout_values):
                raise ValueError("mixed assessment payout")
            else:
                payout = PayoutVector(
                    (_decode_uint_text(payout_values[0]), _decode_uint_text(payout_values[1])),
                    _decode_uint_text(payout_values[2]),
                )
            return ResolutionAssessment(
                subject, phase, dispute, payout, row[8], row[9], row[10]
            )
        except (TypeError, ValueError) as exc:
            raise SettlementConflict("stored assessment is not canonical") from exc

    def accept_terminal(self, terminal):
        if not isinstance(terminal, TerminalResolution):
            raise TypeError("terminal must be a TerminalResolution")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_healthy_in_transaction()
            self._ensure_subject(terminal.subject)
            existing = self._conn.execute(
                "SELECT terminal_id, payload FROM resolution_terminals WHERE condition_id=?",
                (terminal.subject.condition_id,),
            ).fetchone()
            if existing is not None:
                if (existing[0] != terminal.terminal_id
                        or bytes(existing[1]) != terminal.canonical_bytes):
                    raise SettlementConflict("stored terminal contradicts new terminal bytes")
                self._validate_outbox_integrity(terminal.terminal_id)
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO resolution_terminals "
                "(condition_id, terminal_id, payload, accepted_at) VALUES (?, ?, ?, ?)",
                (
                    terminal.subject.condition_id, terminal.terminal_id,
                    terminal.canonical_bytes, self._stamper.stamp(),
                ),
            )
            self._conn.execute(
                "DELETE FROM resolution_assessments WHERE condition_id=?",
                (terminal.subject.condition_id,),
            )
            for role in _ROLES:
                self._conn.execute(
                    "INSERT INTO resolution_outbox (terminal_id, role, state) "
                    "VALUES (?, ?, 'PENDING')",
                    (terminal.terminal_id, role),
                )
            self._before_terminal_commit()
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return True

    def terminal_for(self, condition_id):
        _validate_condition_id(condition_id)
        row = self._conn.execute(
            "SELECT terminal_id, payload FROM resolution_terminals WHERE condition_id=?",
            (condition_id,),
        ).fetchone()
        if row is None:
            return None
        if self._conn.execute(
                "SELECT 1 FROM resolution_assessments WHERE condition_id=?", (condition_id,)
                ).fetchone() is not None:
            raise SettlementConflict("terminal authority coexists with an assessment")
        terminal = _decode_terminal(row[0], row[1])
        if terminal.subject.condition_id != condition_id:
            raise SettlementConflict("terminal payload condition contradicts its authority row")
        return terminal

    def pending_terminals(self):
        self._validate_outbox_integrity()
        rows = self._pending_terminal_rows()
        return tuple(_decode_terminal(terminal_id, payload) for terminal_id, payload in rows)

    def _pending_terminal_rows(self):
        return self._conn.execute(
            """
            SELECT t.terminal_id, t.payload
            FROM resolution_terminals AS t
            JOIN resolution_outbox AS o USING (terminal_id)
            WHERE o.state='PENDING'
            GROUP BY t.terminal_id, t.payload
            ORDER BY MIN(o.sequence)
            """
        ).fetchall()

    def pending_outbox(self, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("outbox limit must be a positive integer")
        self._validate_outbox_integrity()
        rows = self._conn.execute(
            """
            SELECT o.sequence, o.role, t.terminal_id, t.payload
            FROM resolution_outbox AS o
            JOIN resolution_terminals AS t USING (terminal_id)
            WHERE o.state='PENDING'
            ORDER BY o.sequence
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(
            OutboxRecord(sequence, _decode_terminal(terminal_id, payload), role)
            for sequence, role, terminal_id, payload in rows
        )

    def acknowledge(self, sequence, terminal_id, role):
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("outbox sequence must be a positive integer")
        if not isinstance(terminal_id, str) or _TERMINAL_ID.fullmatch(terminal_id) is None:
            raise ValueError("terminal_id must be a lowercase SHA-256 hex string")
        if role not in _ROLES:
            raise ValueError("outbox role is invalid")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_healthy_in_transaction()
            if self._recovery_required:
                raise RecoveryRequired("resolution outbox requires restart recovery")
            self._validate_outbox_integrity()
            row = self._conn.execute(
                "SELECT terminal_id, role, state FROM resolution_outbox WHERE sequence=?",
                (sequence,),
            ).fetchone()
            if row is None or row[0] != terminal_id or row[1] != role:
                raise SettlementConflict("outbox acknowledgement identity does not match")
            if row[2] == "DELIVERED":
                self._conn.commit()
                return False
            self._conn.execute(
                "UPDATE resolution_outbox SET state='DELIVERED', delivered_at=? "
                "WHERE sequence=?",
                (self._stamper.stamp(), sequence),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        return True

    def halt(self, reason):
        if (not isinstance(reason, str) or not reason or reason != reason.strip()):
            raise ValueError("integrity halt reason must be a non-empty exact string")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT OR IGNORE INTO resolution_integrity_halt "
                "(singleton, reason, halted_at) VALUES (1, ?, ?)",
                (reason, self._stamper.stamp()),
            )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def require_healthy(self):
        self._require_healthy_in_transaction()

    def _require_healthy_in_transaction(self):
        row = self._conn.execute(
            "SELECT reason FROM resolution_integrity_halt WHERE singleton=1"
        ).fetchone()
        if row is not None:
            raise IntegrityHalted(f"resolution integrity halted: {row[0]}")

    def _complete_recovery(self, terminal_ids):
        if not isinstance(terminal_ids, tuple):
            raise TypeError("recovered terminal IDs must be a tuple")
        if any(not isinstance(value, str) or _TERMINAL_ID.fullmatch(value) is None
               for value in terminal_ids):
            raise ValueError("recovered terminal IDs must be lowercase SHA-256 hex strings")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_healthy_in_transaction()
            current = tuple(row[0] for row in self._pending_terminal_rows())
            if terminal_ids != current:
                raise RecoveryRequired(
                    "recovery requires the exact current pending terminal-ID tuple"
                )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        self._recovery_required = False

    def _before_terminal_commit(self):
        """Failure-injection seam for the terminal/outbox transaction."""

    def _validate_outbox_integrity(self, terminal_id=None):
        parameters = () if terminal_id is None else (terminal_id,)
        where = "" if terminal_id is None else " WHERE terminal_id=?"
        terminals = self._conn.execute(
            "SELECT terminal_id FROM resolution_terminals" + where, parameters
        ).fetchall()
        for (stored_terminal_id,) in terminals:
            rows = self._conn.execute(
                "SELECT role, state, delivered_at FROM resolution_outbox "
                "WHERE terminal_id=? ORDER BY sequence",
                (stored_terminal_id,),
            ).fetchall()
            if tuple(row[0] for row in rows) != _ROLES:
                raise SettlementConflict("terminal outbox roles are missing or out of order")
            for _, state, delivered_at in rows:
                if ((state == "PENDING" and delivered_at is not None)
                        or (state == "DELIVERED" and delivered_at is None)
                        or state not in ("PENDING", "DELIVERED")):
                    raise SettlementConflict("terminal outbox state is not canonical")
        orphan = self._conn.execute(
            "SELECT 1 FROM resolution_outbox AS o LEFT JOIN resolution_terminals AS t "
            "USING (terminal_id) WHERE t.terminal_id IS NULL LIMIT 1"
        ).fetchone()
        if orphan is not None:
            raise SettlementConflict("outbox row has no terminal authority")

    def _ensure_subject(self, subject):
        token_json = json.dumps(
            list(subject.token_ids), ensure_ascii=False, separators=(",", ":")
        )
        row = self._conn.execute(
            "SELECT event_id, token_ids, category FROM resolution_subjects "
            "WHERE condition_id=?",
            (subject.condition_id,),
        ).fetchone()
        expected = (subject.event_id, token_json, subject.category)
        if row is None:
            self._conn.execute(
                "INSERT INTO resolution_subjects "
                "(condition_id, event_id, token_ids, category) VALUES (?, ?, ?, ?)",
                (subject.condition_id, *expected),
            )
        elif row != expected:
            raise SettlementConflict("condition is already bound to a different subject")

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _decode_terminal(terminal_id, payload_bytes):
    try:
        raw = bytes(payload_bytes)
        payload = json.loads(raw.decode("utf-8"))
        subject_payload = payload["subject"]
        payout_payload = payload["payout"]
        authority = payload["authority"]
        acceptance = payload["acceptance"]
        terminal = TerminalResolution(
            subject=ResolutionSubject(
                subject_payload["event_id"], subject_payload["condition_id"],
                tuple(subject_payload["token_ids"]), subject_payload["category"],
            ),
            payout=PayoutVector(
                tuple(payout_payload["numerators"]), payout_payload["denominator"]
            ),
            dispute=DisputeState(payload["path"]),
            block_number=acceptance["block_number"],
            block_hash=acceptance["block_hash"],
            adapter_address=authority["adapter_address"],
            question_id=authority["question_id"],
            audit_event_ids=tuple(authority["audit_event_ids"]),
            provider_ids=tuple(payload["providers"]),
        )
        if terminal.terminal_id != terminal_id or terminal.canonical_bytes != raw:
            raise ValueError("terminal identity is not canonical")
        return terminal
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise SettlementConflict("stored terminal is not canonical") from exc


def _validate_condition_id(condition_id):
    if not isinstance(condition_id, str) or _BYTES32.fullmatch(condition_id) is None:
        raise ValueError("condition_id must be a canonical lowercase bytes32")


def _decode_uint_text(value):
    if not isinstance(value, str) or _UINT_TEXT.fullmatch(value) is None:
        raise ValueError("stored integer is not canonical unsigned decimal text")
    return int(value)
