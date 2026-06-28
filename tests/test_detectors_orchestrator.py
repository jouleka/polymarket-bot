"""S6 / POL-8 — DetectorOrchestrator: composes the S7 pure detectors into one defensive verdict.

Safety properties under test:
  * zero/placeholder inputs (the S6 state; live D1-D6 wiring is POL-9-deferred) -> FLAG_ONLY, never AVOID;
  * a CRITICAL composite OR an INSIDER_LIKE classification -> AVOID with reason "detector_avoid";
  * toxicity()'s ValueError-on-negative-size is CAUGHT (not propagated) and yields a safe verdict;
  * FOLLOW stays off: action is never FOLLOW across the input space;
  * p_flow (the smart-money confirmation signal) is surfaced as a Decimal.
"""

from decimal import Decimal

from polybot.detectors.config import DetectorConfig
from polybot.detectors.orchestrator import (
    DetectorInputs,
    DetectorOrchestrator,
    DetectorVerdict,
    REASON_DETECTOR_AVOID,
)
from polybot.detectors.policy import AVOID, FLAG_ONLY, FOLLOW

CFG = DetectorConfig()


def _orch():
    return DetectorOrchestrator(CFG)


class _Intent:
    """Minimal stand-in for a PendingIntent; the orchestrator reads nothing off it at S6."""
    token_id = "t1"
    condition_id = "0xabc"
    event_id = "e1"


def test_zero_inputs_yield_flag_only_never_avoid():
    v = _orch().evaluate(_Intent(), inputs=DetectorInputs())
    assert isinstance(v, DetectorVerdict)
    assert v.action == FLAG_ONLY
    assert v.action != AVOID
    assert v.pull_quotes is False
    assert v.p_flow == Decimal(0)


def test_critical_composite_avoids_with_detector_reason():
    # D2 = 0.95 >= critical_subscore (0.8) -> composite band escalates to >= HIGH -> policy AVOID.
    inputs = DetectorInputs(d2=Decimal("0.95"))
    v = _orch().evaluate(_Intent(), inputs=inputs)
    assert v.action == AVOID
    assert REASON_DETECTOR_AVOID == "detector_avoid"
    assert "informed_flow" in v.reasons
