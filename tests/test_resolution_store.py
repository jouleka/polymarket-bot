"""POL-15 durable resolution authority store."""

from dataclasses import replace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.errors import SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ResolutionSubject,
)
from polybot.resolution.store import ResolutionAssessment, ResolutionStore


def _subject(condition_byte, *, event_id="event-1"):
    return ResolutionSubject(
        event_id, "0x" + condition_byte * 32, ("101", "202"), "politics"
    )


def test_assessment_round_trips_and_replaces_only_same_subject(tmp_path):
    path = str(tmp_path / "resolution.db")
    first = ResolutionAssessment(
        subject=_subject("61"), phase=LifecyclePhase.UNRESOLVED,
        dispute=DisputeState.UNKNOWN, payout=None, block_number=100,
        block_hash="0x" + "11" * 32, detail="condition has no payout yet",
    )
    replacement = ResolutionAssessment(
        subject=first.subject, phase=LifecyclePhase.FINALIZED,
        dispute=DisputeState.UNKNOWN, payout=PayoutVector((1, 1), 2), block_number=101,
        block_hash="0x" + "22" * 32, detail="unsupported terminal path",
    )
    independent = replace(first, subject=_subject("62"), detail="other condition")
    conflicting = replace(replacement, subject=_subject("61", event_id="other-event"))

    with ResolutionStore(path, MonotonicStamper()) as store:
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        store.record_assessment(first)
        store.record_assessment(independent)
        store.record_assessment(replacement)
        assert store.assessment_for(first.subject.condition_id) == replacement
        assert store.assessment_for(independent.subject.condition_id) == independent
        with pytest.raises(SettlementConflict, match="subject"):
            store.record_assessment(conflicting)

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        assert reopened.assessment_for(first.subject.condition_id) == replacement
        assert reopened.assessment_for(independent.subject.condition_id) == independent
        with pytest.raises(SettlementConflict, match="subject"):
            reopened.record_assessment(conflicting)

