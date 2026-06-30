"""RestartReconciler boot state machine (S4.5d / POL-6).

At boot the controller starts HALTED(unclean_restart). The restart-reconcile is the ONLY automatic
path to RUNNING: it replays the durable stores, three-way-reconciles, rebuilds the Portfolio, and
flips HALTED->RUNNING *only* on a clean (OK/DORMANT) result. Crash defaults to HOLD. wallet=None is
DORMANT (pure shadow: no chain truth) -> treated as clean -> RUNNING, portfolio rebuilt from the
internal ACCEPTED set. Clocks are injected (monotonic-ns); money is Decimal.
"""

import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.core.models import Envelope
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.reconcile import ThreeWayReconciler
from polybot.ers.restart import RestartReconciler
from polybot.ers.safety import SafetyController
from polybot.ers.service import make_fill_sink
from polybot.ers.validator import Decision, Portfolio
from polybot.storage.market_memory import EventStore

_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _accept_one(store, intent_id="i1", **over):
    # Drive one intent to ACCEPTED + record its fill, mirroring what process_pending+make_fill_sink
    # do on an ACCEPT: stake $8 of a token entered at $0.50 -> 16 shares, $8 worst-case risk.
    store.propose_trade(intent_id, **dict(_P, **over))
    store.record_decision(intent_id, Decision("ACCEPT", Decimal("8"), Decimal("0.50"), "kelly"))
    store.record_fill(intent_id=intent_id, token_id=over.get("token_id", "t1"),
                      condition_id=over.get("condition_id", "m1"),
                      event_id=over.get("event_id", "e1"), side="BUY",
                      shares=Decimal("16"), price_exec=Decimal("0.50"),
                      worst_case_risk=Decimal("8"))


def test_dormant_no_wallet_transitions_running_and_rebuilds_portfolio(tmp_path):
    # wallet=None => DORMANT (pure shadow). The RestartReconciler transitions the HALTED controller
    # to RUNNING(restart_reconciled) and returns a Portfolio rebuilt from the ACCEPTED rows.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        assert ctl.state() == _safety.HALTED  # boot default
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 0, wallet=None)
        portfolio = rr.reconcile_on_boot()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
        assert isinstance(portfolio, Portfolio)
        assert [p.token_id for p in portfolio.positions] == ["t1"]
        pos = portfolio.positions[0]
        assert pos.worst_case_risk == Decimal("8")
        assert pos.entry_price == Decimal("0.50")
        assert pos.condition_id == "m1" and pos.event_id == "e1"
    finally:
        store.close()
        events.close()


def _onchain_env(token_id, value, *, wallet, observed_at, kind="transfer_single"):
    # A Polygon ERC-1155 credit TO the wallet: source="polygon-chain", content carries the decoded
    # transfer event in the {"log":.., "event":..} shape onchain_balances parses. For a single
    # transfer the event provides from/to/token_id/value (value is the RAW 6-decimal-scaled int).
    content = json.dumps({"log": {"block": 1},
                          "event": {"kind": kind, "operator": "0xop",
                                    "from": "0x0", "to": wallet,
                                    "token_id": token_id, "value": str(value)}})
    return Envelope(source="polygon-chain", source_tier="CHAIN", event_id=f"chain:{token_id}",
                    observed_at=observed_at, content=content)


def test_injected_onchain_divergence_stays_halted(tmp_path):
    # ACCEPTANCE CRITERION: internal holds 16 shares of t1; the chain (wallet injected) shows a
    # DIFFERENT balance (0 shares of t1 -> a $16 divergence well past the $0.50 tolerance). The
    # replayed fill carries latest_fill_at=None (no boot settle grace) => DIVERGED => the controller
    # STAYS HALTED(unclean_restart). A human must reconcile; never auto-resume.
    wallet = "0xabc"
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)  # internal: 16 shares of t1
        # Chain shows a DIFFERENT token entirely (t2), so t1 is on-chain-absent => 16-share orphan.
        events.append(_onchain_env("t2", 16_000_000, wallet=wallet, observed_at=1))
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 5_000_000_000, wallet=wallet)
        rr.reconcile_on_boot()
        assert ctl.state() == _safety.HALTED
        assert ctl.state() != _safety.RUNNING
        # The boot decision is audited under unclean_restart (NOT restart_reconciled).
        last = store.op_audit_log()[-1]
        assert last["reason"] == "unclean_restart"
        assert all(r["reason"] != "restart_reconciled" for r in store.op_audit_log())
    finally:
        store.close()
        events.close()


def test_clean_live_reconcile_transitions_running(tmp_path):
    # Live (wallet injected) AND internal == on-chain within tolerance => OK => the controller
    # transitions HALTED->RUNNING(restart_reconciled). This proves DORMANT is NOT the only RUNNING
    # path: a genuine clean 3-way match also resumes. (Distinguishes a real clean chain from shadow.)
    wallet = "0xabc"
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)  # internal: 16 shares of t1
        # Chain shows EXACTLY 16 shares of t1 to the wallet (16 * 10**6 raw, 6-decimal scaled).
        events.append(_onchain_env("t1", 16_000_000, wallet=wallet, observed_at=1))
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 5_000_000_000, wallet=wallet)
        rr.reconcile_on_boot()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
    finally:
        store.close()
        events.close()
