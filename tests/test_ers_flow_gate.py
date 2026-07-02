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


def test_raising_gate_fail_closes_with_flow_gate_error_and_state_stays_running(tmp_path):
    # Fail closed on our own data (design SS6.4): a raising gate means the flow_journal read
    # is corrupt -- the verdict blocks with flow_gate_error instead of propagating, and the
    # op-state is untouched (the block clears if the read recovers; no operator unwind needed).
    # Kills: letting the exception escape verdict (wedges process_pending), or except-ing to a
    # silent no-block pass (trades through corruption).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        def _corrupt_gate():
            raise RuntimeError("flow_journal corrupted")
        ctl.wire_flow_gate(_corrupt_gate)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, "flow_gate_error", None, ("flow_gate_error",))
        assert ctl.state() == _safety.RUNNING
    finally:
        ctl_store.close()


def test_paused_verdict_never_consults_the_gate(tmp_path):
    # The consult lives ONLY in the RUNNING branch: PAUSED blocks under its stored reason and
    # the gate is never called. Kills: hoisting the consult above the state dispatch (a gate
    # reason could then overwrite the sticky paused reason the operator must see).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        calls = []
        def _counting_gate():
            calls.append(1)
            return None
        ctl.wire_flow_gate(_counting_gate)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v.block_reason == "l8_paused"
        assert calls == []
    finally:
        ctl_store.close()


def test_halted_verdict_never_consults_the_gate(tmp_path):
    # HALTED boundary partner (the boot default): blocks unclean_restart, gate never called.
    # Kills: hoisting the consult above the state dispatch.
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    try:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # starts HALTED
        calls = []
        def _counting_gate():
            calls.append(1)
            return None
        ctl.wire_flow_gate(_counting_gate)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v.block_reason == "unclean_restart"
        assert calls == []
    finally:
        store.close()


def test_flow_gate_one_accept_in_the_hour_returns_none_under_cap_two(tmp_path):
    # At-boundary partner: 1 accept < new_positions_per_hour(2) -> the 2nd is still allowed.
    # Kills: off-by-one down (count >= cap - 1), which would block with headroom left.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() is None


def test_flow_gate_two_accepts_in_the_hour_blocks_the_would_be_third(tmp_path):
    # Just-over partner: 2 accepts == new_positions_per_hour(2) -> rate_cap_hourly (blocking
    # the WOULD-BE 3rd). Amounts are tiny so no other arm can fire.
    # Kills: >= mutated to > (2 > 2 would let a 3rd position through the signed rate cap).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() == "rate_cap_hourly"


def test_flow_gate_re_reads_the_journal_on_every_call(tmp_path):
    # The gate is consulted PER CYCLE: rows recorded after make_flow_gate must count.
    # Kills: capturing store.flow_log() once at make time (new accepts would never be counted).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() is None
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        assert gate() == "rate_cap_hourly"


def test_flow_gate_auto_slides_open_when_the_window_passes(tmp_path):
    # Blocked at the inclusive old edge (age == 3600 still counts -- the breaker/ApiStorm
    # convention, keeping the boundary row is tighter), open one second past it. Recovery is
    # AUTOMATIC: no set_state, no operator. Kills: freezing wall_clock() at make time (a
    # captured `now` would block forever), and flipping the inclusive <= window edge.
    from polybot.ers.flow import make_flow_gate
    wall = [200.0]
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: wall[0])
        assert gate() == "rate_cap_hourly"
        wall[0] = 3700.0   # oldest accept age == 3600 exactly -> STILL in-window (inclusive);
        assert gate() == "rate_cap_hourly"          # both rows count -> still >= cap 2
        wall[0] = 3701.0   # oldest ages to 3601 -> out; only 1 accept remains in the hour;
        assert gate() is None                       # daily 2 < 6; pending 2 + 12 <= 24 -> open again


def test_flow_gate_consults_the_caps_provider_on_every_call(tmp_path):
    # The gate follows the ratchet: a tightened envelope flips the verdict on the SAME journal.
    # Kills: capturing caps_provider() once at make time (a swap_caps ramp step would never
    # bite the gate -- design SS4 binds caps_provider=controller.active_caps for exactly this).
    from polybot.ers.flow import make_flow_gate
    caps_cell = [RiskCaps()]
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: caps_cell[0], wall_clock=lambda: 200.0)
        assert gate() is None                                # 1 accept < hour cap 2
        caps_cell[0] = RiskCaps(new_positions_per_hour=1)    # tighten (1 <= day 6: constructible)
        assert gate() == "rate_cap_hourly"                   # same rows, tighter caps -> blocked


def test_flow_gate_six_accepts_spread_over_the_day_blocks_daily_rate(tmp_path):
    # 6 accepts all OLDER than the hour (hourly arm sees 0) but inside 24h == the daily cap(6)
    # -> rate_cap_daily. Ages run 45000..50000s: every row is > 3600 old and <= 86400 old.
    # Kills: dropping the daily arm, or windowing it over 3600s instead of 86400s.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        for i, at in enumerate((0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0)):
            store.record_flow_event(kind="accept", token_id=f"a{i}", amount=Decimal("1"), wall_at=at)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 50000.0)
        assert gate() == "rate_cap_daily"


def test_flow_gate_hourly_wins_when_both_rate_arms_are_breached(tmp_path):
    # 6 accepts inside ONE hour breach both arms (6 >= 2 hourly AND 6 >= 6 daily); the reason
    # must be the hourly one -- checked FIRST (design SS3 row 1, the SS4 pinned order).
    # Kills: re-ordering the arms (daily-first would misreport the block reason the operator
    # and the intent audit see).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        for i, at in enumerate((100.0, 200.0, 300.0, 400.0, 500.0, 600.0)):
            store.record_flow_event(kind="accept", token_id=f"a{i}", amount=Decimal("1"), wall_at=at)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 700.0)
        assert gate() == "rate_cap_hourly"
