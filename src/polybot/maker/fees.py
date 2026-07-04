"""Taker-fee model + maker rebate (S8 / POL-10), pure exact-Decimal.

A pure maker pays 0 fees; this model prices the FORCED-taker-exit hurdle (DECISIONS-S0)
and the rebate leg of the net identity. Dossier-corrected per-category shape:
fee = size * fee_rate * p * (1 - p)**exponent, with planned-INACTIVE and FREE categories
paying 0 and an UNKNOWN category failing LOUD — a config gap must never silently price
as free. The schedule numbers are a re-pullable deploy seam (see maker/config.py).
"""

from decimal import Decimal


def taker_fee(category, p, size, *, schedule) -> Decimal:
    if not p.is_finite() or not (Decimal(0) <= p <= Decimal(1)):
        raise ValueError(f"p must be a finite Decimal in [0, 1], got {p}")
    if not size.is_finite() or size < 0:
        raise ValueError(f"size must be a finite Decimal >= 0, got {size}")
    entry = None
    for candidate in schedule:
        if candidate.name == category:
            entry = candidate
            break
    if entry is None:
        raise ValueError(f"unknown fee category {category!r} -- config gap, refusing to price as free")
    if entry.free or not entry.active:
        return Decimal(0)
    return size * entry.fee_rate * p * (Decimal(1) - p) ** entry.exponent


def rebate(taker_fee_paid, *, fraction) -> Decimal:
    if not taker_fee_paid.is_finite() or taker_fee_paid < 0:
        raise ValueError(f"taker_fee_paid must be a finite Decimal >= 0, got {taker_fee_paid}")
    return fraction * taker_fee_paid
