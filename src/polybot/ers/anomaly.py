"""L5 AnomalyMonitor -- the anomaly kill-switch (S4.4 / POL-6).

DrawdownBreaker-shaped: constructed with caps + an injected 0-arg ``clock`` returning float
monotonic SECONDS + one seam per trigger; every seam defaults to None == that trigger is
dormant (the data-gated pattern), so a bare monitor never fires. FAIL CLOSED: a wired seam
that RAISES inside evaluate fires its own trigger -- it never masks and never propagates.
STICKY (Fork 1): this module only ever REPORTS anomalies; recovery is operator-owned, so
nothing here touches the op-state machine.
"""

from dataclasses import dataclass

from polybot.ers.safety import REASON_L5_CLOCK_SKEW

NONE = "NONE"
HALT = "HALT"


@dataclass(frozen=True)
class AnomalyState:
    action: str      # NONE | HALT
    triggers: tuple  # the l5_* reason strings that fired, severity order; () when NONE

    def __post_init__(self):
        if self.action == HALT and not self.triggers:
            raise ValueError(
                "HALT requires at least one trigger (the controller reports triggers[0] as "
                "the halt reason)")


class AnomalyMonitor:
    """evaluate(positions, book_for) -> AnomalyState, once per controller cycle. Consults the
    wired seams in pinned severity order and collects ALL firing triggers; triggers[0] is the
    halt reason the consumer reports. S4.4a wires the skew seam; S4.4b-e add the rest."""

    def __init__(self, caps, *, clock, ws_last_frame_at=None, api_sentinel=None,
                 skew_sentinel=None, recon_provider=None, canary=None, dispute_flagger=None):
        self._caps = caps
        self._clock = clock                        # 0-arg -> float monotonic SECONDS
        self._ws_last_frame_at = ws_last_frame_at  # 0-arg -> stamper-domain ns | None (S4.4d)
        self._api_sentinel = api_sentinel          # ApiStormSentinel (S4.4b)
        self._skew_sentinel = skew_sentinel        # duck-typed .skewed() -> bool
        self._recon_provider = recon_provider      # 0-arg -> ReconResult | None (S4.4e)
        self._canary = canary                      # 0-arg -> bool (S4.4e scheduler)
        # DEFERRED seam (UMA dispute watch, design §3): stored + documented, NOT consulted
        # in S4.4 -- no dispute-ingestion source exists yet.
        self._dispute_flagger = dispute_flagger
        self._canary_last_run = None               # float | None: the canary scheduler's memory

    def evaluate(self, positions, book_for):
        triggers = []
        # Severity slot 1 of the pinned order (skew, recon, canary, book, api, ws): clock
        # skew. Slots 2-6 land in S4.4b-e.
        if self._skew_sentinel is not None:
            try:
                if self._skew_sentinel.skewed():
                    triggers.append(REASON_L5_CLOCK_SKEW)
            except Exception:
                # FAIL-CLOSED SEAM RULE: a raising sentinel IS the anomaly -- fire this
                # seam's trigger and continue to the next seam; never mask, never propagate.
                triggers.append(REASON_L5_CLOCK_SKEW)
        if not triggers:
            return AnomalyState(NONE, ())
        return AnomalyState(HALT, tuple(triggers))
