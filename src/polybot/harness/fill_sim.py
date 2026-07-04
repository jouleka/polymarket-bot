"""Maker-only shadow fill simulator (S9 / POL-11).

Models ONE resting-limit maker entry against an injected book snapshot (Fork 2:
maker-primary, reuse S8). A resting price that would CROSS the book (a BUY at/above
the ask, a SELL at/below the bid), or a stale/empty/crossed book with no usable
midpoint, fails CLOSED -> filled=False, reward 0: we do NOT shadow taker fills, so an
unfillable maker order simply earns nothing (never a phantom fill). A genuine bad
proposal (bad side, non-positive/non-finite shares, a resting_price outside (0,1) or
non-finite) fails LOUD -- that is a caller bug, not market data. Reward accrues through
S8's reward_accrual; every numeric is exact Decimal, is_finite() before every compare.

NOTE (incremental TDD, S9a): this is the A5 happy-path-only cut. The fail-closed
crossing/None-mid branch (A6) and the loud bad-proposal guards (A7) land in later
cycles per docs/PLAN-S9-HARNESS.md's incremental-TDD override.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.maker.reward import reward_accrual


@dataclass(frozen=True)
class SimulatedFill:
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    fill_price: Decimal
    fill_mid: Decimal
    spread_from_mid: Decimal
    filled: bool
    reward_accrued: Decimal


def simulate_fill(*, token_id, condition_id, category, side, shares, resting_price, book, maker_config):
    """A single resting-maker fill decision. Fails CLOSED (filled=False, reward 0)
    on a crossing price or a stale/one-sided/None-mid book (A6)."""
    mid = book.midpoint()  # None when stale / empty side / crossed
    best_bid = book.best_bid()
    best_ask = book.best_ask()

    crosses = (
        mid is None
        or (side == "BUY" and (best_ask is None or resting_price >= best_ask))
        or (side == "SELL" and (best_bid is None or resting_price <= best_bid))
    )
    if crosses:
        return SimulatedFill(
            token_id=token_id,
            condition_id=condition_id,
            category=category,
            side=side,
            shares=shares,
            fill_price=resting_price,
            fill_mid=mid if mid is not None else Decimal(0),
            spread_from_mid=Decimal(0),
            filled=False,
            reward_accrued=Decimal(0),
        )

    spread_from_mid = abs(resting_price - mid)
    reward = reward_accrual(shares, spread_from_mid, config=maker_config)
    return SimulatedFill(
        token_id=token_id,
        condition_id=condition_id,
        category=category,
        side=side,
        shares=shares,
        fill_price=resting_price,
        fill_mid=mid,
        spread_from_mid=spread_from_mid,
        filled=True,
        reward_accrued=reward,
    )
