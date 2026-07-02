"""S4.4e (POL-6): per-cycle reconcile cadence + signing-canary scheduler + dispute stub + the e2e.

Pins make_recon_provider (the 0-arg recon_provider seam factory; the shadow short-circuit is
proven with a RAISING event store), the AnomalyMonitor recon consult (DIVERGED and any UNKNOWN
status fire l5_recon_mismatch; OK/DORMANT/SETTLING do not; a raising provider fires -- the
fail-closed seam rule), the canary scheduler (first evaluate due, `>=` interval re-due, at most
one call per cycle, falsy/raise -> l5_canary_fail, NEVER blind-retried), the inert
dispute_flagger stub seam, and the DESIGN-S4.4 §8.3 e2e on the real assembly. Helpers are
module-level copies (no conftest); clocks are injected 0-arg callables; money is Decimal from
string literals.
"""

import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.core.models import Envelope
from polybot.ers import safety as _safety
from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    ReconResult,
    ThreeWayReconciler,
    make_recon_provider,
)
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition
from polybot.ingestion.orderbook import LocalBook

WALLET = "0xcafe000000000000000000000000000000000001"


# --- module-level helpers (per-file copies by convention; no conftest) ------------------------

def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _pos(token="t1"):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal("8"), matrix_cold=False, token_id=token,
                        entry_price=Decimal("0.50"), frozen=False)


def _recon(status):
    return ReconResult(status=status, divergences=(), onchain_confirmed_exposure=Decimal("0"),
                       settling_tokens=(), triggers=())


def _clock_box(start=0.0):
    """Injected 0-arg monitor clock (float monotonic SECONDS) + the mutable box that advances it."""
    box = {"now": float(start)}
    return (lambda: box["now"]), box


def _fill(token, side, shares, at, *, intent="i1"):
    # Mirrors IntentStore.fills_log() row shape (S4.5a): Decimals already converted.
    return {"at": at, "intent_id": intent, "token_id": token, "condition_id": "0xcond",
            "event_id": "evt", "side": side, "shares": Decimal(shares),
            "price_exec": Decimal("0.50"), "worst_case_risk": Decimal(shares) * Decimal("0.50")}


class _FillsStore:
    """Stub of the ONE IntentStore method make_recon_provider reads: fills_log()."""

    def __init__(self, rows=()):
        self._rows = list(rows)

    def fills_log(self):
        return list(self._rows)


class _EventStore:
    """Stub of the ONE event-store method make_recon_provider reads: all() -> Envelopes."""

    def __init__(self, envelopes=()):
        self._envelopes = list(envelopes)

    def all(self):
        return list(self._envelopes)


class _RaisingEventStore:
    """Proves the wallet=None short-circuit: ANY scan of the event store blows the test up."""

    def all(self):
        raise AssertionError("event_store.all() must not be scanned when wallet is None")


def _positions_env(asset, size, *, eid_suffix="0xwallet"):
    # Mirrors data_api.py: content is json.dumps(item); event_id is "/positions:<id>".
    item = {"asset": asset, "size": size, "conditionId": "0xcond"}
    return Envelope(source="data-api", source_tier="DATA",
                    event_id=f"/positions:{eid_suffix}", observed_at=1,
                    content=json.dumps(item, sort_keys=True, default=str))


def _chain_env(event, *, eid="0xtx:0"):
    # Mirrors polygon.py: content is json.dumps({"log": log, "event": event}).
    return Envelope(source="polygon-chain", source_tier="CHAIN", event_id=eid,
                    observed_at=1, content=json.dumps({"log": {}, "event": event},
                                                      sort_keys=True, default=str))


def _single(frm, to, token, value):
    return {"kind": "transfer_single", "operator": "0xop", "from": frm, "to": to,
            "token_id": token, "value": value}


# --- Task E1: make_recon_provider shadow short-circuit ----------------------------------------

def test_make_recon_provider_wallet_none_short_circuits_to_dormant_without_scanning_event_store():
    """Shadow path (wallet=None): the provider must call reconciler.reconcile({}, {}, None,
    wallet=None, now=clock_ns()) WITHOUT touching the event store -- the RAISING event store
    kills the mutation that drops the short-circuit and always builds the three legs."""
    provider = make_recon_provider(_FillsStore(), _RaisingEventStore(),
                                   ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=None, clock_ns=lambda: 0)
    result = provider()
    assert result.status == DORMANT


# --- Task E2: make_recon_provider with a real wallet builds the three legs ---------------------

def test_make_recon_provider_with_wallet_agreement_across_three_legs_is_ok():
    """wallet set: the provider folds internal_balances(store.fills_log(), in_session=True) +
    clob_balances(event_store.all()) + onchain_balances(event_store.all(), wallet=wallet) and
    hands them to the reconciler. 5 internal shares vs 5 on-chain shares (raw 5_000_000 /
    10**6) agree -> OK. Kills the mutation that keeps the E1 shadow short-circuit for a real
    wallet (that would report DORMANT, not OK)."""
    store = _FillsStore([_fill("42", "BUY", "5", at=0)])
    events = _EventStore([_positions_env("42", "5"),
                          _chain_env(_single("0xseller", WALLET, "42", "5000000"))])
    provider = make_recon_provider(store, events, ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=WALLET, clock_ns=lambda: 200_000_000_000)
    assert provider().status == OK


def test_make_recon_provider_with_wallet_unexplained_internal_excess_is_diverged():
    """wallet set: 5 internal shares with NO on-chain transfer is a $5 delta over the $0.50
    tolerance, and now (200s in ns) is far past the fill's settle window (fill at=0, window
    90s) -> DIVERGED with the token named. Kills the mutation that drops/swaps the internal
    leg (an empty internal fold would read OK)."""
    store = _FillsStore([_fill("42", "BUY", "5", at=0)])
    events = _EventStore([_positions_env("42", "0", eid_suffix="0xother")])
    provider = make_recon_provider(store, events, ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=WALLET, clock_ns=lambda: 200_000_000_000)
    result = provider()
    assert result.status == DIVERGED
    assert [d.token_id for d in result.divergences] == ["42"]


# --- Task E3: the recon_provider seam in AnomalyMonitor.evaluate ------------------------------

def test_recon_seam_diverged_status_fires_l5_recon_mismatch_halt():
    """A recon_provider returning DIVERGED must HALT with l5_recon_mismatch as triggers[0]
    (the set_state reason). Kills: dropping the recon consult from evaluate entirely."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: _recon(DIVERGED))
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert state.triggers[0] == "l5_recon_mismatch"


def test_recon_seam_ok_dormant_and_settling_statuses_do_not_fire():
    """OK / DORMANT / SETTLING are the three benign reconcile outcomes (the settle window
    exists precisely so in-flight fills don't false-halt); none may fire. Kills: inverting
    the status check so a healthy reconcile halts the loop."""
    for status in (OK, DORMANT, SETTLING):
        clock, _ = _clock_box()
        monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: _recon(status))
        state = monitor.evaluate((), {}.get)
        assert state.action == NONE, f"{status} must not fire"
        assert state.triggers == ()


def test_recon_seam_absent_provider_is_dormant():
    """recon_provider=None (the default) keeps the trigger dormant -- the data-gated seam
    pattern. Kills: consulting a None seam (TypeError on the call)."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock)
    assert monitor.evaluate((), {}.get).action == NONE


def test_recon_seam_provider_returning_none_result_is_skipped():
    """A wired provider yielding None (no result this cycle) is a SKIP -- not a fire, not a
    crash. Kills: unconditional r.status attribute access on a None result."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: None)
    assert monitor.evaluate((), {}.get).action == NONE
