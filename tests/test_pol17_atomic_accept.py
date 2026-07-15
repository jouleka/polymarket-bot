"""POL-17 atomic paper-ACCEPT durability."""

from decimal import Decimal
import math

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import (
    AcceptJournalRecord,
    IntentStore,
    ShadowExecutionRecord,
)
from polybot.ers.validator import Decision
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _journal(**overrides):
    values = dict(
        token_id="101",
        condition_id="0x" + "11" * 32,
        event_id="event-1",
        shares=Decimal("23.07692307692307692307692308"),
        price_exec=Decimal("0.52"),
        worst_case_risk=Decimal("12"),
        wall_at=1_750_000_000.25,
    )
    values.update(overrides)
    return AcceptJournalRecord(**values)


def test_accept_atomically_persists_restart_flow_and_execution_authority(tmp_path):
    path = str(tmp_path / "intents.db")
    condition_id = "0x" + "11" * 32
    execution = ShadowExecutionRecord(
        execution_id="intent-1",
        token_id="101",
        condition_id=condition_id,
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
    journal = _journal()

    with IntentStore(path, MonotonicStamper()) as store:
        store.propose_trade(
            "intent-1",
            token_id="101",
            condition_id=condition_id,
            event_id="event-1",
            side="BUY",
            target_price="0.49",
            max_price="0.60",
            size_usd_suggestion="12",
            p="0.80",
            p_confidence="0.75",
        )

        store.record_decision(
            "intent-1",
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
            shadow_execution=execution,
            accept_journal=journal,
        )

        assert store.get("intent-1").status == "ACCEPTED"
        assert store.audit_log()[-1]["verdict"] == "ACCEPT"
        assert store.fills_log() == [{
            "at": store.fills_log()[0]["at"],
            "intent_id": "intent-1",
            "token_id": "101",
            "condition_id": condition_id,
            "event_id": "event-1",
            "side": "BUY",
            "shares": journal.shares,
            "price_exec": journal.price_exec,
            "worst_case_risk": journal.worst_case_risk,
        }]
        assert store.flow_log() == [{
            "at": store.flow_log()[0]["at"],
            "wall_at": journal.wall_at,
            "kind": "accept",
            "token_id": "101",
            "amount": Decimal("12"),
        }]
        assert [record.role for record in store.pending_shadow_executions(10)] == [
            "MAKER",
            "SHADOW",
        ]

    with IntentStore(path, MonotonicStamper()) as reopened:
        assert reopened.get("intent-1").status == "ACCEPTED"
        assert reopened.fills_log()[0]["shares"] == journal.shares
        assert reopened.flow_log()[0]["wall_at"] == journal.wall_at
        assert all(
            record.execution == execution
            for record in reopened.pending_shadow_executions(10)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_id": ""},
        {"condition_id": 1},
        {"event_id": ""},
        {"shares": Decimal("0")},
        {"shares": Decimal("NaN")},
        {"shares": Decimal("1")},
        {"price_exec": Decimal("0")},
        {"price_exec": Decimal("Infinity")},
        {"worst_case_risk": Decimal("-1")},
        {"wall_at": math.inf},
        {"wall_at": True},
    ],
)
def test_accept_journal_rejects_noncanonical_or_inconsistent_values(overrides):
    with pytest.raises((TypeError, ValueError)):
        _journal(**overrides)


def test_process_pending_can_commit_restart_and_flow_authority_atomically(tmp_path):
    condition_id = "0x" + "11" * 32
    book = LocalBook()
    book.apply_book({
        "bids": [{"price": "0.49", "size": "1000"}],
        "asks": [{"price": "0.52", "size": "1000"}],
    })
    with IntentStore(str(tmp_path / "intents.db"), MonotonicStamper()) as store:
        store.propose_trade(
            "intent-1",
            token_id="101",
            condition_id=condition_id,
            event_id="event-1",
            side="BUY",
            target_price="0.49",
            max_price="0.60",
            size_usd_suggestion="12",
            p="0.90",
            p_confidence="0.75",
        )

        process_pending(
            store,
            book_for={"101": book}.get,
            portfolio=Portfolio(nav=Decimal("300")),
            caps=RiskCaps(),
            signer=PaperSigner(),
            accept_wall_clock=lambda: 1_750_000_000.25,
        )

        decision = store.get("intent-1")
        assert decision.status == "ACCEPTED"
        assert store.fills_log()[0]["shares"] == (
            decision.decision_stake_usd / decision.decision_price_exec
        )
        assert store.flow_log()[0] == {
            "at": store.flow_log()[0]["at"],
            "wall_at": 1_750_000_000.25,
            "kind": "accept",
            "token_id": "101",
            "amount": decision.decision_stake_usd,
        }
