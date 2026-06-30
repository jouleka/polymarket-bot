"""S4.5c / POL-6 -- the pure ThreeWayReconciler verdict over three per-token balance maps.

On-chain (ERC-1155) is ground truth; the reconciler trades only when the ERS's own belief
matches that truth within the signed reconcile_tolerance, and FAILS CLOSED (DIVERGED) on any
divergence, orphan, or replayed-unconfirmed state. wallet=None / onchain=None -> DORMANT
(shadow-clean). CLOB is advisory: a CLOB-only mismatch never drives the verdict. The
settle-window (caps.reconcile_settle_window_seconds, keyed on the INTERNAL fill stamp in the
SAME monotonic-ns domain as `now`) exempts a just-placed not-yet-confirmed fill as SETTLING.
"""

from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    Balance,
    Divergence,
    ReconResult,
    ThreeWayReconciler,
)

# reconcile_tolerance defaults to $0.50; reconcile_settle_window_seconds to 90 (S4.5d cap).
_WINDOW_NS = 90 * 1_000_000_000
_CAPS = RiskCaps()


def _recon():
    return ThreeWayReconciler(caps=_CAPS)


def _bal(token, shares, *, latest_fill_at=None):
    return Balance(token_id=token, shares=Decimal(shares), latest_fill_at=latest_fill_at)


def test_wallet_none_is_dormant_not_a_divergence():
    """No wallet => no chain truth => DORMANT (shadow-clean), even though internal holds
    positions the empty chain would otherwise 'diverge' against. The dormant_no_wallet trigger
    is always recorded; nothing is reported as a divergence."""
    internal = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, None, wallet=None, now=0)
    assert result.status == DORMANT
    assert result.divergences == ()
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal(0)
    assert result.triggers == ("dormant_no_wallet",)


def test_onchain_none_with_a_wallet_is_still_dormant():
    """Even with a wallet set, a None on-chain leg (the DORMANT sentinel from onchain_balances)
    means there is no chain to reconcile against -> DORMANT, not a false DIVERGED."""
    internal = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, None, wallet="0xabc", now=0)
    assert result.status == DORMANT
    assert result.triggers == ("dormant_no_wallet",)
