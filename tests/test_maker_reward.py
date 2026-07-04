"""S8 / POL-10 — maker reward (spread_score S(v,s) quadratic + reward_accrual eligibility gate)."""

from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.reward import reward_accrual, spread_score


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


def _config(**over):
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, **over)


def test_reward_accrual_zero_strictly_outside_max_spread():
    # default max_spread = 0.03; resting strictly wider earns NOTHING
    assert reward_accrual(Decimal("10"), Decimal("0.031"), config=_config()) == Decimal(0)


def test_reward_accrual_eligible_at_max_spread_boundary():
    # AT max_spread is still eligible (the gate is strictly >):
    # s/v = 0.03/10 = 0.003 ; (10 - 0.003)^2 = 9.997^2 = 99.940009 ; * b=1
    assert reward_accrual(Decimal("10"), Decimal("0.03"), config=_config()) == Decimal("99.940009")


def test_reward_accrual_inside_equals_spread_score():
    cfg = _config()
    got = reward_accrual(Decimal("10"), Decimal("0.02"), config=cfg)
    # s/v = 0.002 ; (10 - 0.002)^2 = 9.998^2 = 99.960004 ; * b=1
    assert got == Decimal("99.960004")
    assert got == spread_score(Decimal("10"), Decimal("0.02"), b=cfg.reward_b)


def test_reward_accrual_uses_config_reward_b():
    cfg = _config(reward_b=Decimal("2"))
    # 99.960004 * 2 = 199.920008
    assert reward_accrual(Decimal("10"), Decimal("0.02"), config=cfg) == Decimal("199.920008")


def test_reward_accrual_rejects_non_positive_eligible_size():
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("0"), Decimal("0.02"), config=_config())
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("-1"), Decimal("0.02"), config=_config())


def test_reward_accrual_rejects_non_finite_eligible_size():
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("NaN"), Decimal("0.02"), config=_config())


def test_reward_accrual_rejects_negative_spread():
    with pytest.raises(ValueError, match="spread_from_mid"):
        reward_accrual(Decimal("10"), Decimal("-0.01"), config=_config())


def test_reward_accrual_rejects_non_finite_spread():
    with pytest.raises(ValueError, match="spread_from_mid"):
        reward_accrual(Decimal("10"), Decimal("Infinity"), config=_config())
