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


from polybot.detectors.classify import INSIDER_LIKE


def test_insider_like_classification_avoids_even_at_low_band():
    # All sub-scores zero -> LOW band, but INSIDER_LIKE classification forces AVOID.
    inputs = DetectorInputs(classification=INSIDER_LIKE)
    v = _orch().evaluate(_Intent(), inputs=inputs)
    assert v.action == AVOID
    assert "insider_like" in v.reasons


def test_negative_size_is_caught_and_yields_a_safe_verdict():
    # A negative buy_size makes toxicity() raise ValueError; the orchestrator must swallow it and
    # degrade to a safe verdict rather than letting the exception wedge the per-intent guard.
    inputs = DetectorInputs(buy_size=Decimal("-10"), sell_size=Decimal("50"),
                            baseline_mean=Decimal("0.2"), baseline_std=Decimal("0.1"))
    v = _orch().evaluate(_Intent(), inputs=inputs)   # must NOT raise
    assert v.action == FLAG_ONLY
    assert v.pull_quotes is False
    assert v.p_flow == Decimal(0)


from polybot.detectors.classify import LUCKY, MARKET_MAKER, NOISE, SHARP


def test_follow_is_never_emitted_across_the_input_space():
    orch = _orch()
    for cls in (SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE):
        for d in ("0", "0.5", "0.95"):
            inputs = DetectorInputs(classification=cls, d2=Decimal(d), d6=Decimal(d))
            v = orch.evaluate(_Intent(), inputs=inputs)
            assert v.action in (AVOID, FLAG_ONLY), (cls, d)
            assert v.action != FOLLOW, (cls, d)


def test_p_flow_surfaces_d6_smart_money_as_decimal():
    v = _orch().evaluate(_Intent(), inputs=DetectorInputs(d6=Decimal("0.6")))
    # d6_smart_money(edge_weight=0.6, conviction=1.0) == 0.6, surfaced as a Decimal.
    assert isinstance(v.p_flow, Decimal)
    assert v.p_flow == Decimal("0.6")
    # And zero d6 -> zero p_flow.
    z = _orch().evaluate(_Intent(), inputs=DetectorInputs(d6=Decimal(0)))
    assert z.p_flow == Decimal(0)
