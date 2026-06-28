"""DetectorOrchestrator (S6 / POL-8): composes the S7 pure detectors into ONE defensive verdict.

The ERS loop (process_pending, step 2) consumes this: action == AVOID -> the intent is REJECTed with
reason REASON_DETECTOR_AVOID. The pipeline is toxicity -> d2..d6 -> composite() -> policy.decide().

DEFENSIVE invariants:
  * FOLLOW is hard-off (policy.FOLLOW_ENABLED is False), so the verdict action is only ever AVOID/FLAG_ONLY.
  * toxicity()'s ValueError-on-negative-size (data corruption from the POL-9-deferred /activity parser)
    is CAUGHT here, never propagated -- a corrupt input must degrade to a safe verdict, not wedge the
    per-intent guard. On that path D1 contributes 0 and pull_quotes stays False.
  * At S6 the inputs are placeholder/zeros (live /activity + on-chain parsing is POL-9-deferred); the
    orchestrator and the AVOID->REJECT wiring are real and tested.

p_flow is the D6 smart-money confirmation signal, surfaced as a Decimal (0 weight in fusion v1, logged).
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.detectors.composite import composite
from polybot.detectors.policy import decide
from polybot.detectors.signals import d6_smart_money
from polybot.detectors.toxicity import toxicity

REASON_DETECTOR_AVOID = "detector_avoid"


@dataclass(frozen=True)
class DetectorInputs:
    # S6 defaults = zeros: live /activity + on-chain inputs are POL-9-deferred.
    buy_size: Decimal = Decimal(0)
    sell_size: Decimal = Decimal(0)
    baseline_mean: Decimal = Decimal(0)
    baseline_std: Decimal = Decimal(0)
    d2: Decimal = Decimal(0)
    d3: Decimal = Decimal(0)
    d4: Decimal = Decimal(0)
    d5: Decimal = Decimal(0)
    d6: Decimal = Decimal(0)
    classification: str = "NOISE"
    # RESERVED POL-9 seam -- NOT consumed at S6. At S6, d2..d6 arrive PRE-COMPUTED (already-normalized
    # [0,1] sub-scores fed straight to composite()); the raw->sub-score computation is POL-9's job.
    # POL-9 will apply catalyst_present at THAT stage, where d3_abnormal_move(move_strength,
    # catalyst_present=...) zeroes D3 on a corroborated public catalyst. The orchestrator must NOT call
    # d3_abnormal_move on inputs.d3 -- inputs.d3 is already a sub-score, not a raw move_strength
    # (calling it here would double-compute / type-mismatch).
    catalyst_present: bool = False


@dataclass(frozen=True)
class DetectorVerdict:
    action: str          # 'AVOID' | 'FLAG_ONLY'  (never FOLLOW -- FOLLOW_ENABLED is False)
    pull_quotes: bool
    p_flow: Decimal      # the D6 smart-money confirmation signal (0 weight in fusion v1, logged)
    reasons: tuple


class DetectorOrchestrator:
    def __init__(self, config):
        self._config = config

    def evaluate(self, intent, *, inputs: DetectorInputs) -> DetectorVerdict:
        # D1 toxicity. CATCH the ValueError-on-negative-size: a corrupt size must degrade to a safe
        # verdict (D1 -> 0, no pull-quotes), not blow up the per-intent guard.
        try:
            tox = toxicity(
                inputs.buy_size, inputs.sell_size,
                baseline_mean=inputs.baseline_mean, baseline_std=inputs.baseline_std,
                config=self._config,
            )
            d1 = tox.subscore
            pull_quotes = tox.pull_quotes
        except ValueError:
            d1 = 0.0
            pull_quotes = False

        # D2-D6 are already-normalized [0,1] floats at S6 (zeros until POL-9 wires the live inputs).
        # inputs.d3 is consumed AS A SUB-SCORE -- we do NOT call d3_abnormal_move on it. inputs.catalyst_present
        # is the RESERVED POL-9 seam (NOT consumed here): POL-9 applies it upstream at the raw->sub-score
        # stage, where d3_abnormal_move(move_strength, catalyst_present=...) zeroes D3 on a public catalyst.
        d2 = float(inputs.d2)
        d3 = float(inputs.d3)
        d4 = float(inputs.d4)
        d5 = float(inputs.d5)
        d6 = float(inputs.d6)

        score = composite(
            {"D1": d1, "D2": d2, "D3": d3, "D4": d4, "D5": d5, "D6": d6},
            self._config,
        )

        decision = decide(
            composite_band=score.band,
            classification=inputs.classification,
            pull_quotes=pull_quotes,
        )

        # p_flow = the D6 smart-money score, re-wrapped as a Decimal (0 weight in fusion v1, logged).
        # Going through str() (not Decimal(float)) avoids binary-float noise in equality checks.
        p_flow = Decimal(str(d6_smart_money(edge_weight=d6, conviction=1.0)))

        return DetectorVerdict(
            action=decision.action,
            pull_quotes=decision.pull_quotes,
            p_flow=p_flow,
            reasons=decision.reasons,
        )
