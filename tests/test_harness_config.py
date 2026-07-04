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
