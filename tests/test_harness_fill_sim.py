"""S9 / POL-11 — simulate_fill (maker-only resting fill + reward, fail-closed)."""

from decimal import Decimal

import pytest

from polybot.ingestion.orderbook import LocalBook
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.reward import reward_accrual
from polybot.harness.fill_sim import SimulatedFill, simulate_fill


def _book(bids, asks):
    """A fresh, non-stale LocalBook seeded from (price, size) string pairs."""
    b = LocalBook()
    b.apply_book(
        {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        }
    )
    return b


def _cfg():
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _fill(**overrides):
    """simulate_fill with a valid resting-maker BUY baseline (mid 0.50, rest 0.49)."""
    kwargs = dict(
        token_id="tok-1",
        condition_id="cond-1",
        category="politics",
        side="BUY",
        shares=Decimal("10"),
        resting_price=Decimal("0.49"),
        book=_book([("0.48", "100")], [("0.52", "100")]),
        maker_config=_cfg(),
    )
    kwargs.update(overrides)
    return simulate_fill(**kwargs)


def test_resting_maker_buy_fills_and_accrues_the_reward():
    fill = _fill()
    assert isinstance(fill, SimulatedFill)
    assert fill.filled is True
    assert fill.fill_price == Decimal("0.49")
    assert fill.fill_mid == Decimal("0.50")
    # spread_from_mid = abs(0.49 - 0.50) = 0.01  (<= max_spread 0.03 -> eligible)
    assert fill.spread_from_mid == Decimal("0.01")
    # reward = spread_score(10, 0.01, b=1) = (10 - 0.01/10)^2 = 9.999^2 = 99.980001
    assert fill.reward_accrued == Decimal("99.980001")
    # and it agrees exactly with the S8 primitive it delegates to
    assert fill.reward_accrued == reward_accrual(Decimal("10"), Decimal("0.01"), config=_cfg())
    # passthrough fields
    assert fill.token_id == "tok-1"
    assert fill.condition_id == "cond-1"
    assert fill.category == "politics"
    assert fill.side == "BUY"
    assert fill.shares == Decimal("10")


def test_resting_maker_sell_mirror_fills_and_accrues_the_reward():
    # SELL rests at 0.51 (>= best_bid 0.48 -> does not cross) ; mid 0.50 ; spread 0.01
    fill = _fill(side="SELL", resting_price=Decimal("0.51"))
    assert fill.filled is True
    assert fill.side == "SELL"
    assert fill.fill_price == Decimal("0.51")
    assert fill.fill_mid == Decimal("0.50")
    assert fill.spread_from_mid == Decimal("0.01")
    assert fill.reward_accrued == Decimal("99.980001")


def test_filled_but_outside_max_spread_earns_no_reward():
    # Wide book (bid 0.30 / ask 0.70 -> mid 0.50). BUY resting 0.60 does NOT cross
    # the 0.70 ask (fills), but spread_from_mid = 0.10 > max_spread 0.03 -> reward 0.
    fill = _fill(
        resting_price=Decimal("0.60"),
        book=_book([("0.30", "100")], [("0.70", "100")]),
    )
    assert fill.filled is True
    assert fill.fill_mid == Decimal("0.50")
    assert fill.spread_from_mid == Decimal("0.10")
    assert fill.reward_accrued == Decimal("0")
