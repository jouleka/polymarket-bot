"""Tests for the signed risk-caps envelope (S3 / POL-5).

RiskCaps carries the DECISIONS-S0 §4 numbers that REPLACE the human confirm. It
verifies its own internal consistency at construction and FAILS LOUD on an
inconsistent envelope -- the exact defects the S0 adversarial verification caught
(inverted breaker ordering, zero-slack caps, wrong reserve denominator, a
taxonomy-blind at-risk ceiling) must be impossible to construct. A content hash
gives tamper-evidence (the seed of the signed-caps startup self-test).
"""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps


def test_default_caps_are_the_s0_envelope():
    caps = RiskCaps()
    assert caps.nav == Decimal("300")
    assert caps.total_open_risk == Decimal("60")
    assert caps.per_trade == Decimal("12")
    assert caps.max_concurrent == 4
    assert caps.matrix_cold_concurrent == 3
    assert caps.per_event_union == Decimal("24")
    assert caps.reserve_floor == Decimal("240")
    assert caps.kelly_fraction == Decimal("0.25")
    assert caps.min_position_floor == Decimal("5")


def test_rejects_inverted_breaker_ordering():
    # per_trade must be < daily_pending_ceiling < total_open (the §Verification #1 bug:
    # a daily halt sitting BELOW the per-trade max loss).
    with pytest.raises(ValueError, match="ordering"):
        RiskCaps(daily_pending_ceiling=Decimal("10"))  # 12 !< 10


def test_rejects_zero_slack_concurrency():
    # max_concurrent * per_trade must leave slack under total_open (§Verification #2:
    # 6 x $25 = $150 = total-open exactly).
    with pytest.raises(ValueError, match="slack|concurrent"):
        RiskCaps(max_concurrent=6)  # 6 * 12 = 72 > 60


def test_rejects_wrong_reserve_denominator():
    # reserve_floor must equal nav - total_open (one capital band, no triple-counting).
    with pytest.raises(ValueError, match="reserve"):
        RiskCaps(reserve_floor=Decimal("200"))  # != 300 - 60


def test_rejects_at_risk_ceiling_above_20pct_nav():
    # total_open must be <= 20% NAV (§Verification #3: the 50%-NAV taxonomy-blind stroke).
    with pytest.raises(ValueError, match="20%|at-risk"):
        RiskCaps(total_open_risk=Decimal("90"), reserve_floor=Decimal("210"))  # 90 > 0.20*300


def test_rejects_kelly_fraction_out_of_range():
    with pytest.raises(ValueError, match="kelly"):
        RiskCaps(kelly_fraction=Decimal("0.75"))  # > 0.5


def test_content_hash_is_stable_and_tamper_evident():
    assert RiskCaps().content_hash() == RiskCaps().content_hash()  # deterministic
    # a different (but still consistent) envelope hashes differently
    bigger = RiskCaps(nav=Decimal("600"), total_open_risk=Decimal("120"),
                      reserve_floor=Decimal("480"), daily_pending_ceiling=Decimal("48"))
    assert bigger.content_hash() != RiskCaps().content_hash()


# --- slice-3 per-cluster co-move cap: per_trade + (1-rho)*(total_open - per_trade), clamped ---

def test_cluster_cap_full_correlation_collapses_to_per_trade():
    assert RiskCaps().cluster_cap(Decimal("1")) == Decimal("12")  # one bet


def test_cluster_cap_zero_correlation_is_total_open():
    assert RiskCaps().cluster_cap(Decimal("0")) == Decimal("60")  # global ceiling only


def test_cluster_cap_half_correlation_interpolates_linearly():
    assert RiskCaps().cluster_cap(Decimal("0.5")) == Decimal("36")  # 12 + 0.5*48


def test_cluster_cap_negative_correlation_clamped_to_total_open():
    # anti-correlated positions hedge -> no extra cluster tightening (clamp at total_open).
    assert RiskCaps().cluster_cap(Decimal("-1")) == Decimal("60")


def test_cluster_cap_above_one_clamped_to_per_trade():
    assert RiskCaps().cluster_cap(Decimal("1.5")) == Decimal("12")


# --- slice-3 L7 real-time unrealized-drawdown breaker thresholds (§4 L7) ---------------------

def test_default_caps_include_the_l7_envelope():
    caps = RiskCaps()
    assert caps.l7_freeze_floor == Decimal("18")          # freeze-adds > $18 (6% NAV)
    assert caps.l7_flatten_floor == Decimal("30")         # FLATTEN > $30 (10% NAV)
    assert caps.l7_velocity_delta == Decimal("18")        # rose > $18 ...
    assert caps.l7_velocity_window_seconds == 900         # ... within 15 min


def test_rejects_l7_freeze_floor_not_below_flatten():
    with pytest.raises(ValueError, match="L7|freeze|flatten"):
        RiskCaps(l7_freeze_floor=Decimal("30"))  # 30 !< 30 (flatten)


def test_rejects_l7_flatten_floor_above_total_open():
    with pytest.raises(ValueError, match="L7|flatten|total"):
        RiskCaps(l7_flatten_floor=Decimal("70"))  # > total_open $60


def test_rejects_non_positive_l7_velocity_delta():
    with pytest.raises(ValueError, match="velocity"):
        RiskCaps(l7_velocity_delta=Decimal("0"))
