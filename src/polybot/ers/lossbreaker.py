"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

AnomalyMonitor-shaped pure evidence producer over the durable flow_journal: evaluate() reads
store.flow_log() plus the injected caps_provider / wall_clock and returns an immutable
LossState. STICKY BY CONSUMER: this module only ever REPORTS; recovery is operator-owned, so
nothing here touches the op-state machine (structurally pinned). Windows are rolling
wall-clock seconds over wall_at -- the monotonic `at` column is never used for windowing.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.ers.flow import pending_in_window
from polybot.ers.safety import (
    REASON_CONSECUTIVE_LOSS, REASON_DAILY_PENDING_PAUSE, REASON_FLOW_DATA_ERROR,
    REASON_WEEKLY_LOSS)

NONE = "NONE"
PAUSE = "PAUSE"
HALT = "HALT"

_WEEKLY_WINDOW_SECONDS = 604800


@dataclass(frozen=True)
class LossState:
    action: str        # NONE | PAUSE | HALT (HALT beats PAUSE)
    triggers: tuple    # reason strings, most-severe-first; () when NONE
    ramp_steps: tuple  # ("weekly",) / ("daily",) / both -- consumed by run_cycle for swap_caps

    def __post_init__(self):
        # The S4.4 AnomalyState lesson: the consumer indexes triggers[0] on the halt/pause
        # path, so an actionable state with no trigger is unrepresentable.
        if self.action in (PAUSE, HALT) and not self.triggers:
            raise ValueError(
                "PAUSE/HALT requires at least one trigger (the consumer indexes triggers[0])")


class LossBreakers:
    """evaluate(frozen_tokens=...) -> LossState, once per controller cycle (consumed by
    ERSController AFTER the L5 anomaly consult). The fail-closed wrapper is the load-bearing
    frame: ANY raise inside the journal read + window math becomes the data-error halt."""

    def __init__(self, *, store, caps_provider, wall_clock):
        self._store = store
        self._caps_provider = caps_provider   # 0-arg -> RiskCaps (follows the ramp ratchet)
        self._wall_clock = wall_clock         # 0-arg -> float epoch seconds (windowing domain)

    def evaluate(self, *, frozen_tokens=frozenset()):
        try:
            return self._evaluate(frozen_tokens)
        except Exception:
            # FAIL CLOSED (DESIGN §6.4): corruption in our own safety ledger is never skipped
            # and never propagates -- it IS a halt. No ramp step off unreadable data.
            return LossState(HALT, (REASON_FLOW_DATA_ERROR,), ())

    def _evaluate(self, frozen_tokens):
        rows = self._store.flow_log()
        caps = self._caps_provider()
        now = self._wall_clock()
        # Frozen exclusion (DECISIONS row 74): disputed/frozen tokens leave the realized
        # counters entirely (weekly, streak, AND the pending loss component); accept rows are
        # NOT filtered -- frozen positions still count toward pending/open flow.
        realized = [r for r in rows
                    if r["kind"] == "realized" and r["token_id"] not in frozen_tokens]
        triggers = []
        ramp_steps = []
        # Arm 1 (most severe -- DECISIONS row 71): |realized losses| over the rolling 7d wall
        # window, STRICT >, INCLUSIVE old edge.
        weekly_loss_total = sum(
            (abs(r["amount"]) for r in realized
             if r["amount"] < 0 and now - r["wall_at"] <= _WEEKLY_WINDOW_SECONDS),
            Decimal(0))
        weekly_fired = weekly_loss_total > caps.weekly_loss_halt
        if weekly_fired:
            triggers.append(REASON_WEEKLY_LOSS)
            ramp_steps.append("weekly")
        # Arm 2 (row 72): trailing consecutive losses at the END of the realized sequence
        # (flow order). NO time window -- only a win (amount >= 0) breaks the trail.
        streak = 0
        for row in reversed(realized):
            if row["amount"] < 0:
                streak += 1
            else:
                break
        if streak >= caps.consecutive_loss:
            triggers.append(REASON_CONSECUTIVE_LOSS)
        # Arm 3 (rows 70 vs 72 interplay): accepts + |realized losses| in the rolling 24h
        # window via the shared helper (ignores wins; raises on malformed rows -- the
        # fail-closed wrapper converts that raise into the data halt).
        accepts = [r for r in rows if r["kind"] == "accept"]
        pending_today = pending_in_window(accepts + realized, wall_now=now)
        if pending_today > caps.daily_pending_ceiling:
            triggers.append(REASON_DAILY_PENDING_PAUSE)
            ramp_steps.append("daily")
        if not triggers:
            return LossState(NONE, (), ())
        action = HALT if weekly_fired else PAUSE
        return LossState(action, tuple(triggers), tuple(ramp_steps))
