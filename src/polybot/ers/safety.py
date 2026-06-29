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

# --- op-state vocabulary (NET-NEW; FLATTENING != breaker.py FLATTEN) -------------------------
RUNNING = "RUNNING"
PAUSED = "PAUSED"
HALTED = "HALTED"
FLATTENING = "FLATTENING"

# --- S4.1 reason codes (free-form Decision.reason strings; NO validator/schema change) -------
REASON_L8_KILL = "l8_kill"
REASON_L8_PAUSED = "l8_paused"
REASON_OP_FLATTEN = "op_flatten"
REASON_UNCLEAN_RESTART = "unclean_restart"


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

    def set_state(self, op_state, *, reason):
        """Operator/L8-driven transition. Appends an immutable op-audit row, then swaps the
        in-memory op-state + reason. Audit-before-mutate so a crash mid-call leaves an
        explanation. The stored reason is what verdict() reports -- so a kill records l8_kill
        and verdict blocks with l8_kill (not a generic 'halted'). Same for pause/flatten."""
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
        FLATTENING -> block with stored reason AND de-risk: signal the exit + cancel working
                      entries, exactly as the L7-FLATTEN short-circuit does, but ahead of it.

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
            return OpVerdict(FLATTENING, self._reason, REASON_OP_FLATTEN, ("op_flatten",))
        # HALTED (startup default unclean_restart OR explicit kill/halt) -> block with stored reason.
        return OpVerdict(HALTED, self._reason, None, ("halted",))
