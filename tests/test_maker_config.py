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
