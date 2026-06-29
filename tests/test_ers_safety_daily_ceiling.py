"""daily_pending_ceiling ($24) halt-new wiring (S4.2 / POL-6). A PURE pending-FLOW rate gate
(new worst-case dollars proposed today), distinct from the validator's at-risk STOCK cap and
the L7 breaker -- so it never double-counts. Fail-closed: it BLOCKS when accepting would cross
the ceiling (>), allows at-or-below."""
from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.safety import would_cross_daily_pending_ceiling


def test_below_ceiling_is_allowed():
    caps = RiskCaps()  # daily_pending_ceiling == 24
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("10"), new_worst_case=Decimal("12"), caps=caps) is False  # 22 <= 24


def test_exactly_at_ceiling_is_allowed():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("12"), new_worst_case=Decimal("12"), caps=caps) is False  # 24 == 24


def test_crossing_the_ceiling_halts_new():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("18"), new_worst_case=Decimal("12"), caps=caps) is True   # 30 > 24


def test_already_over_ceiling_halts_new_even_for_a_tiny_add():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("24"), new_worst_case=Decimal("0.01"), caps=caps) is True
