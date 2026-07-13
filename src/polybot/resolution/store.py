"""Durable central authority store for POL-15 resolution state."""

import json
import re
import sqlite3
from dataclasses import dataclass

from polybot.resolution.errors import SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ResolutionSubject,
)


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")


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
        if (isinstance(self.block_number, bool) or not isinstance(self.block_number, int)
                or self.block_number < 0):
            raise ValueError("assessment block_number must be a non-negative integer")
        if not isinstance(self.block_hash, str) or _BYTES32.fullmatch(self.block_hash) is None:
            raise ValueError("assessment block_hash must be a canonical lowercase bytes32")
        if not isinstance(self.detail, str):
            raise TypeError("assessment detail must be a string")
        if self.phase is LifecyclePhase.UNRESOLVED:
            if self.dispute is not DisputeState.UNKNOWN or self.payout is not None:
                raise ValueError("unresolved assessments cannot carry terminal evidence")
        elif not isinstance(self.payout, PayoutVector):
            raise ValueError("finalized assessments require a payout")


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
        self._conn.commit()

    def record_assessment(self, assessment):
        if not isinstance(assessment, ResolutionAssessment):
            raise TypeError("assessment must be a ResolutionAssessment")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._ensure_subject(assessment.subject)
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
                    (int(payout_values[0]), int(payout_values[1])), int(payout_values[2])
                )
            return ResolutionAssessment(
                subject, phase, dispute, payout, row[8], row[9], row[10]
            )
        except (TypeError, ValueError) as exc:
            raise SettlementConflict("stored assessment is not canonical") from exc

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

