"""Earn-autonomy ramp controller (S9 / POL-11) — the binary stage machine.

Turns an accrued shadow EvidenceReport into a per-category RampDecision. It is STRUCTURALLY
ADVISORY: ramp-UP is a recommendation the operator acts on out-of-band (DECISIONS-S0 §8: the
human ramp-up gate); ramp-DOWN is an automatic flag the existing S4.7 tighten-only ratchet
applies. The controller has NO signer and NO cap-mutation surface at all — it cannot widen or
loosen a cap, so "the controller widened a cap" / "ramp-up mutated caps" are UNREPRESENTABLE,
not merely untested (the structural-honesty pins in test_harness_ramp_controller.py assert the
class surface is bare). The only PnL basis is EvidenceReport.ready, which reads the OUT-OF-SAMPLE
net (never gross, never in-sample). Fail-closed: not-ready -> SHADOW, no promote.
"""

from polybot.harness import stress

SHADOW = "SHADOW"
TINY_LIVE = "TINY_LIVE"
RAMP = "RAMP"

from dataclasses import dataclass

from polybot.harness.evidence import EvidenceReport


@dataclass(frozen=True)
class RampDecision:
    category: str
    stage: str
    promote_recommended: bool
    ramp_down: bool
    reason: str
    evidence: EvidenceReport


class RampController:
    """Advisory stage machine. NO cap-mutation surface (see the module docstring): decide()
    returns ONLY a RampDecision — it never returns a loosened cap, and the class exposes no
    swap_caps / set_state / place / widen / signer attribute."""

    def __init__(self, *, ramp_config, caps):
        self._ramp_config = ramp_config
        self._caps = caps

    def decide(self, category, *, evidence, current_stage, portfolio, n_resolved_disputed,
               stress_episodes, breaker_tripped):
        promote_recommended = evidence.ready
        ramp_down = current_stage != SHADOW and not evidence.ready
        stage = SHADOW if not evidence.ready else current_stage
        reason = self._reason(evidence, ramp_down=ramp_down)
        return RampDecision(category=category, stage=stage,
                            promote_recommended=promote_recommended, ramp_down=ramp_down,
                            reason=reason, evidence=evidence)

    @staticmethod
    def _reason(evidence, *, ramp_down):
        if ramp_down:                       # current_stage != SHADOW and not ready
            return "ramp_down:regression"
        if not evidence.ready:
            # Name the specific failed evidence gate (fail-closed order: oos, calibration, maker).
            if not evidence.oos_positive:
                return "not_ready:oos"
            if not evidence.calibration_ok:
                return "not_ready:calibration"
            if not evidence.maker_ok:
                return "not_ready:maker"
            return "not_ready:sample"       # n_resolved below the floor (all sub-gates ok)
        return "promote_ok"
