"""S4.7c -- the flow gate (POL-6; DESIGN-S4.7-BREAKERS SS3 rows 1-2, SS4).

The nine S4.7 REASON_* constants, SafetyController.wire_flow_gate (one-shot late binder),
the verdict RUNNING-branch consult (the gate BLOCKS without touching op-state -- the block
auto-slides with the window; a raising gate fail-closes to flow_gate_error), make_flow_gate's
three ordered arms (hourly rate, daily rate, conservative per_trade-headroom daily ceiling),
and the gate-through-verdict e2e. Helpers are copied per file per convention (no conftest)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import OpVerdict, SafetyController
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _store(path):
    return IntentStore(path, MonotonicStamper())


def _running_controller(tmp_path):
    """A controller already transitioned to RUNNING (so only the gate can block)."""
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl, store


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def test_s4_7_flow_loss_ramp_reason_constants_exist_with_exact_strings():
    # The nine NET-NEW S4.7 reason codes -- free-form Decision.reason / op-audit strings, NO
    # validator/schema change (mirrors test_s4_4_l5_reason_constants_exist_with_exact_strings).
    # Kills: renaming any constant or typo-ing its string (the gate/breakers/ratchet report
    # these verbatim as block/halt/audit reasons).
    from polybot.ers import safety as _s
    assert _s.REASON_RATE_HOURLY == "rate_cap_hourly"
    assert _s.REASON_RATE_DAILY == "rate_cap_daily"
    assert _s.REASON_DAILY_CEILING == "daily_ceiling"
    assert _s.REASON_DAILY_PENDING_PAUSE == "daily_pending_pause"
    assert _s.REASON_WEEKLY_LOSS == "weekly_loss_halt"
    assert _s.REASON_CONSECUTIVE_LOSS == "consecutive_loss"
    assert _s.REASON_RAMP_DOWN == "ramp_down"
    assert _s.REASON_FLOW_GATE_ERROR == "flow_gate_error"
    assert _s.REASON_FLOW_DATA_ERROR == "flow_data_error"


def test_wire_flow_gate_second_call_raises_runtime_error(tmp_path):
    # One-shot late binder (design SS4: the gate needs caps_provider=controller.active_caps,
    # so it cannot be a ctor kwarg). Kills: dropping the already-wired guard (a silent re-wire
    # could swap the safety gate out from under a running loop).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: None)
        with pytest.raises(RuntimeError):
            ctl.wire_flow_gate(lambda: None)
    finally:
        ctl_store.close()


def test_unwired_running_verdict_is_byte_identical_to_today(tmp_path):
    # Unwired == today byte-for-byte: the RUNNING branch returns the no-block verdict
    # (the existing 660-baseline suite pins the other branches). Kills: __init__ pre-wiring
    # _flow_gate to anything non-None (a phantom gate would block a clean RUNNING loop).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, None, None, ())
    finally:
        ctl_store.close()


def test_running_verdict_with_gate_returning_none_does_not_block(tmp_path):
    # No-block side of the consult pair. Kills: inverting the `reason is not None` check
    # (blocking on None would wedge every clean RUNNING cycle).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: None)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, None, None, ())
    finally:
        ctl_store.close()


def test_running_verdict_with_gate_reason_blocks_but_op_state_and_audit_are_untouched(tmp_path):
    # A gate reason blocks THIS cycle's intents while action stays RUNNING, state() stays
    # RUNNING, and NO op-audit row is written -- the block must auto-slide with the window
    # (design SS2 "the gate blocks, states stick"; no new auto-resume path exists to undo a
    # sticky transition). Kills: the consult calling set_state or record_op_event (a sticky
    # gate block would then need an operator RESUME every hour).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: _safety.REASON_RATE_HOURLY)
        audit_before = ctl_store.op_audit_log()
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, "rate_cap_hourly", None, ("rate_cap_hourly",))
        assert ctl.state() == _safety.RUNNING
        assert ctl_store.op_audit_log() == audit_before
    finally:
        ctl_store.close()
