"""S8 / POL-10 — taker_fee + rebate (the parameterized per-category fee model, pure exact-Decimal)."""

from decimal import Decimal

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, FeeCategory
from polybot.maker.fees import taker_fee


def _fee(p, size="100", category="sports", schedule=DEFAULT_FEE_SCHEDULE):
    return taker_fee(category, Decimal(p), Decimal(size), schedule=schedule)


def test_sports_fee_hand_computed_at_the_peak():
    # 100 shares * 0.03 * 0.5 * (1 - 0.5)**1 = 0.75 — the master design's
    # "$0.75 sports per 100 shares" figure, exact.
    assert _fee("0.5") == Decimal("0.75")


def test_sports_fee_hand_computed_off_peak():
    # 100 * 0.03 * 0.2 * 0.8 = 0.48
    assert _fee("0.2") == Decimal("0.48")


def test_fee_peaks_at_p_half():
    # p*(1-p) is maximal at p = 0.5 and symmetric for exponent 1:
    # both wings are 100 * 0.03 * 0.3 * 0.7 = 0.63 < 0.75.
    assert _fee("0.5") > _fee("0.3")
    assert _fee("0.5") > _fee("0.7")
    assert _fee("0.3") == _fee("0.7") == Decimal("0.63")


def test_fee_is_zero_at_the_p_boundaries():
    # a certainty-priced share generates no fee: p or (1-p) is 0.
    assert _fee("0") == Decimal("0")
    assert _fee("1") == Decimal("0")


def test_exponent_is_applied_via_decimal_power():
    quadratic = (
        FeeCategory(name="sports", fee_rate=Decimal("0.03"), exponent=Decimal("2"),
                    active=True, free=False),
    )
    # 100 * 0.03 * 0.5 * (0.5)**2 = 0.375
    assert _fee("0.5", schedule=quadratic) == Decimal("0.375")
