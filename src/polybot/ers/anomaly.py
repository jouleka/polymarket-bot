"""L5 AnomalyMonitor -- the anomaly kill-switch (S4.4 / POL-6).

DrawdownBreaker-shaped: constructed with caps + an injected 0-arg ``clock`` returning float
monotonic SECONDS + one seam per trigger; every seam defaults to None == that trigger is
dormant (the data-gated pattern), so a bare monitor never fires. FAIL CLOSED: a wired seam
that RAISES inside evaluate fires its own trigger -- it never masks and never propagates.
STICKY (Fork 1): this module only ever REPORTS anomalies; recovery is operator-owned, so
nothing here touches the op-state machine.
"""

from dataclasses import dataclass

NONE = "NONE"
HALT = "HALT"


@dataclass(frozen=True)
class AnomalyState:
    action: str      # NONE | HALT
    triggers: tuple  # the l5_* reason strings that fired, severity order; () when NONE
