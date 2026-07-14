"""POL-16 atomic ACCEPT → shadow-execution outbox persistence."""

from decimal import Decimal
import sqlite3

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore, ShadowExecutionRecord
from polybot.ers.validator import Decision
from polybot.resolution.errors import SettlementConflict


_PROPOSAL = dict(
    token_id="101",
    condition_id="0x" + "11" * 32,
    event_id="event-1",
    side="BUY",
    target_price="0.49",
    max_price="0.60",
    size_usd_suggestion="12",
    p="0.80",
    p_confidence="0.75",
    resolution_summary="",
    thesis="",
    citations=(),
)


def _execution(**overrides):
    values = dict(
        execution_id="intent-1",
        token_id="101",
        condition_id="0x" + "11" * 32,
        event_id="event-1",
        category="politics",
        outcome_slot=0,
        sibling_token_ids=("101", "202"),
        side="BUY",
        shares=Decimal("24"),
        price_exec=Decimal("0.50"),
        fill_mid=Decimal("0.51"),
        reward_accrued=Decimal("1.25"),
    )
    values.update(overrides)
    return ShadowExecutionRecord(**values)


def test_accept_decision_atomically_enqueues_exact_execution_for_both_targets(tmp_path):
    path = str(tmp_path / "intent.db")
    with IntentStore(path, MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        decision = Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap")

        store.record_decision("intent-1", decision, shadow_execution=_execution())

        assert store.get("intent-1").status == "ACCEPTED"
        assert store.audit_log()[-1]["verdict"] == "ACCEPT"
        pending = store.pending_shadow_executions(10)
        assert [(record.sequence, record.role) for record in pending] == [
            (1, "MAKER"),
            (2, "SHADOW"),
        ]
        assert pending[0].execution == _execution()
        assert pending[1].execution == _execution()

    with IntentStore(path, MonotonicStamper()) as reopened:
        assert reopened.pending_shadow_executions(10) == pending


def test_acknowledgement_requires_exact_sequence_execution_and_role(tmp_path):
    with IntentStore(str(tmp_path / "intent.db"), MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=_execution(),
        )

        with pytest.raises(SettlementConflict, match="outbox acknowledgement"):
            store.acknowledge_shadow_execution(1, "wrong", "MAKER")
        with pytest.raises(SettlementConflict, match="outbox acknowledgement"):
            store.acknowledge_shadow_execution(1, "intent-1", "SHADOW")
        assert [record.role for record in store.pending_shadow_executions(10)] == [
            "MAKER",
            "SHADOW",
        ]

        store.acknowledge_shadow_execution(1, "intent-1", "MAKER")
        assert [record.role for record in store.pending_shadow_executions(10)] == ["SHADOW"]


def test_reopen_fails_loud_on_orphaned_shadow_execution_outbox(tmp_path):
    path = str(tmp_path / "intent.db")
    with IntentStore(path, MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=_execution(),
        )

    with sqlite3.connect(path) as corrupt:
        corrupt.execute("PRAGMA foreign_keys=OFF")
        corrupt.execute("DELETE FROM shadow_executions WHERE execution_id='intent-1'")
        corrupt.commit()

    with pytest.raises(SettlementConflict, match="orphaned shadow execution outbox"):
        IntentStore(path, MonotonicStamper())


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome_slot": 2},
        {"sibling_token_ids": ("202", "303")},
        {"side": "SELL"},
        {"shares": Decimal("0")},
        {"shares": Decimal("NaN")},
        {"price_exec": Decimal("1.1")},
        {"fill_mid": Decimal("Infinity")},
        {"reward_accrued": Decimal("-0.01")},
    ],
)
def test_shadow_execution_record_rejects_noncanonical_or_nonfinite_values(overrides):
    with pytest.raises(ValueError):
        _execution(**overrides)


def test_non_accept_cannot_enqueue_and_rolls_back_decision_and_audit(tmp_path):
    with IntentStore(str(tmp_path / "intent.db"), MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)

        with pytest.raises(ValueError, match="only ACCEPT"):
            store.record_decision(
                "intent-1",
                Decision("REJECT", None, None, "no_book"),
                shadow_execution=_execution(),
            )

        assert store.get("intent-1").status == "PROPOSED"
        assert store.audit_log() == []
        assert store.pending_shadow_executions(10) == ()


def test_execution_identity_mismatch_rolls_back_the_whole_accept(tmp_path):
    with IntentStore(str(tmp_path / "intent.db"), MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)

        with pytest.raises(ValueError, match="identity contradicts intent"):
            store.record_decision(
                "intent-1",
                Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
                shadow_execution=_execution(token_id="202", outcome_slot=1),
            )

        assert store.get("intent-1").status == "PROPOSED"
        assert store.audit_log() == []
        assert store.pending_shadow_executions(10) == ()


def test_outbox_limit_and_acknowledgement_replay_boundaries(tmp_path):
    with IntentStore(str(tmp_path / "intent.db"), MonotonicStamper()) as store:
        with pytest.raises(ValueError, match="positive integer"):
            store.pending_shadow_executions(0)
        store.propose_trade("intent-1", **_PROPOSAL)
        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=_execution(),
        )
        assert [record.role for record in store.pending_shadow_executions(1)] == ["MAKER"]
        assert store.acknowledge_shadow_execution(1, "intent-1", "MAKER") is True
        assert store.acknowledge_shadow_execution(1, "intent-1", "MAKER") is False


def test_failure_before_accept_outbox_commit_rolls_back_every_surface(tmp_path):
    with IntentStore(str(tmp_path / "intent.db"), MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        store._before_shadow_execution_commit = lambda: (_ for _ in ()).throw(
            RuntimeError("injected precommit failure")
        )

        with pytest.raises(RuntimeError, match="precommit failure"):
            store.record_decision(
                "intent-1",
                Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
                shadow_execution=_execution(),
            )

        assert store.get("intent-1").status == "PROPOSED"
        assert store.audit_log() == []
        assert store.pending_shadow_executions(10) == ()
        assert store._conn.execute("SELECT * FROM shadow_executions").fetchall() == []


def test_reopen_rejects_missing_target_role_and_noncanonical_sibling_json(tmp_path):
    role_path = str(tmp_path / "missing-role.db")
    with IntentStore(role_path, MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=_execution(),
        )
    with sqlite3.connect(role_path) as corrupt:
        corrupt.execute(
            "DELETE FROM shadow_execution_outbox WHERE execution_id='intent-1' AND role='SHADOW'"
        )
        corrupt.commit()
    with pytest.raises(SettlementConflict, match="target roles"):
        IntentStore(role_path, MonotonicStamper())

    json_path = str(tmp_path / "bad-json.db")
    with IntentStore(json_path, MonotonicStamper()) as store:
        store.propose_trade("intent-1", **_PROPOSAL)
        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=_execution(),
        )
    with sqlite3.connect(json_path) as corrupt:
        corrupt.execute(
            "UPDATE shadow_executions SET sibling_token_ids='[\"101\", \"202\"]'"
        )
        corrupt.commit()
    with pytest.raises(SettlementConflict, match="canonical sibling JSON"):
        IntentStore(json_path, MonotonicStamper())
