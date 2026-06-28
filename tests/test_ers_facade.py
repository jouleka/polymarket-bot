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


def test_read_tools_delegate_to_readers(tmp_path):
    calls = {"market": [], "book": [], "ledger": [], "flags": []}

    def _market_reader(*a, **k):
        calls["market"].append((a, k)); return "MARKET"

    def _book_reader(*a, **k):
        calls["book"].append((a, k)); return "BOOK"

    def _ledger_reader(*a, **k):
        calls["ledger"].append((a, k)); return "LEDGER"

    def _flags_reader(*a, **k):
        calls["flags"].append((a, k)); return "FLAGS"

    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(
            store, market_reader=_market_reader, book_reader=_book_reader,
            ledger_reader=_ledger_reader, flags_reader=_flags_reader,
        )
        assert facade.get_market("0xabc") == "MARKET"
        assert facade.get_book("t1", depth=5) == "BOOK"
        assert facade.get_ledger(category="politics") == "LEDGER"
        assert facade.get_flags("t1") == "FLAGS"

        # Each reader was invoked exactly once with the forwarded args/kwargs.
        assert calls["market"] == [(("0xabc",), {})]
        assert calls["book"] == [(("t1",), {"depth": 5})]
        assert calls["ledger"] == [((), {"category": "politics"})]
        assert calls["flags"] == [(("t1",), {})]


def test_structural_sweep_no_signer_or_status_path(tmp_path):
    """Load-bearing safety guarantee: the facade exposes EXACTLY the allowed
    public names and NO dangerous attribute. This is the property that makes
    'Hermes can at worst enqueue' true in code, surviving careless future
    wiring."""
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)

        # (a) The public surface is EXACTLY the allowed set -- nothing more.
        allowed = {
            "propose_trade", "get", "audit_log",
            "get_market", "get_book", "get_ledger", "get_flags",
        }
        public = {name for name in dir(facade) if not name.startswith("_")}
        assert public == allowed, f"unexpected public surface: {public ^ allowed}"

        # (b) No dispose/mutate/signer attribute is reachable on the facade,
        #     by any access path (hasattr covers inherited + instance attrs).
        for forbidden in ("place", "flatten", "record_decision", "pending",
                          "signer", "store"):
            assert not hasattr(facade, forbidden), forbidden
            assert forbidden not in dir(facade), forbidden

        # (c) The facade did NOT subclass IntentStore (composition only), so it
        #     inherits none of the store's dispose methods.
        assert not isinstance(facade, IntentStore)
        assert IntentStore not in type(facade).__mro__

        # (d) The store ref exists ONLY under name-mangling -- there is no plain
        #     `store` / `_store` attribute Hermes could dot into.
        assert not hasattr(facade, "_store")
        assert getattr(facade, "_ProposeOnlyFacade__store", None) is store

        # (e) Even reaching the mangled store, propose_trade has no `status`
        #     param: there is no public path to transition a status or sign.
        assert "status" not in inspect.signature(facade.propose_trade).parameters


def test_read_tools_fail_loud_without_reader(tmp_path):
    """A reader is None by default; calling that read tool must raise, not
    silently return None -- fail-closed over a misconfigured wiring."""
    import pytest
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)        # no readers injected
        with pytest.raises(TypeError):           # None is not callable
            facade.get_market("0xabc")
        with pytest.raises(TypeError):
            facade.get_book("t1")
        with pytest.raises(TypeError):
            facade.get_ledger()
        with pytest.raises(TypeError):
            facade.get_flags("t1")
