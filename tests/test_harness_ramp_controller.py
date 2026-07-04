"""S9 / POL-11 — RampController.decide (the binary stage machine: advisory promote, auto ramp-down)."""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig
from polybot.harness.evidence import EvidenceReport
from polybot.harness.ramp_controller import RAMP, SHADOW, TINY_LIVE, RampController, RampDecision


def _evidence(*, ready, category="sports", oos_positive=True, calibration_ok=True,
              maker_ok=True, n_resolved=200, n_oos=60):
    """A pinned EvidenceReport (S9c contract). Only `.ready` drives decide(); the sub-flags
    let a test say WHICH gate failed so `reason` can be asserted. Other numeric fields are
    carried through into RampDecision.evidence."""
    return EvidenceReport(
        category=category, n_resolved=n_resolved, n_oos=n_oos, n_disputed=2,
        net_full=Decimal("5"), net_oos=Decimal("3"), brier_skill=Decimal("0.2"),
        reliability=Decimal("0.01"), k=Decimal("1"), maker_go=maker_ok,
        required_margin=Decimal("0"), oos_positive=oos_positive,
        calibration_ok=calibration_ok, maker_ok=maker_ok, ready=ready)


def _controller():
    return RampController(ramp_config=RampConfig(), caps=RiskCaps())


def _healthy_portfolio():
    # One small position that leaves the reserve floor intact under a 100%-adverse freeze:
    # RiskCaps() nav=300, reserve_floor=240; a single $8 worst-case position frozen ->
    # reserve_after = 300 - 0 - 8 = 292 >= 240 -> survives.
    return Portfolio(nav=Decimal("300"), positions=(
        OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                     cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                     entry_price=Decimal("0.50")),
    ))


def test_not_ready_forces_shadow_and_no_promote_with_reason():
    # evidence.ready False (OOS gate the culprit) -> stage collapses to SHADOW, promote False,
    # ramp_down False (still in SHADOW so a not-ready is not a regression), reason names the gate.
    c = _controller()
    ev = _evidence(ready=False, oos_positive=False)
    d = c.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert isinstance(d, RampDecision)
    assert d.stage == SHADOW
    assert d.promote_recommended is False
    assert d.ramp_down is False
    assert d.reason == "not_ready:oos"
    assert d.evidence is ev            # the report is carried through verbatim
    assert d.category == "sports"


def test_not_ready_reason_distinguishes_calibration_and_maker():
    # The reason string names the SPECIFIC failed evidence gate (calibration vs maker), so an
    # operator reading the decision knows why a category is still SHADOW.
    c = _controller()
    d_cal = c.decide("sports", evidence=_evidence(ready=False, calibration_ok=False),
                     current_stage=SHADOW, portfolio=_healthy_portfolio(),
                     n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d_cal.stage == SHADOW and d_cal.reason == "not_ready:calibration"
    d_mk = c.decide("sports", evidence=_evidence(ready=False, maker_ok=False),
                    current_stage=SHADOW, portfolio=_healthy_portfolio(),
                    n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d_mk.stage == SHADOW and d_mk.reason == "not_ready:maker"


def test_promote_recommended_true_only_when_all_four_hold():
    # The healthy promotion: ready AND tail survived AND stress survives AND no breaker ->
    # promote_recommended True, reason promote_ok, ramp_down False. stage stays current_stage
    # (SHADOW here) -- promotion PAST it is the operator's human gate, not the controller's.
    c = _controller()
    ev = _evidence(ready=True)
    d = c.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d.promote_recommended is True
    assert d.ramp_down is False
    assert d.reason == "promote_ok"
    assert d.stage == SHADOW


def test_promote_blocked_when_tail_not_survived():
    # ready but n_resolved_disputed 0 < min_resolved_disputed(1) -> tail_survived False ->
    # promote False, reason blocked:tail. (RampConfig() defaults: min_resolved_disputed 1.)
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=0, stress_episodes=1,
                 breaker_tripped=False)
    assert d.promote_recommended is False
    assert d.reason == "blocked:tail"
    assert d.ramp_down is False          # still SHADOW + ready -> not a regression


def test_promote_blocked_when_stress_does_not_survive():
    # ready + tail, but a portfolio whose largest-cluster 100%-adverse freeze breaches the
    # reserve floor -> dispute_freeze_stress.survives False -> promote False, reason blocked:stress.
    # One $70 worst-case position (over the $60 ceiling, but the stress test is pure over the
    # portfolio it is given): frozen markdown 70 -> reserve_after = 300 - 0 - 70 = 230 < 240.
    c = _controller()
    breach = Portfolio(nav=Decimal("300"), positions=(
        OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                     cluster_id="c1", worst_case_risk=Decimal("70"), token_id="t1",
                     entry_price=Decimal("0.50")),
    ))
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=breach, n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=False)
    assert d.promote_recommended is False
    assert d.reason == "blocked:stress"


def test_promote_blocked_when_breaker_tripped():
    # ready + tail + stress, but a tripped breaker -> promote False. A breaker ALSO raises
    # ramp_down (D3), and the breaker reason dominates the string.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=True)
    assert d.promote_recommended is False
    assert d.ramp_down is True
    assert d.reason == "ramp_down:breaker"


def test_ramp_down_on_regression_from_a_live_stage():
    # A previously-promoted category (current_stage TINY_LIVE) whose evidence flips un-ready is a
    # REGRESSION -> ramp_down True (automatic; the flag ERSController hands the S4.7 ratchet),
    # stage collapses to SHADOW, promote False, reason ramp_down:regression.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=False, oos_positive=False),
                 current_stage=TINY_LIVE, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d.ramp_down is True
    assert d.stage == SHADOW
    assert d.promote_recommended is False
    assert d.reason == "ramp_down:regression"


def test_ramp_down_on_tripped_breaker_from_any_stage():
    # A tripped breaker raises ramp_down regardless of stage or readiness (even a RAMP-stage,
    # ready category). Breaker reason dominates.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=RAMP,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=True)
    assert d.ramp_down is True
    assert d.promote_recommended is False
    assert d.reason == "ramp_down:breaker"
    # ready True -> stage is NOT collapsed to SHADOW by readiness; it stays current_stage.
    # (ramp_down is the automatic-tighten signal; the stage field tracks readiness only.)
    assert d.stage == RAMP


def test_no_ramp_down_in_healthy_shadow_ready_case():
    # SHADOW + ready + no breaker -> NOT a regression: ramp_down False. (A not-ready WHILE in
    # SHADOW is also not a regression -- guarded in D1's test_not_ready_*.)
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=False)
    assert d.ramp_down is False
