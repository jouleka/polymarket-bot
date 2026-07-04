"""Maker quote-policy actions (S8 / POL-10).

Decides QUOTE / WIDEN / PULL for one quoting cycle. Consumes the D1 toxicity ``pull_quotes``
seam as a plain bool plus the CALLER-computed break-even adverse move (daily_reward/order_size,
the master design's tiny number) and the locked-inventory cap. Doctrine: PULL is fail-safe under
ANY trigger (toxic flow / adverse over break-even / locked over cap), and a None or non-finite
numeric input also PULLs -- the quoting loop must never crash, and ambiguity is never a reason
to keep quoting. ``config`` is accepted per the pinned contract (reserved for future policy
knobs; unused today -- do not invent behavior for it).
"""

from decimal import Decimal

QUOTE = "QUOTE"
WIDEN = "WIDEN"
PULL = "PULL"


def _unusable(value):
    """None or a non-finite Decimal -- an input the policy must not reason over."""
    return value is None or not value.is_finite()


def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap,
                 config):
    if (_unusable(recent_adverse) or _unusable(break_even)
            or _unusable(locked_effective) or _unusable(locked_cap)):
        return PULL  # fail-safe: never crash, never quote into ambiguity
    if pull_quotes or recent_adverse > break_even or locked_effective > locked_cap:
        return PULL
    if recent_adverse > Decimal(0):
        return WIDEN
    return QUOTE
