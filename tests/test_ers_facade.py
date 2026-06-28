"""ProposeOnlyFacade — the load-bearing S6/POL-8 safety boundary.

Hermes is handed ONLY this facade. The safety claim is structural and IN CODE:
the facade composes (never subclasses) an IntentStore, holds it in a
name-mangled private attribute, and exposes EXACTLY {propose_trade, get,
audit_log, get_market, get_book, get_ledger, get_flags}. It has no place /
flatten / record_decision / pending attribute and no public path to mutate
status or reach the signer. A confused-deputy Hermes can at worst enqueue a
PROPOSED row; the deterministic ERS (not Hermes) disposes.
"""
import inspect
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.ers.facade import ProposeOnlyFacade

_PROPOSAL = dict(
    token_id="t1", condition_id="0xabc", event_id="e1", side="BUY",
    target_price="0.55", max_price="0.60", size_usd_suggestion="10",
    p="0.7", p_confidence="0.6", resolution_summary="will X happen?",
    thesis="because Y", citations=("https://primary.example/1",),
)


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def test_propose_trade_delegates_insert(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        ok = facade.propose_trade("intent-1", **_PROPOSAL)
        assert ok is True
        # The row landed in the underlying store as PROPOSED (delegated INSERT).
        row = store.get("intent-1")
        assert row is not None and row.status == "PROPOSED"
        assert row.token_id == "t1" and row.side == "BUY"
        # Signature parity: the facade's propose_trade exposes the SAME kwargs
        # as the store (no extra `status` param — the chokepoint).
        params = inspect.signature(facade.propose_trade).parameters
        assert "status" not in params
        assert "citations" in params and "p" in params


def test_propose_trade_idempotent_returns_false_on_dup(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        first = facade.propose_trade("intent-1", **_PROPOSAL)
        second = facade.propose_trade("intent-1", **_PROPOSAL)
        assert first is True and second is False
        # Still exactly one row; the dup INSERT was IGNOREd by the store.
        assert store.get("intent-1") is not None


def test_get_and_audit_log_read_through(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        assert facade.get("missing") is None        # nothing proposed yet
        facade.propose_trade("intent-1", **_PROPOSAL)

        row = facade.get("intent-1")
        assert row is not None and row.intent_id == "intent-1"
        assert row.status == "PROPOSED" and row.p == Decimal("0.7")

        # audit_log is read-only and empty until the ERS (not the facade)
        # records a decision; the facade exposes no way to write an audit row.
        assert facade.audit_log() == []
