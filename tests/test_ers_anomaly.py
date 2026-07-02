"""L5 AnomalyMonitor -- the anomaly kill-switch spine (S4.4a / POL-6).

Sub-slice A: the l5_* reason constants, the AnomalyState/AnomalyMonitor skeleton driven by a
duck-typed skew-sentinel double (the real ClockSkewSentinel is S4.4b), the fail-closed
raising-seam rule, all-seams-None inertness, and the ERSController anomaly= seam --
edge-triggered halt-first one-shot cancel_all with exact op-audit rows, sticky semantics, and
a raising signer that never unwinds the halt. Clocks are injected; helpers are copied per
file per convention (no conftest)."""


def test_s4_4_l5_reason_constants_exist_with_exact_strings():
    # S4.4a defines the five NET-NEW l5_* reason codes (l5_recon_mismatch already exists from
    # S4.5). Free-form Decision.reason / op-audit strings, NO validator/schema change -- the
    # existing REASON_* convention (mirrors test_s4_5_reason_constants_exist).
    # MUTATION KILLED: renaming any constant or typo-ing its string (the controller reports
    # these verbatim as the halt reason).
    from polybot.ers import safety as _s
    assert _s.REASON_L5_CLOCK_SKEW == "l5_clock_skew"
    assert _s.REASON_L5_ABNORMAL_BOOK == "l5_abnormal_book"
    assert _s.REASON_L5_API_STORM == "l5_api_storm"
    assert _s.REASON_L5_WS_DOWN == "l5_ws_down"
    assert _s.REASON_L5_CANARY_FAIL == "l5_canary_fail"
    # The pre-existing S4.5 constant this slice consumes (guards accidental removal).
    assert _s.REASON_L5_RECON_MISMATCH == "l5_recon_mismatch"


# --- ers/anomaly.py: module vocab + AnomalyState ----------------------------------------------
import dataclasses

import pytest


def test_anomaly_module_action_vocab_is_none_and_halt_exact_strings():
    # The AnomalyState.action vocabulary, module-constant style mirroring breaker.py's
    # NONE/FREEZE_ADDS/FLATTEN. MUTATION KILLED: changing either constant's string (the
    # controller compares state.action == HALT by value).
    from polybot.ers import anomaly as _a
    assert _a.NONE == "NONE"
    assert _a.HALT == "HALT"


def test_anomaly_state_is_a_frozen_dataclass_with_action_and_triggers():
    # AnomalyState is immutable evidence (mirrors BreakerState: action + provenance tuple).
    # MUTATION KILLED: dropping frozen=True, or renaming the action/triggers fields.
    from polybot.ers.anomaly import HALT, AnomalyState
    state = AnomalyState(action=HALT, triggers=("l5_clock_skew",))
    assert state.action == "HALT"
    assert state.triggers == ("l5_clock_skew",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.action = "NONE"


def test_anomaly_state_halt_with_empty_triggers_is_unrepresentable():
    # S4.4a review LOW-2: HALT => non-empty triggers, pinned AT THE BOUNDARY. The controller's
    # kill path reports state.triggers[0] verbatim as the halt reason. MUTATION KILLED:
    # removing the __post_init__ guard lets a triggerless HALT reach the controller's
    # triggers[0] = IndexError in the kill path at the exact moment an anomaly fires.
    from polybot.ers.anomaly import HALT, AnomalyState
    with pytest.raises(ValueError):
        AnomalyState(action=HALT, triggers=())


def test_monitor_with_all_seams_none_is_inert_and_returns_action_none():
    # Dormant-by-default (design §6.5): a bare AnomalyMonitor(caps, clock=...) with every
    # seam left None must NEVER fire, whatever positions/books look like -- the data-gated
    # pattern. MUTATION KILLED: any seam consult that fires when its seam is None (e.g.
    # dropping an `is not None` guard).
    from polybot.ers.anomaly import NONE as A_NONE, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0)
    state = monitor.evaluate((), {}.get)
    assert state.action == A_NONE
    assert state.triggers == ()


class _SkewDouble:
    """Duck-typed skew-sentinel double (.skewed() -> bool); the real ClockSkewSentinel lands
    in S4.4b. Mutable so the sticky tests can CLEAR the anomaly between cycles."""

    def __init__(self, skewed):
        self.is_skewed = skewed

    def skewed(self):
        return self.is_skewed


def test_truthy_skew_sentinel_fires_halt_with_the_l5_clock_skew_trigger():
    # Fire side of the skew boundary pair. MUTATION KILLED: dropping the skew consult, or
    # appending the wrong reason string (the controller reports triggers[0] verbatim as the
    # set_state reason).
    from polybot.ers.anomaly import HALT as A_HALT, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_SkewDouble(True))
    state = monitor.evaluate((), {}.get)
    assert state.action == A_HALT
    assert state.triggers == ("l5_clock_skew",)


def test_falsy_skew_sentinel_keeps_action_none_with_no_triggers():
    # No-fire side of the pair (explicit boundary partner of the test above). MUTATION
    # KILLED: inverting the .skewed() check (`if not ...skewed()`).
    from polybot.ers.anomaly import NONE as A_NONE, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_SkewDouble(False))
    state = monitor.evaluate((), {}.get)
    assert state.action == A_NONE
    assert state.triggers == ()


class _RaisingSkew:
    """A wired sentinel that explodes -- per the FAIL-CLOSED SEAM RULE this IS the anomaly."""

    def skewed(self):
        raise RuntimeError("skew sentinel exploded")


def test_raising_skew_sentinel_fires_its_own_trigger_instead_of_propagating():
    # FAIL-CLOSED SEAM RULE (design §6.4): a wired sentinel that RAISES inside evaluate fires
    # its own trigger -- append + continue; never mask, never propagate. MUTATION KILLED:
    # letting the exception escape evaluate, or except-ing to a silent `pass`.
    from polybot.ers.anomaly import HALT as A_HALT, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_RaisingSkew())
    state = monitor.evaluate((), {}.get)   # must NOT raise
    assert state.action == A_HALT
    assert state.triggers == ("l5_clock_skew",)


def test_anomaly_module_source_never_references_running_or_set_state():
    # STICKY pin (design §6.1, Fork 1; the detectors FOLLOW-off structural style): nothing in
    # ers/anomaly.py may ever transition op-state or even NAME the resume state -- the ONLY
    # automatic HALTED->resume in the system stays RestartReconciler's clean boot-reconcile.
    # MUTATION KILLED: any auto-resume (or any op-state mutation at all) creeping into the
    # monitor module.
    from pathlib import Path

    from polybot.ers import anomaly as _a
    src = Path(_a.__file__).read_text(encoding="utf-8")
    assert "set_state" not in src
    assert "RUNNING" not in src


# --- ERSController anomaly= seam (the run_cycle kill-path wiring) -----------------------------
from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ingestion.orderbook import LocalBook


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _monitor(skew):
    from polybot.ers.anomaly import AnomalyMonitor
    return AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=skew)


def _rc(store, ctl, signer, *, anomaly):
    return ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                         signer=signer, controller=ctl, anomaly=anomaly, clock=lambda: 0)


class _StateSnoopingSigner(PaperSigner):
    """PaperSigner that records the op-state AT THE MOMENT cancel_all is called -- proves the
    gate closed (HALTED) BEFORE the de-risk fired."""

    def __init__(self, ctl):
        super().__init__()
        self._ctl = ctl
        self.state_at_cancel = []

    def cancel_all(self):
        self.state_at_cancel.append(self._ctl.state())
        super().cancel_all()


def test_new_anomaly_from_running_halts_first_then_cancels_once_with_exact_audit_rows(tmp_path):
    # Design §2 / invariant 2: on a NEW anomaly while RUNNING the controller (1) closes the
    # gate FIRST -- set_state(HALTED, reason=state.triggers[0]), audited by set_state -- THEN
    # (2) fires exactly ONE cancel_all and (3) writes exactly one kind="cancel_all" op-audit
    # row with reason=triggers[0], detail=",".join(triggers).
    # MUTATIONS KILLED: swapping the halt/cancel order (state_at_cancel would read RUNNING);
    # double-firing cancel_all; wrong reason/detail strings on either audit row.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = _StateSnoopingSigner(ctl)
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))

        rc.run_cycle()

        assert ctl.state() == _safety.HALTED               # the gate is closed...
        assert signer.state_at_cancel == [_safety.HALTED]  # ...and was ALREADY closed at cancel time
        assert len(signer.cancelled_all) == 1              # one-shot de-risk
        rows = store.op_audit_log()
        # EXACT op-audit sequence: setup transition, then halt-first, then the de-risk row.
        assert [(r["kind"], r["reason"], r["detail"]) for r in rows] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("state_change", "l5_clock_skew", _safety.HALTED),
            ("cancel_all", "l5_clock_skew", "l5_clock_skew"),
        ]


def test_anomaly_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Design §6.5 dormant-by-default: an ERSController WITHOUT the anomaly kwarg (the None
    # default) trades exactly as before S4.4 -- ACCEPT, no cancel_all, no anomaly audit rows.
    # Expected GREEN from birth: it pins the seam's None default (the 556-test baseline is
    # the wider proof). MUTATION KILLED: making the seam mandatory, or consulting/de-risking
    # when the monitor is None.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)   # anomaly unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_already_halted_loop_never_refires_cancel_all_or_state_change(tmp_path):
    # Edge-triggered (design §2): the monitor evaluates every cycle, but an ALREADY-HALTED
    # loop is never re-de-risked and never re-audited -- no audit spam, no cancel_all churn
    # against the standing GTD exits. Start = boot HALTED (unclean_restart), anomaly firing.
    # MUTATION KILLED: dropping the op-state edge guard entirely.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)   # boot: HALTED
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert signer.cancelled_all == []      # no de-risk fired from HALTED
        assert store.op_audit_log() == []      # no state_change row, no cancel_all row


def test_anomaly_still_firing_on_the_next_cycle_does_not_refire_the_one_shot(tmp_path):
    # Edge-triggered, cycle 2: after the halt, a STILL-firing anomaly must not re-fire
    # cancel_all or append further audit rows -- exactly one halt + one de-risk, ever.
    # MUTATION KILLED: level-triggered re-firing on every cycle the sentinel stays skewed.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()     # fires: halt + ONE cancel_all
        rc.run_cycle()     # still skewed -- must be a no-op on the kill path
        assert len(signer.cancelled_all) == 1
        kinds = [r["kind"] for r in store.op_audit_log()]
        assert kinds.count("cancel_all") == 1
        assert kinds.count("state_change") == 2    # clean_reconcile + the ONE l5 halt


def test_flattening_in_flight_is_not_preempted_by_an_anomaly(tmp_path):
    # Design §2: FLATTENING is a STRONGER de-risk already in flight -- the anomaly path must
    # not preempt it. The cycle proceeds to process_pending where the op-FLATTEN verdict
    # de-risks (flatten + cancel working entries) and settles HALTED on its own (I1); the
    # anomaly path contributes NO l5 state_change and NO kind="cancel_all" row.
    # MUTATION KILLED: widening the edge guard to preempt FLATTENING -- which would SKIP the
    # flatten de-risk entirely (strictly riskier).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.FLATTENING, reason=_safety.REASON_OP_FLATTEN)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert len(signer.flattened) == 1          # the op-FLATTEN de-risk ran (empty book OK)
        assert len(signer.cancelled_all) == 1      # from the FLATTEN path ONLY
        rows = store.op_audit_log()
        assert [r["kind"] for r in rows].count("cancel_all") == 0   # anomaly one-shot did NOT fire
        assert not any(r["reason"] == "l5_clock_skew" for r in rows)
        assert ctl.state() == _safety.HALTED       # settled by FLATTENING itself (I1)


def test_paused_loop_escalates_to_halted_on_an_anomaly(tmp_path):
    # Design §2: PAUSED is a LIVE loop (blocks new trades only) -- an anomaly must still
    # escalate it to the sticky HALTED + the one-shot de-risk. Expected GREEN once the guard
    # is (RUNNING, PAUSED); it exists to KILL the over-tightened (RUNNING,)-only guard
    # mutation -- verified by the Step-4 mutation check.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
        rows = store.op_audit_log()
        assert ("cancel_all", "l5_clock_skew") in [(r["kind"], r["reason"]) for r in rows]


def test_halt_is_sticky_after_the_anomaly_clears_and_next_intent_rejects_with_the_l5_reason(tmp_path):
    # Fork 1 / design §6.1 STICKY: the anomaly CLEARING does not resume the loop -- op-state
    # stays HALTED with the stored l5 reason, and an intent proposed AFTER the halt is
    # REJECTED with Decision.reason == "l5_clock_skew" (the controller's stored reason
    # surfaces verbatim through the untouched verdict path, §6.6). Recovery is operator-owned.
    # MUTATION KILLED: any auto-resume branch in run_cycle (see the Step-2 mutation check),
    # and a generic reason string masking the specific l5_* one.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        skew = _SkewDouble(True)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(skew))
        rc.run_cycle()                       # cycle 1: anomaly -> halt + one-shot de-risk
        assert ctl.state() == _safety.HALTED

        skew.is_skewed = False               # the anomaly CLEARS...
        store.propose_trade("i1", **_P)      # ...and a fresh intent arrives
        rc.run_cycle()                       # cycle 2

        assert ctl.state() == _safety.HALTED             # ...but the halt is STICKY
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "l5_clock_skew"
        assert len(signer.cancelled_all) == 1            # and the one-shot stayed one-shot


class _RaisingCancelSigner(PaperSigner):
    """cancel_all raises (venue/RPC down at the worst moment): the halt must already be in
    place and must SURVIVE; the failure is audited; the cycle continues."""

    def cancel_all(self):
        raise RuntimeError("venue rejected cancelAll")


def test_raising_cancel_all_is_audited_as_failed_and_never_unwinds_the_halt_or_the_cycle(tmp_path):
    # Design §2 / invariant 2: a raising signer must NOT unwind the halt or kill the cycle --
    # the gate closed FIRST, the failure lands in op_audit as detail="FAILED: ...", and
    # process_pending still runs (the pending intent is REJECTED under the l5 reason; the
    # standing GTD exits are the backstop). MUTATION KILLED: letting the exception propagate
    # out of run_cycle (the S4.3 supervisor would SIGKILL a healthy-but-unlucky loop), and
    # auditing an unconditional success detail.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = _RaisingCancelSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))

        rc.run_cycle()                                   # must NOT raise

        assert ctl.state() == _safety.HALTED             # the halt held
        cancel_rows = [r for r in store.op_audit_log() if r["kind"] == "cancel_all"]
        assert len(cancel_rows) == 1
        assert cancel_rows[0]["reason"] == "l5_clock_skew"
        assert cancel_rows[0]["detail"] == "FAILED: venue rejected cancelAll"
        # The cycle SURVIVED to process_pending: the intent is blocked under the l5 reason.
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "l5_clock_skew"
