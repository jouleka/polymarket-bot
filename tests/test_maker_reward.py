"""S8 / POL-10 — maker reward (spread_score S(v,s) quadratic + reward_accrual eligibility gate)."""

from decimal import Decimal

import pytest

from polybot.maker.reward import spread_score


def test_spread_score_exact_hand_computed():
    # S(v=10, s=0.5) with b=2: s/v = 0.05 ; (10 - 0.05)^2 = 9.95^2 = 99.0025 ; * 2 = 198.005
    assert spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("2")) == Decimal("198.005")


def test_spread_score_zero_spread_is_depth_squared_times_b():
    # s = 0: S = v^2 * b = 100 * 3 = 300
    assert spread_score(Decimal("10"), Decimal("0"), b=Decimal("3")) == Decimal("300")


def test_spread_score_scales_linearly_in_b():
    one = spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("1"))
    assert one == Decimal("99.0025")
    assert spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("3")) == one * 3


def test_spread_score_rejects_non_positive_v():
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("0"), Decimal("0.5"), b=Decimal("1"))
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("-1"), Decimal("0.5"), b=Decimal("1"))


def test_spread_score_rejects_non_finite_v():
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("NaN"), Decimal("0.5"), b=Decimal("1"))
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("Infinity"), Decimal("0.5"), b=Decimal("1"))


def test_spread_score_rejects_negative_s():
    with pytest.raises(ValueError, match="s must"):
        spread_score(Decimal("10"), Decimal("-0.01"), b=Decimal("1"))


def test_spread_score_rejects_non_finite_s():
    with pytest.raises(ValueError, match="s must"):
        spread_score(Decimal("10"), Decimal("NaN"), b=Decimal("1"))


def test_spread_score_rejects_negative_b():
    with pytest.raises(ValueError, match="b must"):
        spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("-1"))


def test_spread_score_rejects_non_finite_b():
    with pytest.raises(ValueError, match="b must"):
        spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("NaN"))
