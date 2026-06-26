"""S7 / POL-9 — D2/D3/D4/D5/D6 informed-flow sub-scores (each 0..1)."""

from decimal import Decimal

from polybot.detectors.signals import (
    clamp01,
    d2_conviction,
    d3_abnormal_move,
    d4_coordinated_entry,
    d5_lead_time,
    d6_smart_money,
)


def _close(a, b):
    return abs(a - b) < 1e-9


def test_clamp01_is_nan_safe_and_bounds():
    # review M1: NaN must fail CLOSED to 0.0, not propagate (NaN comparisons are all False).
    assert clamp01(float("nan")) == 0.0
    assert clamp01(-1.0) == 0.0
    assert clamp01(2.0) == 1.0
    assert clamp01(0.3) == 0.3


def test_d2_with_nan_recency_fails_closed():
    assert d2_conviction(Decimal("50"), Decimal("100"), Decimal("0.2"), float("nan")) == 0.0


def test_d2_conviction_is_the_product_of_its_factors():
    # (50/100) * (1 - 0.2) * 0.8 = 0.5 * 0.8 * 0.8 = 0.32
    assert _close(d2_conviction(Decimal("50"), Decimal("100"), Decimal("0.2"), 0.8), 0.32)


def test_d2_clamps_oversized_position_fraction_to_one():
    # size > wallet value -> the size fraction clamps at 1.0
    assert _close(d2_conviction(Decimal("200"), Decimal("100"), Decimal("0.5"), 1.0), 0.5)


def test_d3_abnormal_move_is_cancelled_by_a_known_catalyst():
    assert _close(d3_abnormal_move(0.7, catalyst_present=False), 0.7)
    assert d3_abnormal_move(0.7, catalyst_present=True) == 0.0


def test_d4_coordinated_entry_is_the_cluster_share():
    assert _close(d4_coordinated_entry(6, 10), 0.6)
    assert d4_coordinated_entry(0, 0) == 0.0


def test_d5_lead_time_scores_trading_before_the_news():
    assert _close(d5_lead_time(trade_ts=100, public_ts=300, horizon=400), 0.5)  # 200s lead
    assert d5_lead_time(trade_ts=300, public_ts=100, horizon=400) == 0.0        # traded after


def test_d6_smart_money_gated_by_the_edge_weight():
    assert _close(d6_smart_money(edge_weight=1, conviction=0.6), 0.6)
    assert d6_smart_money(edge_weight=0, conviction=0.9) == 0.0
