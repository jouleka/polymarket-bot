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
    TerminalResolution,
)
from polybot.resolution.store import ResolutionAssessment, ResolutionStore


def _subject(condition_byte, *, event_id="event-1"):
    return ResolutionSubject(
        event_id, "0x" + condition_byte * 32, ("101", "202"), "politics"
    )


def _terminal(condition_byte):
    return TerminalResolution(
        subject=_subject(condition_byte), payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR, block_number=200,
        block_hash="0x" + "33" * 32, adapter_address="0x" + "44" * 20,
        question_id="0x" + "55" * 32,
        audit_event_ids=(
            "199:1:" + "0x" + "66" * 32 + ":CONDITION_RESOLUTION",
        ),
        provider_ids=("archive-a", "archive-b"),
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


def test_terminal_atomically_creates_three_ordered_outbox_rows(tmp_path):
    path = str(tmp_path / "resolution.db")
    terminal = _terminal("71")
    failing_terminal = _terminal("72")
    prior = ResolutionAssessment(
        terminal.subject, LifecyclePhase.FINALIZED, DisputeState.UNKNOWN,
        terminal.payout, terminal.block_number, terminal.block_hash, "awaiting classification",
    )
    failing_prior = replace(prior, subject=failing_terminal.subject)

    with ResolutionStore(path, MonotonicStamper()) as store:
        store.record_assessment(prior)
        assert store.accept_terminal(terminal) is True
        assert store.assessment_for(terminal.subject.condition_id) is None
        assert store.terminal_for(terminal.subject.condition_id) == terminal
        assert store._conn.execute(
            "SELECT role, state FROM resolution_outbox ORDER BY sequence"
        ).fetchall() == [
            ("FORECAST", "PENDING"), ("MAKER", "PENDING"), ("SHADOW", "PENDING")
        ]
        with pytest.raises(SettlementConflict, match="terminal"):
            store.record_assessment(prior)

        store.record_assessment(failing_prior)

        def fail_before_commit():
            raise RuntimeError("injected pre-commit failure")

        store._before_terminal_commit = fail_before_commit
        with pytest.raises(RuntimeError, match="injected"):
            store.accept_terminal(failing_terminal)
        assert store.assessment_for(failing_terminal.subject.condition_id) == failing_prior
        assert store.terminal_for(failing_terminal.subject.condition_id) is None
        assert store._conn.execute(
            "SELECT COUNT(*) FROM resolution_outbox"
        ).fetchone()[0] == 3
