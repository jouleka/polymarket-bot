"""Maker quote-policy actions (S8 / POL-10).

Decides QUOTE / WIDEN / PULL for one quoting cycle. Consumes the D1 toxicity ``pull_quotes``
seam as a plain bool plus the CALLER-computed break-even adverse move (daily_reward/order_size,
the master design's tiny number) and the locked-inventory cap. Doctrine: the default under any
ambiguity is the SAFE action -- never "keep quoting". ``config`` is accepted per the pinned
contract (reserved for future policy knobs; unused today -- do not invent behavior for it).
"""

from decimal import Decimal

QUOTE = "QUOTE"
WIDEN = "WIDEN"
PULL = "PULL"


def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap,
                 config):
    if recent_adverse > Decimal(0):
        return WIDEN
    return QUOTE
