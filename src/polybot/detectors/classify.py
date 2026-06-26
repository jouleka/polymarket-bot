"""Wallet classification (S7 / POL-9).

Map a wallet to one of {SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE}. MARKET_MAKERs are
checked FIRST and excluded from copy -- their edge is uncopyable rebates, and a high two-sided
volume is the tell. A wallet that passes the luck filter is SHARP, or INSIDER_LIKE when its
insider composite is High/Critical (suspicious timing/concentration). A wallet that fails the
luck filter is LUCKY (positive raw edge but not robust) or NOISE. Pure.
"""

from decimal import Decimal

SHARP = "SHARP"
LUCKY = "LUCKY"
MARKET_MAKER = "MARKET_MAKER"
INSIDER_LIKE = "INSIDER_LIKE"
NOISE = "NOISE"

_INSIDER_BANDS = {"HIGH", "CRITICAL"}


def classify(*, edge_passes, raw_mean_edge, trade_count, buy_volume, sell_volume,
             insider_band, config):
    high = max(buy_volume, sell_volume)
    balance = (min(buy_volume, sell_volume) / high) if high > 0 else Decimal(0)
    if trade_count >= config.mm_min_trades and balance >= config.mm_balance_min:
        return MARKET_MAKER
    if edge_passes:
        return INSIDER_LIKE if insider_band in _INSIDER_BANDS else SHARP
    return LUCKY if raw_mean_edge > 0 else NOISE
