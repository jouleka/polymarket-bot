"""Taker-fee model + maker rebate (S8 / POL-10), pure exact-Decimal.

A pure maker pays 0 fees; this model prices the FORCED-taker-exit hurdle (DECISIONS-S0)
and the rebate leg of the net identity. Dossier-corrected per-category shape:
fee = size * fee_rate * p * (1 - p)**exponent, with planned-INACTIVE and FREE categories
paying 0 and an UNKNOWN category failing LOUD — a config gap must never silently price
as free. The schedule numbers are a re-pullable deploy seam (see maker/config.py).
"""

from decimal import Decimal


def taker_fee(category, p, size, *, schedule) -> Decimal:
    entry = None
    for candidate in schedule:
        if candidate.name == category:
            entry = candidate
            break
    # Unknown-category / free / inactive dispositions pinned RED-first by the A5 cycle;
    # p/size input guards by A6.
    return size * entry.fee_rate * p * (Decimal(1) - p) ** entry.exponent
