"""S9 / POL-11 — the ERSController(reconciler=…) boot seam (additive; reconciler=None == today)."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


def test_reconciler_none_boot_is_a_noop_and_leaves_controller_halted(tmp_path):
    # reconciler=None (the default) -> boot() is a no-op: it returns None, the held SafetyController
    # stays HALTED (the construction default), and the portfolio is unchanged (empty at NAV). This
    # proves the seam is byte-for-byte inert when unwired -- the whole existing test suite relies on
    # it. NOT passing reconciler at all must construct exactly as today.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    try:
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=PaperSigner(), controller=ctl, clock=lambda: 0)  # no reconciler
        assert ctl.state() == _safety.HALTED           # boot default, untouched
        result = rc.boot()
        assert result is None                          # no-op returns None
        assert ctl.state() == _safety.HALTED           # STILL halted -- boot did nothing
        # The threaded portfolio is the empty construction portfolio (NAV only, no positions).
        final = rc.run_cycle()
        assert isinstance(final, Portfolio)
        assert final.positions == ()                   # nothing adopted
    finally:
        store.close()


from polybot.core.models import Envelope  # noqa: F401  (kept for parity with restart tests)
from polybot.ers.reconcile import ThreeWayReconciler
from polybot.ers.restart import RestartReconciler
from polybot.ers.validator import Decision
from polybot.storage.market_memory import EventStore

_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _accept_one(store, intent_id="i1", **over):
    # Drive one intent to ACCEPTED + record its fill, mirroring process_pending+make_fill_sink:
    # stake $8 of a token entered at $0.50 -> 16 shares, $8 worst-case risk. Verbatim from
    # tests/test_ers_restart.py so store.accepted() yields exactly one rebuildable row.
    store.propose_trade(intent_id, **dict(_P, **over))
    store.record_decision(intent_id, Decision("ACCEPT", Decimal("8"), Decimal("0.50"), "kelly"))
    store.record_fill(intent_id=intent_id, token_id=over.get("token_id", "t1"),
                      condition_id=over.get("condition_id", "m1"),
                      event_id=over.get("event_id", "e1"), side="BUY",
                      shares=Decimal("16"), price_exec=Decimal("0.50"),
                      worst_case_risk=Decimal("8"))


def test_boot_with_dormant_reconciler_transitions_running_and_adopts_portfolio(tmp_path):
    # A RestartReconciler with wallet=None (DORMANT shadow path) wired into the controller: boot()
    # flips the held SafetyController HALTED->RUNNING(restart_reconciled) AND adopts the Portfolio
    # rebuilt from the ACCEPTED set. This is the S9d seam finally connecting RestartReconciler to
    # boot. (Mirrors tests/test_ers_restart.py's DORMANT case, but THROUGH ERSController.boot().)
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        assert ctl.state() == _safety.HALTED                       # boot default
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 0, wallet=None)
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=PaperSigner(), controller=ctl, reconciler=rr, clock=lambda: 0)
        adopted = rc.boot()
        # The controller transitioned HALTED->RUNNING via the reconciler (the only automatic path).
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
        # boot() returned AND threaded the rebuilt portfolio (one position from the ACCEPTED row).
        assert isinstance(adopted, Portfolio)
        assert [p.token_id for p in adopted.positions] == ["t1"]
        pos = adopted.positions[0]
        assert pos.worst_case_risk == Decimal("8")
        assert pos.entry_price == Decimal("0.50")
        # The adopted portfolio is what run_cycle now threads (not the empty construction one).
        # RUNNING + the single pending intent already consumed -> the next cycle keeps the position.
        final = rc.run_cycle()
        assert [p.token_id for p in final.positions] == ["t1"]
    finally:
        store.close()
        events.close()
