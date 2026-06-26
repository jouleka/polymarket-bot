"""S7 / POL-9 — wallet classification {SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE}."""

from decimal import Decimal

from polybot.detectors.classify import (
    INSIDER_LIKE,
    LUCKY,
    MARKET_MAKER,
    NOISE,
    SHARP,
    classify,
)
from polybot.detectors.config import DetectorConfig

CFG = DetectorConfig()  # mm_min_trades=100, mm_balance_min=0.4


def _classify(**kw):
    base = dict(edge_passes=False, raw_mean_edge=0.0, trade_count=5,
                buy_volume=Decimal("100"), sell_volume=Decimal("0"),
                insider_band="LOW", config=CFG)
    base.update(kw)
    return classify(**base)


def test_market_maker_is_excluded_first_even_when_sharp():
    # high two-sided volume -> MM (uncopyable rebate edge), regardless of edge_passes.
    assert _classify(trade_count=200, buy_volume=Decimal("100"), sell_volume=Decimal("90"),
                     edge_passes=True) == MARKET_MAKER


def test_passing_wallet_with_low_insider_band_is_sharp():
    assert _classify(edge_passes=True, insider_band="LOW", trade_count=10) == SHARP


def test_passing_wallet_with_high_insider_band_is_insider_like():
    assert _classify(edge_passes=True, insider_band="CRITICAL", trade_count=10) == INSIDER_LIKE


def test_failing_wallet_with_positive_edge_is_lucky():
    assert _classify(edge_passes=False, raw_mean_edge=0.1) == LUCKY


def test_failing_wallet_with_non_positive_edge_is_noise():
    assert _classify(edge_passes=False, raw_mean_edge=-0.05) == NOISE
