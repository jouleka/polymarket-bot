"""POL-15 pure resolution authority models."""

import pytest
from dataclasses import replace
from decimal import getcontext
from fractions import Fraction
import hashlib
import inspect
import json
from typing import get_type_hints

from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ProviderObservation,
    ResolutionProvider,
    ResolutionSubject,
    TerminalResolution,
    fold_dispute,
)
from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.canonical import canonical_bytes


_CONDITION = "0x" + "11" * 32
_BLOCK_HASH = "0x" + "22" * 32
_ADDRESS = "0x" + "33" * 20
_QUESTION = "0x" + "44" * 32
_TX_HASH = "0x" + "55" * 32


def test_resolution_provider_protocol_pins_public_boundary():
    assert ResolutionProvider._is_protocol is True
    assert ResolutionProvider.__annotations__ == {"provider_id": str}
    expected = {
        "chain_id": (["self"], {"return": int}),
        "latest_block": (["self"], {"return": int}),
        "block_hash": (
            ["self", "block_number"],
            {"block_number": int, "return": str},
        ),
        "observe": (
            ["self", "subject", "block_number"],
            {
                "subject": ResolutionSubject,
                "block_number": int,
                "return": ProviderObservation,
            },
        ),
        "verify_terminal": (
            ["self", "terminal"],
            {"terminal": TerminalResolution, "return": type(None)},
        ),
    }
    for name, (parameters, hints) in expected.items():
        method = getattr(ResolutionProvider, name)
        assert list(inspect.signature(method).parameters) == parameters
        assert get_type_hints(method) == hints


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
        terminal | {"audit_event_ids": (
            f"9:1:{_BLOCK_HASH}:CONDITION_PREPARATION",
            f"9:1:{_TX_HASH}:QUESTION_RESOLVED",
        )},
        unresolved.__dict__ | {"payout": PayoutVector((1, 0), 1)},
        unresolved.__dict__ | {"dispute": DisputeState.CLEAR},
    )
    for values in invalid:
        with pytest.raises((TypeError, ValueError)):
            ProviderObservation(**values)


def test_path_precedence_is_manual_disputed_unknown_clear():
    assert fold_dispute((DisputeState.CLEAR,)) is DisputeState.CLEAR
    assert fold_dispute((DisputeState.CLEAR, DisputeState.UNKNOWN)) is DisputeState.UNKNOWN
    assert fold_dispute((DisputeState.UNKNOWN, DisputeState.DISPUTED)) is DisputeState.DISPUTED
    assert fold_dispute((DisputeState.DISPUTED, DisputeState.MANUAL)) is DisputeState.MANUAL
    with pytest.raises(ValueError):
        fold_dispute(())
    with pytest.raises(TypeError):
        fold_dispute(("MANUAL",))


def test_terminal_requires_two_distinct_matching_finalized_observations():
    subject = ResolutionSubject("event-1", _CONDITION, ("101", "202"), "politics")
    first = ProviderObservation(
        provider_id="archive-b", block_number=10, block_hash=_BLOCK_HASH,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR,
        collateral_address="0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        derived_token_ids=subject.token_ids, adapter_address=_ADDRESS,
        question_id=_QUESTION,
        audit_event_ids=(f"9:1:{_TX_HASH}:CONDITION_PREPARATION",),
    )
    second = replace(first, provider_id="archive-a")
    terminal = TerminalResolution.from_observations(subject, first, second)
    assert terminal.provider_ids == ("archive-a", "archive-b")
    assert terminal.payout == PayoutVector((3, 1), 4)

    bad_pairs = (
        (first, replace(first, provider_id="archive-b")),
        (first, replace(second, block_number=11)),
        (first, replace(second, payout=PayoutVector((1, 3), 4))),
        (first, replace(second, dispute=DisputeState.UNKNOWN)),
        (first, replace(second, derived_token_ids=("202", "101"))),
    )
    for left, right in bad_pairs:
        with pytest.raises(ResolutionUnavailable):
            TerminalResolution.from_observations(subject, left, right)
    with pytest.raises(ResolutionUnavailable):
        TerminalResolution.from_observations(
            subject, replace(first, dispute=DisputeState.UNKNOWN),
            replace(second, dispute=DisputeState.UNKNOWN),
        )


def test_terminal_direct_construction_enforces_public_authority_contract():
    terminal = TerminalResolution(
        subject=ResolutionSubject("event-1", _CONDITION, ("101", "202"), "politics"),
        payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR,
        block_number=10,
        block_hash=_BLOCK_HASH,
        adapter_address=_ADDRESS,
        question_id=_QUESTION,
        audit_event_ids=(f"9:1:{_TX_HASH}:CONDITION_RESOLUTION",),
        provider_ids=("archive-b", "archive-a"),
    )
    assert terminal.provider_ids == ("archive-b", "archive-a")

    for change in (
        {"subject": object()},
        {"payout": object()},
        {"dispute": DisputeState.UNKNOWN},
        {"block_number": True},
        {"block_hash": "0x22"},
        {"adapter_address": "0x33"},
        {"question_id": "0x44"},
        {"audit_event_ids": ()},
        {"audit_event_ids": ("not-a-canonical-audit-event",)},
        {"audit_event_ids": (
            f"9:1:{_BLOCK_HASH}:CONDITION_RESOLUTION",
            f"9:1:{_TX_HASH}:QUESTION_RESOLVED",
        )},
        {"audit_event_ids": (
            f"10:1:{_BLOCK_HASH}:QUESTION_RESOLVED",
            f"9:1:{_TX_HASH}:CONDITION_RESOLUTION",
        )},
        {"provider_ids": ("archive-a", "archive-a")},
        {"provider_ids": ("archive-a",)},
        {"provider_ids": ("archive-a", " ")},
        {"provider_ids": ("archive-a", 7)},
    ):
        with pytest.raises((TypeError, ValueError)):
            replace(terminal, **change)


def test_terminal_v1_canonical_bytes_and_hash():
    terminal = TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + "11" * 32, ("101", "202"), "política"),
        payout=PayoutVector((3, 1), 4), dispute=DisputeState.CLEAR,
        block_number=100, block_hash="0x" + "22" * 32,
        adapter_address="0x157ce2d672854c848c9b79c49a8cc6cc89176a49",
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "90:1:" + "0x" + "33" * 32 + ":CONDITION_PREPARATION",
            "99:2:" + "0x" + "44" * 32 + ":CONDITION_RESOLUTION",
            "99:3:" + "0x" + "44" * 32 + ":QUESTION_RESOLVED",
        ),
        provider_ids=("archive-b", "archive-a"),
    )
    expected_primitive = {
        "acceptance": {"block_hash": "0x" + "22" * 32, "block_number": 100},
        "authority": {
            "adapter_address": "0x157ce2d672854c848c9b79c49a8cc6cc89176a49",
            "audit_event_ids": list(terminal.audit_event_ids),
            "chain_id": 137,
            "collateral_address": "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
            "ctf_address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
            "question_id": "0x" + "66" * 32,
        },
        "path": "CLEAR",
        "payout": {"denominator": 4, "numerators": [3, 1]},
        "providers": ["archive-a", "archive-b"],
        "subject": {
            "category": "política", "condition_id": "0x" + "11" * 32,
            "event_id": "event-1", "token_ids": ["101", "202"],
        },
        "version": 1,
    }
    expected = json.dumps(expected_primitive, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert len(expected) == 997
    assert hashlib.sha256(expected).hexdigest() == (
        "499af1bbfcdd6989ffbcf31a2d8898b78a1b573e1db20619c635285310f3759b")
    assert terminal.payload == expected_primitive
    assert terminal.canonical_bytes == expected
    assert terminal.terminal_id == hashlib.sha256(expected).hexdigest()
    assert canonical_bytes(dict(reversed(list(expected_primitive.items())))) == expected
    with pytest.raises(TypeError):
        canonical_bytes({"value": 0.5})
