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


def test_internal_equals_onchain_within_tolerance_is_ok():
    """When every token's internal shares equal the on-chain shares (delta 0 <= $0.50 tol),
    the verdict is OK with no divergences; onchain_confirmed_exposure sums the on-chain shares
    at the $1/share resolution ceiling."""
    internal = {"t1": _bal("t1", "10"), "t2": _bal("t2", "3")}
    onchain = {"t1": _bal("t1", "10"), "t2": _bal("t2", "3")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == OK
    assert result.divergences == ()
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal("13")


def test_sub_tolerance_delta_is_still_ok():
    """A share-delta valued under the $0.50 reconcile_tolerance (0.4 shares = $0.40) does NOT
    diverge -- the tolerance band is inclusive at the boundary's interior."""
    internal = {"t1": _bal("t1", "10.4")}
    onchain = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == OK
    assert result.divergences == ()


def test_injected_divergence_internal_holds_onchain_empty_is_diverged():
    """HEADLINE acceptance criterion: the ERS believes it holds 7 shares of a token the chain
    shows ZERO of (wallet injected). The 7-share gap valued at the $1 resolution ceiling = $7.00
    > $0.50 tolerance, and the internal fill is replayed (latest_fill_at=None -> no settle grace)
    -> DIVERGED, with a single Divergence pinning internal=7, onchain=0, dollars=$7.00."""
    internal = {"t1": _bal("t1", "7", latest_fill_at=None)}
    onchain = {}  # chain shows nothing for t1
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("7"),
                   onchain_shares=Decimal("0"), dollars=Decimal("7")),
    )
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal("0")


def test_just_over_tolerance_diverges():
    """A 0.6-share gap = $0.60 > the $0.50 tolerance with a replayed fill -> DIVERGED (the band
    is exclusive just past the tolerance), pinning dollars=$0.60."""
    internal = {"t1": _bal("t1", "10.6", latest_fill_at=None)}
    onchain = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences[0].dollars == Decimal("0.6")


def test_onchain_only_orphan_diverges():
    """A token present ONLY on-chain (the internal ledger never recorded it) is an orphan: the
    union iteration sees internal=absent=0 vs onchain=5 -> $5 > tol -> DIVERGED, pinning
    internal=0, onchain=5, dollars=$5."""
    internal = {}
    onchain = {"t1": _bal("t1", "5")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("0"),
                   onchain_shares=Decimal("5"), dollars=Decimal("5")),
    )
    assert result.onchain_confirmed_exposure == Decimal("5")


def test_internal_only_orphan_past_window_diverges():
    """A token present ONLY internally with a REPLAYED fill (latest_fill_at=None -> no grace) and
    nothing on-chain is the inverse orphan -> DIVERGED, internal=4, onchain=0, dollars=$4."""
    internal = {"t1": _bal("t1", "4", latest_fill_at=None)}
    onchain = {}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("4"),
                   onchain_shares=Decimal("0"), dollars=Decimal("4")),
    )
