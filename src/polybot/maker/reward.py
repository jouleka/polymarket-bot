"""Maker reward accrual -- the S(v,s) quadratic (S8 / POL-10).

Models the documented per-market reward score S(v,s) = (v - s/v)^2 * b (v = resting depth,
s = spread-from-mid, b = pool constant), exact Decimal. Resting STRICTLY wider than
config.max_spread earns NOTHING (the eligibility gate); AT the boundary still earns. All
guards fail LOUD -- a bad input here is a caller bug, not market data. The exact Polymarket
pool->score mapping and the real b are deploy calibration (design §6); config-parameterized.
"""

from decimal import Decimal


def spread_score(v, s, *, b):
    """The documented S(v, s) = (v - s/v)^2 * b, exact Decimal."""
    if not v.is_finite() or v <= 0:
        raise ValueError(f"v must be finite > 0, got {v}")
    if not s.is_finite() or s < 0:
        raise ValueError(f"s must be finite >= 0, got {s}")
    if not b.is_finite() or b < 0:
        raise ValueError(f"b must be finite >= 0, got {b}")
    delta = v - s / v
    return delta * delta * b
