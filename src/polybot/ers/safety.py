"""Operational safety controller + op-state machine (S4.1 / POL-6).

The SafetyController is the operational kill surface consulted at the TOP of process_pending
(new ``controller=`` seam, the same additive pattern as ``breaker=`` / ``pipeline=``). It holds
the op-state, the swappable active-caps reference, and a durable-state handle (the IntentStore,
for the append-only op/kill audit). Its ``verdict`` fails CLOSED and dominates the L7 breaker:
the loop precedence is KILL > op_flatten > l7_flatten > l7_freeze > none.

FLATTENING here is the operator/L5/L6-driven op-state -- DISTINCT from breaker.py's drawdown
FLATTEN action. Crash/restart starts HALTED; RUNNING is only entered after a clean reconcile
(S4.5). Clocks are injected for deterministic TDD; money is Decimal.

RECONCILED DEVIATION (baked into this implementation): ``verdict()`` returns the SPECIFIC stored
reason (``l8_kill`` / ``l8_paused`` / ``unclean_restart`` / ``op_flatten``) as
``Decision.block_reason``, NOT a generic state name like "halted". The controller stores the
current (op_state, reason) pair; ``set_state`` sets both, and ``verdict`` reads the stored reason.
This matches the design's distinct §6 reason codes + the audit trail.
"""

from dataclasses import dataclass

from polybot.ers.ramp import assert_tighten_only

# --- op-state vocabulary (NET-NEW; FLATTENING != breaker.py FLATTEN) -------------------------
RUNNING = "RUNNING"
PAUSED = "PAUSED"
HALTED = "HALTED"
FLATTENING = "FLATTENING"

# The complete op-state vocabulary -- set_state whitelists against it (fail-closed on anything else).
_VALID_STATES = frozenset({RUNNING, PAUSED, HALTED, FLATTENING})

# --- S4.1 reason codes (free-form Decision.reason strings; NO validator/schema change) -------
REASON_L8_KILL = "l8_kill"
REASON_L8_PAUSED = "l8_paused"
REASON_OP_FLATTEN = "op_flatten"
REASON_UNCLEAN_RESTART = "unclean_restart"
# --- S4.5 reason codes (NET-NEW; free-form Decision.reason / op-audit strings) ----------------
REASON_L5_RECON_MISMATCH = "l5_recon_mismatch"     # running-cadence recon-mismatch (S4.4 consumer)
REASON_RESTART_RECONCILED = "restart_reconciled"   # the clean restart-reconcile HALTED->RUNNING reason
# --- S4.4 reason codes (NET-NEW; the L5 AnomalyMonitor trigger vocabulary) --------------------
REASON_L5_CLOCK_SKEW = "l5_clock_skew"        # |wall - ntp| beyond tolerance (halts signing)
REASON_L5_ABNORMAL_BOOK = "l5_abnormal_book"  # crossed/locked mid, depth collapse, mid jump
REASON_L5_API_STORM = "l5_api_storm"          # 5xx / auth-failure storm within the window
REASON_L5_WS_DOWN = "l5_ws_down"              # WS silent beyond staleness (None frame = +inf age)
REASON_L5_CANARY_FAIL = "l5_canary_fail"      # signing canary failed/raised -- NEVER blind-retried


@dataclass(frozen=True)
class OpVerdict:
    """The op-state verdict read at the top of process_pending (mirrors breaker.BreakerState).

    ``action`` is the current op-state; ``block_reason`` is the Decision.reason to reject every
    pending intent under (None => the loop proceeds to the L7 breaker); ``derisk`` is the de-risk
    primitive the loop must fire on the signer (``op_flatten`` => flatten + cancel_all), None
    otherwise; ``triggers`` is the audit/debug provenance tuple."""
    action: str
    block_reason: str | None
    derisk: str | None
    triggers: tuple


class SafetyController:
    def __init__(self, *, caps, store, clock):
        # Starts HALTED: a fresh/restarted controller never trades until an operator (or, in
        # S4.5, a clean restart-reconcile) transitions it to RUNNING. Fail closed.
        self._caps = caps
        self._store = store
        self._clock = clock
        self._state = HALTED
        # Reconciled deviation: track the specific reason so verdict() returns it, not a generic
        # state name. Initial reason is unclean_restart (boot default; never explicitly set).
        self._reason = REASON_UNCLEAN_RESTART

    def state(self):
        return self._state

    def active_caps(self):
        # The swappable RiskCaps reference (the S4.7 ramp-DOWN ratchet replaces it atomically).
        return self._caps

    def swap_caps(self, new_caps, *, reason):
        """The S4.7 ramp-DOWN ratchet: atomically install a NEW re-verified RiskCaps.

        Tighten-only (assert_tighten_only over every field per ramp.TIGHTEN_DIRECTION -- a
        loosening swap raises ValueError and changes NOTHING); idempotent (a hash-identical
        new_caps returns False and writes NO audit row); audited (kind=caps_swap,
        detail=old->new 16-char content-hash prefixes) BEFORE the in-memory mutate, so a
        crash mid-swap leaves the explanation ahead of the effect (the set_state doctrine).
        Applies in ANY op-state -- tightening while halted is harmless and desirable.
        Returns True iff the caps actually changed."""
        assert_tighten_only(self._caps, new_caps)
        old_hash = self._caps.content_hash()
        new_hash = new_caps.content_hash()
        if new_hash == old_hash:
            return False
        self._store.record_op_event(
            kind="caps_swap", reason=reason, detail=f"{old_hash[:16]}->{new_hash[:16]}")
        self._caps = new_caps
        return True

    def set_state(self, op_state, *, reason):
        """Operator/L8-driven transition. Appends an immutable op-audit row, then swaps the
        in-memory op-state + reason. Audit-before-mutate so a crash mid-call leaves an
        explanation. The stored reason is what verdict() reports -- so a kill records l8_kill
        and verdict blocks with l8_kill (not a generic 'halted'). Same for pause/flatten.

        M1: this is the privileged L8/operator authority path, so it fail-closes on an unknown
        op-state (ValueError) -- validated BEFORE the audit write so a bogus state is never even
        recorded or applied."""
        if op_state not in _VALID_STATES:
            raise ValueError(f"unknown op_state: {op_state!r} (expected one of {sorted(_VALID_STATES)})")
        self._store.record_op_event(kind="state_change", reason=reason, detail=op_state)
        self._state = op_state
        self._reason = reason

    def verdict(self, portfolio, signer):
        """Consulted FIRST in process_pending. Fail-closed mapping of op-state -> OpVerdict.

        RECONCILED DEVIATION: returns the SPECIFIC stored reason (set by set_state) as
        block_reason, NOT a generic state name. This means:
          - set_state(HALTED, reason=l8_kill)    -> block_reason == "l8_kill"
          - set_state(HALTED, reason=unclean...) -> block_reason == "unclean_restart"
          - set_state(PAUSED, reason=l8_paused)  -> block_reason == "l8_paused"
          - set_state(FLATTENING, reason=op_flatten) -> block_reason == "op_flatten"

        RUNNING    -> no block (None): the loop falls through to the L7 breaker unchanged.
        HALTED     -> block with the stored reason.
        PAUSED     -> block with the stored reason.
        FLATTENING -> block with stored reason AND de-risk ONCE: signal the exit + cancel working
                      entries, exactly as the L7-FLATTEN short-circuit does, but ahead of it, THEN
                      settle to HALTED (I1) so subsequent cycles block via HALTED and do NOT re-fire
                      the de-risk (a repeated live cancelAll would churn the protective GTD exits).

        A KILL is modelled as the HALTED op-state reached via set_state(HALTED, reason=l8_kill);
        the stored reason is l8_kill, and verdict() blocks with l8_kill -- distinct from the
        startup HALTED which has reason=unclean_restart."""
        if self._state == RUNNING:
            # RUNNING -> no op-block; the loop proceeds to the L7 breaker unchanged.
            return OpVerdict(RUNNING, None, None, ())
        if self._state == PAUSED:
            return OpVerdict(PAUSED, self._reason, None, ("paused",))
        if self._state == FLATTENING:
            # De-risk on the ERS's signer ahead of the breaker (op-FLATTEN dominates L7-FLATTEN):
            # signal the exit, then cancel WORKING ENTRY orders (the GTD exit brackets stay).
            signer.flatten(portfolio.positions)
            signer.cancel_all()
            self._store.record_op_event(
                kind="flatten", reason=REASON_OP_FLATTEN,
                detail=f"{len(portfolio.positions)} positions")
            verdict = OpVerdict(FLATTENING, self._reason, REASON_OP_FLATTEN, ("op_flatten",))
            # I1: de-risk fires ONCE -- settle to HALTED (reason unchanged) so the NEXT cycle blocks
            # via HALTED without re-firing flatten/cancel_all. This cycle still reports FLATTENING.
            self._state = HALTED
            return verdict
        if self._state == HALTED:
            # HALTED (startup default unclean_restart OR explicit kill/halt) -> block (stored reason).
            return OpVerdict(HALTED, self._reason, None, ("halted",))
        # M2: defensive fallthrough -- block under the stored reason but report the state HONESTLY
        # (action=self._state) so a future unexpected state truthfully reaches the S4.3 supervisor /
        # audit consumer rather than masquerading as HALTED.
        return OpVerdict(self._state, self._reason, None, (self._state.lower(),))


# --- S4.2 / POL-6: pure halt-new predicates -------------------------------------------------


def would_cross_daily_pending_ceiling(*, pending_today, new_worst_case, caps):
    """True if accepting ``new_worst_case`` would push today's pending worst-case-risk FLOW past
    caps.daily_pending_ceiling ($24). A pending-FLOW rate gate (new dollars proposed per day),
    DISTINCT from the validator's at-risk STOCK cap (total_open_risk) and the L7 unrealized
    breaker -- so it never double-counts. Fail-closed: blocks on a STRICT crossing (> ceiling);
    allows at-or-below. SEAM (not yet wired): this is a pure, tested predicate staged ahead of
    its consumer. The S4.7 realized-loss-breaker sub-slice WILL call it (and have the
    SafetyController emit the halt-new block_reason) once the durable per-day pending total
    exists -- that fill/pending ledger does not exist yet (only op_audit does), so wiring it
    now would require fabricated state. Until S4.7, this gate is dormant.
    """
    return (pending_today + new_worst_case) > caps.daily_pending_ceiling
