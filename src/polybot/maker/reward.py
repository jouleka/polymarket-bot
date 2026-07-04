"""Maker reward accrual -- the S(v,s) quadratic (S8 / POL-10).

Models the documented per-market reward score S(v,s) = (v - s/v)^2 * b (v = resting depth,
s = spread-from-mid, b = pool constant), Decimal throughout: exact when s/v terminates,
otherwise rounded at the ambient decimal context precision (28 significant digits by
default). Resting STRICTLY wider than
config.max_spread earns NOTHING (the eligibility gate); AT the boundary still earns. All
guards fail LOUD -- a bad input here is a caller bug, not market data. The exact Polymarket
pool->score mapping and the real b are deploy calibration (design §6); config-parameterized.
"""

from decimal import Decimal


def spread_score(v, s, *, b):
    """The documented S(v, s) = (v - s/v)^2 * b, Decimal: exact when s/v terminates,
    otherwise rounded at the ambient decimal context precision (28 digits default)."""
    if not v.is_finite() or v <= 0:
        raise ValueError(f"v must be finite > 0, got {v}")
    if not s.is_finite() or s < 0:
        raise ValueError(f"s must be finite >= 0, got {s}")
    if not b.is_finite() or b < 0:
        raise ValueError(f"b must be finite >= 0, got {b}")
    delta = v - s / v
    return delta * delta * b


def reward_accrual(eligible_size, spread_from_mid, *, config):
    """Decimal(0) if spread_from_mid > config.max_spread (resting too far from mid earns
    NOTHING); else spread_score(eligible_size, spread_from_mid, b=config.reward_b) -- exact
    when spread_from_mid/eligible_size terminates, otherwise rounded at the ambient decimal
    context precision (28 digits default). AT the boundary is eligible -- the gate is
    strictly >."""
    if not eligible_size.is_finite() or eligible_size <= 0:
        raise ValueError(f"eligible_size must be finite > 0, got {eligible_size}")
    if not spread_from_mid.is_finite() or spread_from_mid < 0:
        raise ValueError(f"spread_from_mid must be finite >= 0, got {spread_from_mid}")
    if spread_from_mid > config.max_spread:
        return Decimal(0)
    return spread_score(eligible_size, spread_from_mid, b=config.reward_b)
