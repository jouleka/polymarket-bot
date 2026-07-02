"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

AnomalyMonitor-shaped pure evidence producer over the durable flow_journal: evaluate() reads
store.flow_log() plus the injected caps_provider / wall_clock and returns an immutable
LossState. STICKY BY CONSUMER: this module only ever REPORTS; recovery is operator-owned, so
nothing here touches the op-state machine (structurally pinned). Windows are rolling
wall-clock seconds over wall_at -- the monotonic `at` column is never used for windowing.
"""

from dataclasses import dataclass

from polybot.ers.safety import REASON_FLOW_DATA_ERROR

NONE = "NONE"
PAUSE = "PAUSE"
HALT = "HALT"


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
        self._store.flow_log()   # the arms land in D3-D7; the read must happen (fail-closed)
        return LossState(NONE, (), ())
