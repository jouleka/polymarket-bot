"""S8 / POL-10 — MakerConfig + FeeCategory + DEFAULT_FEE_SCHEDULE (self-verifying maker knobs)."""

import dataclasses
from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, FeeCategory, MakerConfig


def _config(**overrides):
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, **overrides)


def test_default_fee_schedule_is_the_documented_envelope():
    # Design Fork 3: sports ACTIVE (0.03, exp 1); the seven planned categories INACTIVE
    # (same conservative shape, fee 0 until Polymarket activates them); geopolitics FREE
    # by flag. All of it is a re-pullable deploy seam, never a trusted constant.
    names = [c.name for c in DEFAULT_FEE_SCHEDULE]
    assert names == [
        "sports", "politics", "finance", "tech", "econ",
        "culture", "weather", "crypto", "geopolitics",
    ]
    by_name = {c.name: c for c in DEFAULT_FEE_SCHEDULE}
    sports = by_name["sports"]
    assert sports.active is True and sports.free is False
    assert sports.fee_rate == Decimal("0.03") and sports.exponent == Decimal("1")
    geo = by_name["geopolitics"]
    assert geo.free is True  # free by FLAG — the rate field is irrelevant to its fee
    for planned in ("politics", "finance", "tech", "econ", "culture", "weather", "crypto"):
        entry = by_name[planned]
        assert entry.active is False and entry.free is False
        assert entry.fee_rate == Decimal("0.03") and entry.exponent == Decimal("1")


def test_maker_config_defaults_are_the_documented_envelope():
    c = _config()
    assert c.fee_schedule is DEFAULT_FEE_SCHEDULE
    assert c.rebate_fraction == Decimal("0.20")
    assert c.reward_b == Decimal("1")
    assert c.max_spread == Decimal("0.03")
    assert c.min_samples == 150
    assert c.net_margin_min == Decimal("0")
    assert c.lockup_rate == Decimal("0")
    assert c.forced_taker_exit_p == Decimal("0")
    assert c.dispute_p == Decimal("0")


def test_fee_schedule_is_required():
    # No default: a config without an explicit schedule is a construction error, not a guess.
    with pytest.raises(TypeError):
        MakerConfig()


def test_maker_config_is_frozen():
    c = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.rebate_fraction = Decimal("0.3")


def test_fee_category_is_frozen():
    entry = DEFAULT_FEE_SCHEDULE[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.fee_rate = Decimal("0.05")


def test_rejects_rebate_fraction_out_of_range():
    # (0, 0.5]: 0 silently disables the rebate leg; > 0.5 is beyond any documented maker share.
    with pytest.raises(ValueError, match="rebate_fraction"):
        _config(rebate_fraction=Decimal("0"))
    with pytest.raises(ValueError, match="rebate_fraction"):
        _config(rebate_fraction=Decimal("0.6"))


def test_rejects_max_spread_out_of_range():
    # (0, 1): 0 makes nothing reward-eligible; 1 makes EVERYTHING eligible (gate toothless).
    with pytest.raises(ValueError, match="max_spread"):
        _config(max_spread=Decimal("0"))
    with pytest.raises(ValueError, match="max_spread"):
        _config(max_spread=Decimal("1"))


def test_rejects_non_positive_min_samples():
    with pytest.raises(ValueError, match="min_samples"):
        _config(min_samples=0)


def test_rejects_negative_net_margin_min():
    with pytest.raises(ValueError, match="net_margin_min"):
        _config(net_margin_min=Decimal("-0.01"))


def test_rejects_negative_lockup_rate():
    with pytest.raises(ValueError, match="lockup_rate"):
        _config(lockup_rate=Decimal("-0.01"))


def test_rejects_forced_taker_exit_p_out_of_range():
    with pytest.raises(ValueError, match="forced_taker_exit_p"):
        _config(forced_taker_exit_p=Decimal("1.01"))
    with pytest.raises(ValueError, match="forced_taker_exit_p"):
        _config(forced_taker_exit_p=Decimal("-0.01"))


def test_rejects_dispute_p_out_of_range():
    with pytest.raises(ValueError, match="dispute_p"):
        _config(dispute_p=Decimal("1.01"))
    with pytest.raises(ValueError, match="dispute_p"):
        _config(dispute_p=Decimal("-0.01"))


def test_rejects_non_positive_reward_b():
    with pytest.raises(ValueError, match="reward_b"):
        _config(reward_b=Decimal("0"))
    with pytest.raises(ValueError, match="reward_b"):
        _config(reward_b=Decimal("-1"))


def test_rejects_infinity_on_the_unbounded_knobs():
    # One-sided range compares don't catch Infinity — the finiteness guard must
    # run FIRST (the fees.py guard-order discipline) and fail LOUD by field name.
    with pytest.raises(ValueError, match="net_margin_min"):
        _config(net_margin_min=Decimal("Infinity"))
    with pytest.raises(ValueError, match="lockup_rate"):
        _config(lockup_rate=Decimal("Infinity"))
    with pytest.raises(ValueError, match="reward_b"):
        _config(reward_b=Decimal("Infinity"))


def test_rejects_nan_as_a_named_valueerror_not_invalidoperation():
    # A Decimal NaN in an ordered compare raises decimal.InvalidOperation — the
    # doctrine demands the named ValueError instead (is_finite BEFORE any compare).
    with pytest.raises(ValueError, match="rebate_fraction"):
        _config(rebate_fraction=Decimal("NaN"))
    with pytest.raises(ValueError, match="net_margin_min"):
        _config(net_margin_min=Decimal("NaN"))
    with pytest.raises(ValueError, match="lockup_rate"):
        _config(lockup_rate=Decimal("NaN"))


def test_accepts_the_rebate_fraction_inclusive_ceiling():
    # (0, 0.5] — the 0.5 edge is IN the envelope; pin it so a guard tweak
    # can never silently tighten the legal boundary.
    assert _config(rebate_fraction=Decimal("0.5")).rebate_fraction == Decimal("0.5")


def test_accepts_the_probability_knobs_at_both_inclusive_edges():
    # [0, 1] — both edges legal for the probability knobs.
    assert _config(forced_taker_exit_p=Decimal("0")).forced_taker_exit_p == Decimal("0")
    assert _config(forced_taker_exit_p=Decimal("1")).forced_taker_exit_p == Decimal("1")
    assert _config(dispute_p=Decimal("0")).dispute_p == Decimal("0")
    assert _config(dispute_p=Decimal("1")).dispute_p == Decimal("1")


def test_accepts_the_zero_floors_explicitly():
    # >= 0 knobs at exactly 0 (the defaults, but pinned as an explicit construction).
    assert _config(net_margin_min=Decimal("0")).net_margin_min == Decimal("0")
    assert _config(lockup_rate=Decimal("0")).lockup_rate == Decimal("0")


def _cat(name="sports", fee_rate="0.03", exponent="1", active=True, free=False):
    return FeeCategory(
        name=name, fee_rate=Decimal(fee_rate), exponent=Decimal(exponent),
        active=active, free=free,
    )


def test_rejects_an_empty_fee_schedule():
    with pytest.raises(ValueError, match="fee_schedule"):
        MakerConfig(fee_schedule=())


def test_rejects_a_non_tuple_fee_schedule():
    # a mutable schedule invites in-place edits behind the frozen config's back.
    with pytest.raises(ValueError, match="fee_schedule"):
        MakerConfig(fee_schedule=[_cat()])


def test_rejects_a_non_feecategory_entry():
    with pytest.raises(ValueError, match="FeeCategory"):
        MakerConfig(fee_schedule=(_cat(), "sports"))


def test_rejects_duplicate_category_names():
    # two entries for one name = ambiguous lookup -> which fee applies is undefined.
    with pytest.raises(ValueError, match="unique"):
        MakerConfig(fee_schedule=(_cat(name="sports"), _cat(name="sports", free=True)))


def test_rejects_an_empty_category_name():
    with pytest.raises(ValueError, match="name"):
        MakerConfig(fee_schedule=(_cat(name=""),))


def test_rejects_a_negative_fee_rate_entry():
    with pytest.raises(ValueError, match="fee_rate"):
        MakerConfig(fee_schedule=(_cat(fee_rate="-0.01"),))


def test_rejects_a_non_finite_fee_rate_entry():
    with pytest.raises(ValueError, match="fee_rate"):
        MakerConfig(fee_schedule=(_cat(fee_rate="NaN"),))


def test_rejects_a_negative_exponent_entry():
    with pytest.raises(ValueError, match="exponent"):
        MakerConfig(fee_schedule=(_cat(exponent="-1"),))
