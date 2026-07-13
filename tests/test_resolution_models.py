"""POL-15 pure resolution authority models."""

import pytest
from decimal import getcontext
from fractions import Fraction

from polybot.resolution.models import PayoutVector, ResolutionSubject


_CONDITION = "0x" + "11" * 32


def test_resolution_subject_requires_exact_binary_identity():
    subject = ResolutionSubject(
        event_id="event-1",
        condition_id=_CONDITION,
        token_ids=("101", "202"),
        category="politics",
    )
    assert subject.token_ids == ("101", "202")

    invalid = (
        {"event_id": ""},
        {"event_id": " event-1"},
        {"condition_id": "0x" + "AA" * 32},
        {"condition_id": "0x11"},
        {"token_ids": ("101",)},
        {"token_ids": ("101", "101")},
        {"token_ids": ("01", "202")},
        {"token_ids": ("0", "202")},
        {"category": ""},
    )
    base = dict(event_id="event-1", condition_id=_CONDITION,
                token_ids=("101", "202"), category="politics")
    for change in invalid:
        with pytest.raises((TypeError, ValueError)):
            ResolutionSubject(**(base | change))


def test_payout_vector_preserves_every_valid_binary_fraction():
    assert PayoutVector((3, 1), 4).fraction_for(0) == Fraction(3, 4)
    assert PayoutVector((2, 2), 4).fraction_for(1) == Fraction(1, 2)
    assert PayoutVector((0, 9), 9).fraction_for(0) == Fraction(0, 1)

    for numerators, denominator in (
        ((0, 0), 0), ((1, 1), 3), ((-1, 2), 1),
        ((True, 0), 1), ((1,), 1), ((1, 2, 3), 6),
    ):
        with pytest.raises((TypeError, ValueError)):
            PayoutVector(numerators, denominator)
    payout = PayoutVector((1, 2), 3)
    for slot in (-1, 2, True, "0"):
        with pytest.raises((TypeError, ValueError, IndexError)):
            payout.fraction_for(slot)


def test_decimal_projection_ignores_ambient_context():
    payout = PayoutVector((1, 2), 3)
    original = getcontext().copy()
    try:
        values = []
        for precision in (5, 100):
            getcontext().prec = precision
            values.append(payout.decimal_for(0))
            assert getcontext().prec == precision
        expected = "0." + "3" * 78
        assert [str(value) for value in values] == [expected, expected]
    finally:
        getcontext().prec = original.prec
        getcontext().rounding = original.rounding
