"""S9 / POL-11 — dispute-freeze stress + tail-survival (DECISIONS-S0 §4 reserve-floor invariant)."""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig


def _pos(*, wcr, source, token):
    # only worst_case_risk / resolution_source / token_id are load-bearing for the stress test;
    # the other OpenPosition fields take their defaults.
    return OpenPosition(condition_id="c", event_id="e", resolution_source=source,
                        cluster_id="k", worst_case_risk=Decimal(wcr), token_id=token)


def _stress(portfolio, **kw):
    from polybot.harness.stress import dispute_freeze_stress
    return dispute_freeze_stress(portfolio, caps=RiskCaps(), **kw)


def test_reserve_floor_holds_under_100pct_adverse_freeze_survives(tmp_path):
    # ONE resolution_source, cluster worst_case_risk = 30, adverse_fraction default 1.
    #   worst_case_markdown = 1 * 30 = 30 ; non_frozen_encumbered = 0.
    #   reserve_after = nav(300) - 0 - 30 = 270 >= reserve_floor(240) -> survives True.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="10", source="uma", token="t0"),
        _pos(wcr="20", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("30")
    assert res.reserve_after == Decimal("270")
    assert res.reserve_floor == Decimal("240")
    assert res.survives is True


def test_reserve_floor_breach_does_not_survive(tmp_path):
    # frozen cluster (srcA) wcr = 45 ; non-frozen (srcB) wcr = 20.
    #   markdown = 45 ; reserve_after = 300 - 20 - 45 = 235 < 240 -> survives False.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="45", source="srcA", token="ta"),
        _pos(wcr="20", source="srcB", token="tb"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("45")
    assert res.reserve_after == Decimal("235")
    assert res.survives is False


def test_boundary_at_the_60_at_risk_ceiling_survives_inclusive(tmp_path):
    # THE $60 ceiling: one source, total at-risk = 60, adverse_fraction 1 (all frozen, no non-frozen).
    #   markdown = 60 ; reserve_after = 300 - 0 - 60 = 240 == reserve_floor -> survives (>= inclusive).
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="36", source="uma", token="t0"),
        _pos(wcr="24", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("60")
    assert res.reserve_after == Decimal("240")
    assert res.reserve_after == res.reserve_floor
    assert res.survives is True   # inclusive >=


def test_empty_portfolio_survives_with_reserve_after_equal_nav(tmp_path):
    # no positions -> markdown 0, non_frozen 0 -> reserve_after = nav = 300 >= 240 -> survives True.
    port = Portfolio(nav=Decimal("300"), positions=())
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("0")
    assert res.reserve_after == Decimal("300")
    assert res.survives is True


def test_largest_cluster_is_frozen_selection_flips_survival(tmp_path):
    # adverse_fraction=0.5 makes WHICH cluster is frozen load-bearing.
    #   srcBIG wcr=56 (one position), srcSMALL wcr=9 (one position).
    #   CORRECT (freeze srcBIG): markdown = 0.5*56 = 28 ; non_frozen = 9 ;
    #     reserve_after = 300 - 9 - 28 = 263 >= 240 -> survives True.
    #   A mutation freezing srcSMALL instead: markdown = 0.5*9 = 4.5 ; non_frozen = 56 ;
    #     reserve_after = 300 - 56 - 4.5 = 239.5 < 240 -> would be survives False.
    #   Asserting survives True + markdown 28 kills the wrong-cluster mutation.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="9", source="srcSMALL", token="tsmall"),   # smaller listed FIRST to defeat
        _pos(wcr="56", source="srcBIG", token="tbig"),      #   an "always freeze the first source" bug
    ))
    res = _stress(port, adverse_fraction=Decimal("0.5"))
    assert res.worst_case_markdown == Decimal("28.0")
    assert res.reserve_after == Decimal("263.0")
    assert res.survives is True


def test_non_finite_worst_case_risk_fails_closed(tmp_path):
    # a NaN worst_case_risk (corrupt/mis-marked position) -> survives False (never a phantom survival).
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="10", source="uma", token="t0"),
        _pos(wcr="NaN", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.survives is False


def _ramp(**over):
    base = dict(min_resolved_disputed=2, min_stress_episodes=3)
    base.update(over)
    return RampConfig(**base)


def _tail(n_disputed, episodes, ramp):
    from polybot.harness.stress import tail_survived
    return tail_survived(n_resolved_disputed=n_disputed, stress_episodes=episodes, ramp_config=ramp)


def test_tail_survived_requires_both_minimums_inclusive(tmp_path):
    ramp = _ramp(min_resolved_disputed=2, min_stress_episodes=3)
    # AT both minimums (inclusive >=) -> True.
    assert _tail(2, 3, ramp) is True
    # disputed BELOW min (1 < 2) -> False even with episodes clearing.
    assert _tail(1, 3, ramp) is False
    # episodes BELOW min (2 < 3) -> False even with disputes clearing.
    assert _tail(2, 2, ramp) is False
    # ABOVE both minimums -> True.
    assert _tail(5, 9, ramp) is True


def test_tail_survived_below_either_minimum_alone_fails(tmp_path):
    ramp = _ramp(min_resolved_disputed=1, min_stress_episodes=1)
    assert _tail(0, 5, ramp) is False   # zero disputes -> you dodged, did not survive
    assert _tail(5, 0, ramp) is False   # zero stress episodes
    assert _tail(1, 1, ramp) is True    # exactly one of each clears the default-shaped gate
