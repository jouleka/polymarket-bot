"""S9 / POL-11 — RampConfig (self-verifying earn-autonomy thresholds)."""

from decimal import Decimal

import pytest

from polybot.harness.config import RampConfig


def _cfg(**overrides):
    """Construct a RampConfig, overriding individual knobs (defaults are all valid)."""
    return RampConfig(**overrides)


def test_defaults_are_valid_and_match_the_pinned_contract():
    c = _cfg()
    assert c.min_resolved == 150
    assert c.net_margin_min == Decimal("0")
    assert c.oos_holdout_fraction == Decimal("0.30")
    assert c.min_oos_resolved == 30
    assert c.mc_penalty == Decimal("0")
    assert c.oos_n_bins == 10
    assert c.reliability_max == Decimal("0.03")
    assert c.min_resolved_disputed == 1
    assert c.min_stress_episodes == 1
    assert c.ramp_step_fraction == Decimal("0.5")


def test_config_is_frozen():
    c = _cfg()
    with pytest.raises(Exception):  # FrozenInstanceError (a dataclasses subclass); construction-time immutability
        c.min_resolved = 200


def test_min_resolved_must_be_positive():
    with pytest.raises(ValueError, match="min_resolved"):
        _cfg(min_resolved=0)


def test_min_oos_resolved_must_be_positive():
    with pytest.raises(ValueError, match="min_oos_resolved"):
        _cfg(min_oos_resolved=0)


def test_oos_n_bins_must_be_positive():
    with pytest.raises(ValueError, match="oos_n_bins"):
        _cfg(oos_n_bins=0)


def test_min_resolved_disputed_must_be_non_negative():
    with pytest.raises(ValueError, match="min_resolved_disputed"):
        _cfg(min_resolved_disputed=-1)


def test_min_stress_episodes_must_be_non_negative():
    with pytest.raises(ValueError, match="min_stress_episodes"):
        _cfg(min_stress_episodes=-1)


def test_net_margin_min_must_be_non_negative():
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("-0.01"))


def test_net_margin_min_rejects_infinity():
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("Infinity"))


def test_net_margin_min_rejects_nan_by_name_not_invalidoperation():
    # is_finite() BEFORE the compare: a NaN must raise the NAMED ValueError,
    # never a bare InvalidOperation from an ordered compare on NaN.
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("NaN"))


def test_oos_holdout_fraction_rejects_zero():
    with pytest.raises(ValueError, match="oos_holdout_fraction"):
        _cfg(oos_holdout_fraction=Decimal("0"))


def test_oos_holdout_fraction_rejects_one():
    with pytest.raises(ValueError, match="oos_holdout_fraction"):
        _cfg(oos_holdout_fraction=Decimal("1"))


def test_mc_penalty_must_be_non_negative():
    with pytest.raises(ValueError, match="mc_penalty"):
        _cfg(mc_penalty=Decimal("-0.01"))


def test_mc_penalty_rejects_infinity():
    with pytest.raises(ValueError, match="mc_penalty"):
        _cfg(mc_penalty=Decimal("Infinity"))


def test_reliability_max_rejects_zero():
    with pytest.raises(ValueError, match="reliability_max"):
        _cfg(reliability_max=Decimal("0"))


def test_reliability_max_rejects_above_ceiling():
    with pytest.raises(ValueError, match="reliability_max"):
        _cfg(reliability_max=Decimal("0.11"))


def test_ramp_step_fraction_rejects_zero():
    with pytest.raises(ValueError, match="ramp_step_fraction"):
        _cfg(ramp_step_fraction=Decimal("0"))


def test_ramp_step_fraction_rejects_above_one():
    with pytest.raises(ValueError, match="ramp_step_fraction"):
        _cfg(ramp_step_fraction=Decimal("1.5"))


_DECIMAL_KNOBS = (
    "net_margin_min",
    "oos_holdout_fraction",
    "mc_penalty",
    "reliability_max",
    "ramp_step_fraction",
)


@pytest.mark.parametrize("field_name", _DECIMAL_KNOBS)
@pytest.mark.parametrize("bad_value", [Decimal("NaN"), Decimal("Infinity")])
def test_every_decimal_knob_rejects_non_finite_by_name_not_invalidoperation(field_name, bad_value):
    # is_finite() BEFORE every compare: a NaN or Infinity on ANY of the five Decimal
    # knobs must raise that knob's own NAMED ValueError -- never a bare
    # decimal.InvalidOperation leaking from an ordered compare on NaN.
    with pytest.raises(ValueError, match=field_name):
        _cfg(**{field_name: bad_value})
