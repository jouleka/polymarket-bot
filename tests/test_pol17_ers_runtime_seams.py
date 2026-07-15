"""POL-17 ERS seams for resolution-gated runtime composition."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import RUNNING, SafetyController
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book():
    book = LocalBook()
    book.apply_book({
        "bids": [{"price": "0.49", "size": "1000"}],
        "asks": [{"price": "0.52", "size": "1000"}],
    })
    return book


def _proposal(token_id, condition_id, event_id):
    return dict(
        token_id=token_id,
        condition_id=condition_id,
        event_id=event_id,
        side="BUY",
        target_price="0.49",
        max_price="0.60",
        size_usd_suggestion="12",
        p="0.90",
        p_confidence="0.75",
    )


def test_explicit_eligibility_defers_intent_without_any_ers_side_effect(tmp_path):
    calls = []
    books = {"eligible-token": _book(), "deferred-token": _book()}

    def book_for(token_id):
        calls.append(token_id)
        return books[token_id]

    with IntentStore(str(tmp_path / "intents.db"), MonotonicStamper()) as store:
        store.propose_trade(
            "eligible",
            **_proposal("eligible-token", "eligible-condition", "eligible-event"),
        )
        store.propose_trade(
            "deferred",
            **_proposal("deferred-token", "deferred-condition", "deferred-event"),
        )
        signer = PaperSigner()

        final = process_pending(
            store,
            book_for=book_for,
            portfolio=Portfolio(nav=Decimal("300")),
            caps=RiskCaps(),
            signer=signer,
            eligible_intent_ids=frozenset({"eligible"}),
        )

        assert store.get("eligible").status == "ACCEPTED"
        assert store.get("deferred").status == "PROPOSED"
        assert [intent.intent_id for intent in store.pending()] == ["deferred"]
        assert calls == ["eligible-token"]
        assert [order["intent_id"] for order in signer.placed] == ["eligible"]
        assert [position.token_id for position in final.positions] == ["eligible-token"]
        assert [row["intent_id"] for row in store.audit_log()] == ["eligible"]


def test_controller_threads_an_empty_confirmed_eligibility_set(tmp_path):
    with IntentStore(str(tmp_path / "intents.db"), MonotonicStamper()) as store:
        store.propose_trade(
            "deferred",
            **_proposal("deferred-token", "deferred-condition", "deferred-event"),
        )
        caps = RiskCaps()
        safety = SafetyController(caps=caps, store=store, clock=lambda: 1)
        safety.set_state(RUNNING, reason="test_reconcile")
        book_calls = []
        controller = ERSController(
            store=store,
            book_for=lambda token_id: book_calls.append(token_id) or _book(),
            caps=caps,
            signer=PaperSigner(),
            controller=safety,
            clock=lambda: 1,
        )

        controller.run_cycle(eligible_intent_ids=frozenset())

        assert store.get("deferred").status == "PROPOSED"
        assert book_calls == []
        assert store.audit_log() == []


def test_controller_resolution_state_only_retires_or_freezes_risk(tmp_path):
    initial = Portfolio(
        nav=Decimal("300"),
        positions=(
            OpenPosition("terminal", "e1", "s1", "k1", Decimal("12"),
                         token_id="t1", entry_price=Decimal("0.5")),
            OpenPosition("unknown", "e2", "s2", "k2", Decimal("8"),
                         token_id="t2", entry_price=Decimal("0.4")),
            OpenPosition("open", "e3", "s3", "k3", Decimal("4"),
                         token_id="t3", entry_price=Decimal("0.3")),
        ),
    )

    class Reconciler:
        def reconcile_on_boot(self):
            return initial

    with IntentStore(str(tmp_path / "intents.db"), MonotonicStamper()) as store:
        caps = RiskCaps()
        controller = ERSController(
            store=store,
            book_for=lambda _token_id: None,
            caps=caps,
            signer=PaperSigner(),
            controller=SafetyController(caps=caps, store=store, clock=lambda: 1),
            reconciler=Reconciler(),
            clock=lambda: 1,
        )
        controller.boot()

        resolved = controller.apply_resolution_state(
            terminal_condition_ids=("terminal",),
            frozen_condition_ids=("terminal", "unknown"),
        )

        assert resolved.nav == Decimal("300")
        assert [(position.condition_id, position.frozen) for position in resolved.positions] == [
            ("unknown", True),
            ("open", False),
        ]
        assert resolved.total_open_risk() == Decimal("12")
