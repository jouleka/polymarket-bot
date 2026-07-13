"""POL-15 pure resolution authority models."""

import pytest
from decimal import getcontext
from fractions import Fraction

from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
)


_CONDITION = "0x" + "11" * 32
_BLOCK_HASH = "0x" + "22" * 32
_ADDRESS = "0x" + "33" * 20
_QUESTION = "0x" + "44" * 32
_TX_HASH = "0x" + "55" * 32


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


def test_provider_observation_separates_phase_from_payout_and_path():
    unresolved = ProviderObservation(
        provider_id="archive-a", block_number=10, block_hash=_BLOCK_HASH,
        phase=LifecyclePhase.UNRESOLVED, payout=None, dispute=DisputeState.UNKNOWN,
        collateral_address=None, derived_token_ids=None, adapter_address=None,
        question_id=None, audit_event_ids=(),
    )
    assert unresolved.payout is None

    terminal = dict(
        provider_id="archive-a", block_number=10, block_hash=_BLOCK_HASH,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR,
        collateral_address="0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        derived_token_ids=("101", "202"), adapter_address=_ADDRESS,
        question_id=_QUESTION,
        audit_event_ids=(f"9:1:{_TX_HASH}:CONDITION_PREPARATION",),
    )
    assert ProviderObservation(**terminal).phase is LifecyclePhase.FINALIZED

    invalid = (
        terminal | {"payout": None},
        terminal | {"block_number": True},
        terminal | {"block_hash": "0x22"},
        terminal | {"derived_token_ids": None},
        terminal | {"adapter_address": None},
        terminal | {"audit_event_ids": ()},
        unresolved.__dict__ | {"payout": PayoutVector((1, 0), 1)},
        unresolved.__dict__ | {"dispute": DisputeState.CLEAR},
    )
    for values in invalid:
        with pytest.raises((TypeError, ValueError)):
            ProviderObservation(**values)
