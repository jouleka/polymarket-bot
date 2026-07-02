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
