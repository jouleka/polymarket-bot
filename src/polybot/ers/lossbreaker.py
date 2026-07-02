"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

AnomalyMonitor-shaped pure evidence producer over the durable flow_journal: evaluate() reads
store.flow_log() plus the injected caps_provider / wall_clock and returns an immutable
LossState. STICKY BY CONSUMER: this module only ever REPORTS; recovery is operator-owned, so
nothing here touches the op-state machine (structurally pinned). Windows are rolling
wall-clock seconds over wall_at -- the monotonic `at` column is never used for windowing.
"""

from dataclasses import dataclass

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
