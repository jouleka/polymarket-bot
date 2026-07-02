"""Tests for the S4.4 / POL-6 L5 anomaly caps + pure sentinels (DESIGN-S4.4-ANOMALY §3-§5).

The 7 new RiskCaps thresholds are tighten-only, _verify-checked, content-hashed envelope
fields; ClockSkewSentinel and ApiStormSentinel are the pure, clock-injected L5 seams that
the AnomalyMonitor consults in pinned severity order. All time values here are injected
floats (monotonic seconds); no test touches a real clock.
"""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps


def test_anomaly_caps_defaults_construct_and_carry_the_design_values():
    # Kills: a missing field declaration or a wrong default constant (design §5 table).
    caps = RiskCaps()
    assert caps.midpoint_jump_halt == Decimal("0.15")
    assert caps.depth_collapse_fraction == Decimal("0.8")
    assert caps.depth_collapse_min_prev_shares == Decimal("1000")
    assert caps.ws_staleness_halt_seconds == 30
    assert caps.api_5xx_storm_count == 5
    assert caps.api_auth_storm_count == 2
    assert caps.api_storm_window_seconds == 60


def test_anomaly_caps_changes_are_content_hash_tamper_evident():
    # Kills: declaring a threshold as a plain class attribute instead of a dataclass field
    # (asdict would skip it and the signed envelope's hash would NOT change on tamper).
    base = RiskCaps().content_hash()
    assert RiskCaps(ws_staleness_halt_seconds=15).content_hash() != base
    assert RiskCaps(midpoint_jump_halt=Decimal("0.10")).content_hash() != base


def test_midpoint_jump_halt_of_zero_is_rejected_and_the_default_accepted():
    # Boundary pair, lower edge of (0, 1): x == 0 must FAIL construction; 0.15 is in-range.
    # Kills: dropping the lower bound from the midpoint_jump_halt range check.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("0"))
    RiskCaps(midpoint_jump_halt=Decimal("0.15"))  # must not raise


def test_midpoint_jump_halt_of_one_is_rejected_because_a_mid_is_a_probability():
    # Boundary pair, upper edge of (0, 1): x == 1 must FAIL (a probability mid can never
    # jump a full 1.0 -> the trigger would be vacuous). Kills: writing <= 1 instead of < 1.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("1"))


def test_depth_collapse_fraction_of_zero_is_rejected_but_one_is_accepted():
    # Boundary pair for (0, 1]: 0 rejected; 1 ("all prev depth gone") is the legal tightest
    # setting and MUST construct. Kills: writing < 1 instead of <= 1, or dropping the lower bound.
    with pytest.raises(ValueError, match="depth_collapse_fraction"):
        RiskCaps(depth_collapse_fraction=Decimal("0"))
    RiskCaps(depth_collapse_fraction=Decimal("1"))  # must not raise


def test_depth_collapse_min_prev_shares_of_zero_is_rejected():
    # (> 0): a zero noise floor would arm the collapse check on dust-depth books.
    # Kills: dropping the strictly-positive check on the noise floor.
    with pytest.raises(ValueError, match="depth_collapse_min_prev_shares"):
        RiskCaps(depth_collapse_min_prev_shares=Decimal("0"))
