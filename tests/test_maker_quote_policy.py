"""S8 / POL-10 — quote policy (QUOTE / WIDEN / PULL over the D1 pull_quotes seam)."""

from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.quote_policy import PULL, QUOTE, WIDEN, decide_quote

_CFG = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _decide(**kw):
    """A benign baseline cycle: no toxic flow, no bleed, locked inventory well under cap."""
    base = dict(pull_quotes=False, recent_adverse=Decimal("0"), break_even=Decimal("0.05"),
                locked_effective=Decimal("0"), locked_cap=Decimal("100"), config=_CFG)
    base.update(kw)
    return decide_quote(**base)


def test_quote_when_no_bleed_and_no_triggers():
    assert _decide() == QUOTE


def test_widen_when_bleeding_under_break_even():
    # bleeding (recent_adverse > 0) but under the break-even adverse move -> widen, don't pull.
    assert _decide(recent_adverse=Decimal("0.01")) == WIDEN
