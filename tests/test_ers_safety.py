"""SafetyController op-state machine + the loop-gate verdict (S4.1 / POL-6).

The controller is the operational kill surface that sits ABOVE the L7 breaker: it holds the
op-state (RUNNING/PAUSED/HALTED/FLATTENING -- where FLATTENING is operator/L5/L6-driven, DISTINCT
from breaker.py's drawdown FLATTEN), the swappable active-caps reference, and a durable-state
handle (the IntentStore). verdict(portfolio, signer) is consulted at the TOP of process_pending;
it fails closed and audits every operator transition. Clocks are injected; money is Decimal.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ers import safety
from polybot.ers.safety import OpVerdict, SafetyController


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _ctl(tmp_path, store, *, caps=None, clock=lambda: 0):
    return SafetyController(caps=caps or RiskCaps(), store=store, clock=clock)


def test_op_state_vocab_and_reason_constants():
    # The op-state vocabulary (FLATTENING is distinct from breaker.py FLATTEN).
    assert safety.RUNNING == "RUNNING"
    assert safety.PAUSED == "PAUSED"
    assert safety.HALTED == "HALTED"
    assert safety.FLATTENING == "FLATTENING"
    # The S4.1 reason codes (free-form Decision.reason strings; NO validator change).
    assert safety.REASON_L8_KILL == "l8_kill"
    assert safety.REASON_L8_PAUSED == "l8_paused"
    assert safety.REASON_OP_FLATTEN == "op_flatten"
    assert safety.REASON_UNCLEAN_RESTART == "unclean_restart"


def test_controller_starts_halted_and_blocks_with_unclean_restart(tmp_path):
    # A fresh controller starts HALTED (crash/restart default; RUNNING only after a clean
    # reconcile in S4.5) -> verdict blocks the loop with the unclean_restart reason.
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        assert ctl.state() == safety.HALTED
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert isinstance(v, OpVerdict)
        assert v.action == safety.HALTED
        assert v.block_reason == safety.REASON_UNCLEAN_RESTART
        assert v.derisk is None
        assert "halted" in v.triggers


def test_active_caps_returns_the_held_reference(tmp_path):
    with _store(tmp_path) as store:
        caps = RiskCaps()
        ctl = _ctl(tmp_path, store, caps=caps)
        assert ctl.active_caps() is caps


class _RecordingSigner:
    """A minimal Signer double for S4.1 (S4.2 adds these to PaperSigner). Records flatten +
    cancel_all so we can assert op-FLATTEN de-risks on the ERS's own signer."""

    def __init__(self):
        self.flattened = []
        self.cancelled_all = []

    def flatten(self, positions):
        self.flattened.append(tuple(p.token_id for p in positions))

    def cancel_all(self):
        self.cancelled_all.append("cancel_all")


def _pos(token):
    return OpenPosition("m", "e", "s", "c", Decimal("12"), False,
                        token_id=token, entry_price=Decimal("0.50"))


def test_set_state_running_unblocks_and_audits(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.RUNNING, reason="clean_reconcile")
        assert ctl.state() == safety.RUNNING
        v = ctl.verdict(Portfolio(nav=Decimal("300")), _RecordingSigner())
        assert v.action == safety.RUNNING
        assert v.block_reason is None and v.derisk is None
        # The transition was audited (audit-before-mutate).
        rows = store.op_audit_log()
        assert rows[-1]["kind"] == "state_change"
        assert rows[-1]["reason"] == "clean_reconcile" and rows[-1]["detail"] == safety.RUNNING


def test_pause_blocks_with_l8_paused_and_does_not_derisk(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.PAUSED, reason=safety.REASON_L8_PAUSED)
        signer = _RecordingSigner()
        v = ctl.verdict(Portfolio(nav=Decimal("300"), positions=(_pos("A"),)), signer)
        assert v.action == safety.PAUSED and v.block_reason == safety.REASON_L8_PAUSED
        assert v.derisk is None
        # PAUSE blocks NEW trades but never flattens existing ones.
        assert signer.flattened == [] and signer.cancelled_all == []


def test_flattening_blocks_op_flatten_and_derisks_on_the_signer(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.FLATTENING, reason=safety.REASON_OP_FLATTEN)
        signer = _RecordingSigner()
        portfolio = Portfolio(nav=Decimal("300"), positions=(_pos("A"), _pos("B")))
        v = ctl.verdict(portfolio, signer)
        assert v.action == safety.FLATTENING
        assert v.block_reason == safety.REASON_OP_FLATTEN
        assert v.derisk == safety.REASON_OP_FLATTEN
        # Op-FLATTEN signals the exit AND cancels working entry orders on the ERS's own signer.
        assert signer.flattened == [("A", "B")]
        assert signer.cancelled_all == ["cancel_all"]
        # And it audited a flatten event.
        kinds = [r["kind"] for r in store.op_audit_log()]
        assert "flatten" in kinds


def test_kill_via_set_state_halts_and_returns_l8_kill_reason(tmp_path):
    # Reconciled deviation: set_state(HALTED, reason=l8_kill) -> verdict().block_reason == "l8_kill"
    # NOT "unclean_restart". The specific stored reason is what verdict() reports (§6 reason codes
    # + audit trail). This proves kill vs pause return distinct block_reason values.
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.RUNNING, reason="clean_reconcile")
        ctl.set_state(safety.HALTED, reason=safety.REASON_L8_KILL)
        assert ctl.state() == safety.HALTED
        v = ctl.verdict(Portfolio(nav=Decimal("300")), _RecordingSigner())
        # SPECIFIC reason: l8_kill (not the generic unclean_restart startup default).
        assert v.block_reason == safety.REASON_L8_KILL
        assert v.block_reason != safety.REASON_UNCLEAN_RESTART
        # The kill reason is also in the audit trail.
        assert any(r["reason"] == safety.REASON_L8_KILL for r in store.op_audit_log())


def test_startup_halted_returns_unclean_restart_reason(tmp_path):
    # The INITIAL HALTED state (never set_state'd) uses unclean_restart -- the boot default.
    # This is DISTINCT from an explicit l8_kill.
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        assert ctl.state() == safety.HALTED
        v = ctl.verdict(Portfolio(nav=Decimal("300")), _RecordingSigner())
        assert v.block_reason == safety.REASON_UNCLEAN_RESTART
        assert v.block_reason != safety.REASON_L8_KILL


def test_flattening_derisks_once_then_settles_to_halted(tmp_path):
    # I1 lifecycle: FLATTENING must de-risk EXACTLY ONCE, then settle to HALTED so subsequent
    # cycles block via HALTED without re-firing flatten/cancel_all (a repeated live cancelAll would
    # churn the protective GTD exit brackets). The FIRST verdict reports op_flatten + de-risks;
    # after it the state is HALTED and the de-risk does not fire again.
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.FLATTENING, reason=safety.REASON_OP_FLATTEN)
        signer = _RecordingSigner()
        portfolio = Portfolio(nav=Decimal("300"), positions=(_pos("A"), _pos("B")))

        first = ctl.verdict(portfolio, signer)
        assert first.block_reason == safety.REASON_OP_FLATTEN
        assert first.action == safety.FLATTENING
        # After the first cycle the controller has settled to HALTED (reason unchanged).
        assert ctl.state() == safety.HALTED

        # A second cycle still BLOCKS but must NOT re-fire the de-risk.
        ctl.verdict(portfolio, signer)
        assert len(signer.flattened) == 1     # de-risk fired ONCE, not twice
        assert len(signer.cancelled_all) == 1


def test_set_state_rejects_unknown_state(tmp_path):
    # M1: set_state is the privileged L8/operator authority path -- it must fail closed on an
    # unknown op-state rather than silently accept a state verdict() can't reason about.
    import pytest
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        with pytest.raises(ValueError):
            ctl.set_state("BOGUS", reason=safety.REASON_L8_PAUSED)


def test_s4_5_reason_constants_exist():
    # S4.5d defines the two NET-NEW op-state reason codes here: the running-cadence recon-mismatch
    # reason (S4.4 consumes it) and the clean HALTED->RUNNING restart-reconciled reason. Both are
    # free-form strings (NO validator/schema change), matching the existing REASON_* convention.
    from polybot.ers import safety as _s
    assert _s.REASON_L5_RECON_MISMATCH == "l5_recon_mismatch"
    assert _s.REASON_RESTART_RECONCILED == "restart_reconciled"
