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
