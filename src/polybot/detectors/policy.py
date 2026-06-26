"""The detector decision policy (S7 / POL-9) -- DEFENSIVE; FOLLOW is hard-disabled in code.

Maps the composite band + classification + D1 toxicity to an action. ``FOLLOW_ENABLED`` is a
module constant pinned False, gating the ONLY FOLLOW branch (dead code at v1): observing public
on-chain flow and copying is legally fine, but the genuine trap is downstream adverse selection,
so auto-follow stays OFF until precision is empirically proven. The clearly +EV use is DEFENSIVE:
avoid trading into likely-informed flow, FLAG it, and pass D1 toxicity to the maker pull-quote seam.
Pure.
"""

from dataclasses import dataclass

from polybot.detectors.classify import INSIDER_LIKE, SHARP
from polybot.detectors.composite import CRITICAL, HIGH

FOLLOW = "FOLLOW"
AVOID = "AVOID"
FLAG_ONLY = "FLAG_ONLY"

# v1: FOLLOW is hard-disabled. The single FOLLOW branch below is therefore dead code; a test
# sweeps the entire signal matrix and asserts the action is NEVER FOLLOW.
FOLLOW_ENABLED = False


@dataclass(frozen=True)
class DetectorDecision:
    action: str          # AVOID | FLAG_ONLY  (never FOLLOW while FOLLOW_ENABLED is False)
    pull_quotes: bool    # the D1 maker pull-quote seam (passed through)
    reasons: tuple


def decide(*, composite_band, classification, pull_quotes):
    if FOLLOW_ENABLED and classification == SHARP:  # dead at v1 -- FOLLOW stays OFF
        return DetectorDecision(FOLLOW, pull_quotes, ("sharp_follow",))

    reasons = []
    if composite_band in (HIGH, CRITICAL):
        reasons.append("informed_flow")
    if classification == INSIDER_LIKE:
        reasons.append("insider_like")
    action = AVOID if reasons else FLAG_ONLY  # don't trade INTO likely-informed flow
    if pull_quotes:
        reasons.append("toxic_pull_quotes")
    return DetectorDecision(action, pull_quotes, tuple(reasons))
