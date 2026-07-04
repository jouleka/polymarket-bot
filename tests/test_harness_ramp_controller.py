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
