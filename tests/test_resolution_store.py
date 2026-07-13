"""POL-15 durable resolution authority store."""

from dataclasses import replace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.errors import IntegrityHalted, RecoveryRequired, SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import OutboxRecord, ResolutionAssessment, ResolutionStore


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


@pytest.mark.parametrize(
    "classified", [DisputeState.CLEAR, DisputeState.DISPUTED, DisputeState.MANUAL]
)
def test_assessment_rejects_classified_terminal_paths(classified):
    with pytest.raises(ValueError, match="UNKNOWN"):
        ResolutionAssessment(
            _subject("63"), LifecyclePhase.FINALIZED, classified,
            PayoutVector((1, 0), 1), 100, "0x" + "11" * 32,
            "classified paths must become immutable terminals",
        )


@pytest.mark.parametrize("lookup_name", ["assessment_for", "terminal_for"])
def test_store_lookups_require_canonical_condition_identity(tmp_path, lookup_name):
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        lookup = getattr(store, lookup_name)
        assert lookup("0x" + "99" * 32) is None
        for invalid in (None, "bad", "0x" + "AA" * 32):
            with pytest.raises(ValueError, match="condition_id"):
                lookup(invalid)


@pytest.mark.parametrize(("column", "corrupt"), [
    ("payout_numerator_0", "+1"), ("payout_denominator", "02"),
])
def test_assessment_rejects_noncanonical_stored_payout_integers(
        tmp_path, column, corrupt):
    assessment = ResolutionAssessment(
        _subject("64"), LifecyclePhase.FINALIZED, DisputeState.UNKNOWN,
        PayoutVector((1, 1), 2), 100, "0x" + "11" * 32, "unknown path",
    )
    with ResolutionStore(str(tmp_path / f"{column}.db"), MonotonicStamper()) as store:
        store.record_assessment(assessment)
        store._conn.execute(
            f"UPDATE resolution_assessments SET {column}=?", (corrupt,)
        )
        store._conn.commit()
        with pytest.raises(SettlementConflict, match="canonical"):
            store.assessment_for(assessment.subject.condition_id)


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


def test_store_preserves_first_terminal_bytes(tmp_path):
    terminal = _terminal("73")
    changed = replace(
        terminal, block_number=201, block_hash="0x" + "77" * 32
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        assert store.accept_terminal(terminal) is True
        assert store.accept_terminal(terminal) is False
        with pytest.raises(SettlementConflict, match="terminal"):
            store.accept_terminal(changed)
        assert store.terminal_for(terminal.subject.condition_id) == terminal
        assert store._conn.execute(
            "SELECT terminal_id, payload FROM resolution_terminals"
        ).fetchall() == [(terminal.terminal_id, terminal.canonical_bytes)]
        assert store._conn.execute(
            "SELECT COUNT(*) FROM resolution_outbox"
        ).fetchone()[0] == 3


@pytest.mark.parametrize("corruption", ["missing_role", "invalid_state"])
def test_store_rejects_structurally_corrupt_outbox(tmp_path, corruption):
    path = str(tmp_path / f"{corruption}.db")
    terminal = _terminal("7a")
    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        if corruption == "missing_role":
            store._conn.execute(
                "DELETE FROM resolution_outbox WHERE role='SHADOW'"
            )
        else:
            store._conn.execute("PRAGMA ignore_check_constraints=ON")
            store._conn.execute(
                "UPDATE resolution_outbox SET state='BROKEN' WHERE role='SHADOW'"
            )
            store._conn.execute("PRAGMA ignore_check_constraints=OFF")
        store._conn.commit()
        with pytest.raises(SettlementConflict, match="outbox"):
            store.accept_terminal(terminal)
        with pytest.raises(SettlementConflict, match="outbox"):
            store.pending_outbox(10)

    with pytest.raises(SettlementConflict, match="outbox"):
        ResolutionStore(path, MonotonicStamper())


@pytest.mark.parametrize("corruption", ["assessment_coexists", "terminal_rebound"])
def test_store_reads_reject_redundant_authority_corruption(tmp_path, corruption):
    terminal = _terminal("7b")
    with ResolutionStore(str(tmp_path / f"{corruption}.db"), MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        if corruption == "assessment_coexists":
            store._conn.execute(
                "INSERT INTO resolution_assessments "
                "(condition_id, phase, dispute, payout_numerator_0, payout_numerator_1, "
                "payout_denominator, block_number, block_hash, detail, observed_at) "
                "VALUES (?, 'FINALIZED', 'UNKNOWN', '3', '1', '4', ?, ?, 'corrupt', ?)",
                (terminal.subject.condition_id, terminal.block_number,
                 terminal.block_hash, 999),
            )
            lookup_condition = terminal.subject.condition_id
        else:
            rebound = _subject("7c")
            store._conn.execute(
                "INSERT INTO resolution_subjects "
                "(condition_id, event_id, token_ids, category) VALUES (?, ?, ?, ?)",
                (rebound.condition_id, rebound.event_id, '["101","202"]', rebound.category),
            )
            store._conn.execute(
                "UPDATE resolution_terminals SET condition_id=?",
                (rebound.condition_id,),
            )
            lookup_condition = rebound.condition_id
        store._conn.commit()

        with pytest.raises(SettlementConflict, match="authority|condition|assessment"):
            store.terminal_for(lookup_condition)
        if corruption == "assessment_coexists":
            with pytest.raises(SettlementConflict, match="authority|terminal"):
                store.assessment_for(lookup_condition)


def test_outbox_order_and_matching_acknowledgement_are_exact(tmp_path):
    first = _terminal("74")
    second = _terminal("75")
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        store.accept_terminal(first)
        store.accept_terminal(second)
        pending = store.pending_outbox(4)
        assert all(isinstance(record, OutboxRecord) for record in pending)
        assert [(record.sequence, record.terminal.terminal_id, record.role)
                for record in pending] == [
            (1, first.terminal_id, "FORECAST"),
            (2, first.terminal_id, "MAKER"),
            (3, first.terminal_id, "SHADOW"),
            (4, second.terminal_id, "FORECAST"),
        ]
        with pytest.raises(ValueError, match="limit"):
            store.pending_outbox(0)
        with pytest.raises(SettlementConflict, match="outbox"):
            store.acknowledge(1, second.terminal_id, "FORECAST")
        with pytest.raises(SettlementConflict, match="outbox"):
            store.acknowledge(1, first.terminal_id, "MAKER")
        assert store.acknowledge(1, first.terminal_id, "FORECAST") is True
        assert store.acknowledge(1, first.terminal_id, "FORECAST") is False
        assert [(record.sequence, record.role) for record in store.pending_outbox(10)] == [
            (2, "MAKER"), (3, "SHADOW"), (4, "FORECAST"),
            (5, "MAKER"), (6, "SHADOW"),
        ]


def test_outbox_record_validates_its_public_identity():
    record = OutboxRecord(1, _terminal("7d"), "FORECAST")
    for change in (
        {"sequence": 0}, {"sequence": True}, {"terminal": object()}, {"role": "INVALID"},
    ):
        with pytest.raises((TypeError, ValueError)):
            replace(record, **change)


def test_integrity_halt_persists_and_blocks_mutators(tmp_path):
    path = str(tmp_path / "resolution.db")
    terminal = _terminal("76")
    assessment = ResolutionAssessment(
        _subject("77"), LifecyclePhase.UNRESOLVED, DisputeState.UNKNOWN, None,
        100, "0x" + "11" * 32, "still unresolved",
    )
    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        store.halt("first immutable contradiction")
        store.halt("later symptom must not overwrite root cause")
        for operation in (
            store.require_healthy,
            lambda: store.record_assessment(assessment),
            lambda: store.accept_terminal(_terminal("77")),
            lambda: store.acknowledge(1, terminal.terminal_id, "FORECAST"),
            lambda: store._complete_recovery((terminal.terminal_id,)),
        ):
            with pytest.raises(IntegrityHalted, match="first immutable contradiction"):
                operation()

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        with pytest.raises(IntegrityHalted, match="first immutable contradiction"):
            reopened.require_healthy()
        assert reopened.pending_outbox(10)[0].sequence == 1


def test_reopened_pending_outbox_requires_complete_recovery(tmp_path):
    path = str(tmp_path / "resolution.db")
    first = _terminal("78")
    second = _terminal("79")
    with ResolutionStore(path, MonotonicStamper()) as store:
        assert store.recovery_required is False
        store.accept_terminal(first)
        store.accept_terminal(second)
        assert store.recovery_required is False
        assert store.acknowledge(1, first.terminal_id, "FORECAST") is True

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        assert reopened.recovery_required is True
        assert reopened.pending_terminals() == (first, second)
        with pytest.raises(RecoveryRequired, match="recovery"):
            reopened.acknowledge(2, first.terminal_id, "MAKER")
        with pytest.raises(RecoveryRequired, match="exact"):
            reopened._complete_recovery((second.terminal_id, first.terminal_id))
        assert reopened.recovery_required is True
        reopened._complete_recovery((first.terminal_id, second.terminal_id))
        assert reopened.recovery_required is False
        assert reopened.acknowledge(2, first.terminal_id, "MAKER") is True
