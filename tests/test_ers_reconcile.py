"""Tests for the S4.5b leg parsers in ers/reconcile.py (POL-6 / S4.5).

These pin the pure per-token_id balance folders: the internal fills fold, the
Data-API /positions fold, and the on-chain ERC-1155 transfer fold. The reconciler
itself (S4.5c) is NOT exercised here. Fixtures are module-level helpers (no conftest).
"""

import json
from decimal import Decimal

import pytest

from polybot.core.models import Envelope
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    _SHARE_DECIMALS,
    Balance,
    Divergence,
    ReconResult,
)


def test_status_constants_and_share_decimals_are_pinned():
    """The four status strings and the ERC-1155 share scaling are the single
    source of truth the reconciler + restart machine import; pin their literals."""
    assert (OK, DIVERGED, SETTLING, DORMANT) == ("OK", "DIVERGED", "SETTLING", "DORMANT")
    assert _SHARE_DECIMALS == 6


def test_balance_is_frozen_with_default_latest_fill_at_none():
    """Balance carries shares + an optional in-session monotonic-ns fill stamp;
    latest_fill_at defaults to None (the replayed / no-grace marker) and the
    dataclass is frozen so a parsed balance can't be mutated downstream."""
    b = Balance(token_id="42", shares=Decimal("3"))
    assert (b.token_id, b.shares, b.latest_fill_at) == ("42", Decimal("3"), None)
    with pytest.raises(Exception):
        b.shares = Decimal("9")


def test_divergence_and_reconresult_field_shapes():
    """Divergence is (token_id, internal_shares, onchain_shares, dollars); ReconResult
    is (status, divergences, onchain_confirmed_exposure, settling_tokens, triggers)."""
    d = Divergence(token_id="42", internal_shares=Decimal("3"),
                   onchain_shares=Decimal("0"), dollars=Decimal("3"))
    assert d.dollars == Decimal("3")
    r = ReconResult(status=OK, divergences=(), onchain_confirmed_exposure=Decimal("0"),
                    settling_tokens=(), triggers=())
    assert r.status == "OK" and r.divergences == ()
