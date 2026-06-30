"""ERSController runloop scaffold (S4.1 / POL-6).

The long-lived cadence driver that OWNS the SafetyController and wraps process_pending. Each
run_cycle: beat the heartbeat (if wired) THEN process_pending(controller=self._controller,
breaker=..., pipeline=...). Starts effectively HALTED -- the held SafetyController is HALTED on
construction, so a cycle before any clean transition blocks the loop. Later sub-slices extend the
cadence (L7 evaluate, signing canary, reconcile); this is the scaffold + the beat->process order.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner, process_pending  # noqa: F401
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


class _SpyHeartbeat:
    def __init__(self):
        self.beats = 0

    def beat(self):
        self.beats += 1


def test_run_cycle_starts_halted_and_blocks(tmp_path):
    # The held SafetyController starts HALTED, so a run_cycle before any clean transition rejects
    # every pending intent with unclean_restart -- the controller-driven loop does NOT trade.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        final = rc.run_cycle()
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "unclean_restart"
        assert signer.placed == []
        assert isinstance(final, Portfolio)
    finally:
        store.close()


def test_run_cycle_beats_heartbeat_then_processes(tmp_path):
    # With a RUNNING controller and a heartbeat wired, run_cycle beats THEN trades.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        hb = _SpyHeartbeat()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, heartbeat=hb, clock=lambda: 0)
        rc.run_cycle()
        assert hb.beats == 1                       # the heartbeat was beaten this cycle
        assert store.get("i1").status == "ACCEPTED"  # RUNNING -> the loop traded
        assert [o["token_id"] for o in signer.placed] == ["t1"]

    finally:
        store.close()


def test_run_cycle_without_heartbeat_still_runs(tmp_path):
    # heartbeat=None (default) must not break the cycle -- the beat is guarded.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)  # no heartbeat
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
    finally:
        store.close()


# --- S4.5a (POL-6): fill_sink pass-through ---------------------------------------------------
from polybot.ers.service import make_fill_sink


def test_fill_sink_none_default_records_no_fills(tmp_path):
    # Default fill_sink=None: a RUNNING controller's cycle ACCEPTs and places, but writes NO fills
    # row. Guards the S4.1 controller tests (the seam is additive).
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert store.fills_log() == []   # no sink wired -> no durable fill
    finally:
        store.close()


def test_wired_fill_sink_reaches_the_store_on_a_cycle_accept(tmp_path):
    # A make_fill_sink(store) passed to the controller is threaded into process_pending, so a
    # RUNNING-cycle ACCEPT records exactly one durable fill for the folded position.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0,
                           fill_sink=make_fill_sink(store))
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        fills = store.fills_log()
        assert len(fills) == 1
        assert fills[0]["token_id"] == "t1" and fills[0]["side"] == "BUY"
        assert fills[0]["shares"] == Decimal("24")  # 12 / 0.50, Decimal-exact
    finally:
        store.close()
