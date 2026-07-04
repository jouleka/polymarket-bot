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


def test_pull_quotes_alone_pulls():
    # the D1 toxicity seam alone (no bleed, locked under cap) is a HARD pull.
    assert _decide(pull_quotes=True) == PULL


def test_adverse_over_break_even_alone_pulls():
    # 0.06 > break_even 0.05, everything else benign.
    assert _decide(recent_adverse=Decimal("0.06")) == PULL


def test_locked_over_cap_alone_pulls():
    # 101 > cap 100, everything else benign.
    assert _decide(locked_effective=Decimal("101")) == PULL


def test_adverse_exactly_at_break_even_widens_not_pulls():
    # strict >: at break-even is NOT a PULL trigger; 0.05 > 0 so it falls to the WIDEN arm.
    assert _decide(recent_adverse=Decimal("0.05"), break_even=Decimal("0.05")) == WIDEN


def test_zero_adverse_quotes_even_at_zero_break_even():
    # recent_adverse == 0 -> the QUOTE arm (0 > 0 is False on BOTH strict comparisons).
    assert _decide(recent_adverse=Decimal("0"), break_even=Decimal("0")) == QUOTE


def test_locked_exactly_at_cap_is_not_pull():
    assert _decide(locked_effective=Decimal("100"), locked_cap=Decimal("100")) == QUOTE
