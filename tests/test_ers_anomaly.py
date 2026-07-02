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
