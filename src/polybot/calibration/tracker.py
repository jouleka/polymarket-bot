"""Calibration tracker -- the L3 GO/NO-GO sizing multiplier ``k`` (S5 / POL-7).

Scores the bot's OWN resolved forecasts per category and emits ``k`` -- the calibration multiplier
the validator already consumes (``frac_eff = kelly_fraction * min(1, calib_score)``). At v1 ``k`` is
BINARY {0, 1} (DECISIONS-S0 §6: at $300 the sample is a GO/NO-GO gate, not a continuous live-size
dial); the continuous stats are exposed in a ``CalibrationReport`` for a future scale-up policy.

Only honest WON/LOST outcomes are scored. DISPUTED_LOST (a whale-captured UMA flip) and VOID
(refund/50-50) are counted separately and EXCLUDED, so they cannot poison Brier or zero ``k``.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.calibration.scoring import brier, brier_skill, murphy

_OUTCOME = {"WON": 1, "LOST": 0}


@dataclass(frozen=True)
class CalibrationReport:
    category: str
    n_scored: int
    n_disputed: int
    n_void: int
    bot_brier: Decimal | None
    market_brier: Decimal | None
    brier_skill: Decimal | None
    reliability: Decimal | None
    resolution: Decimal | None
    uncertainty: Decimal | None
    go: bool
    k: Decimal


class CalibrationTracker:
    def __init__(self, ledger, config):
        self._ledger = ledger
        self._config = config

    def k_for(self, category):
        """The binary sizing multiplier for a category: 1 only when it passes all go-criteria."""
        return self.report_for(category).k

    def report_for(self, category):
        c = self._config
        bot_pairs, market_pairs = [], []
        n_disputed = n_void = 0
        for r in self._ledger.resolved(category):
            outcome = _OUTCOME.get(r.resolution_status)
            if outcome is not None:
                bot_pairs.append((r.p, outcome))
                market_pairs.append((r.market_mid, outcome))
            elif r.resolution_status == "DISPUTED_LOST":
                n_disputed += 1
            elif r.resolution_status == "VOID":
                n_void += 1
            else:
                # Exhaustive: a status not in {WON,LOST,DISPUTED_LOST,VOID} (DB corruption, or a
                # future 5th VALID_STATUSES not taught here) must fail loud, not silently vanish.
                raise ValueError(f"unhandled resolution status {r.resolution_status!r}")

        n = len(bot_pairs)
        if n == 0:  # no honest resolution yet -> cold (paper-only)
            return CalibrationReport(category, 0, n_disputed, n_void,
                                     None, None, None, None, None, None, False, Decimal(0))

        bot_b = brier(bot_pairs)
        market_b = brier(market_pairs)
        skill = brier_skill(bot_b, market_b)
        m = murphy(bot_pairs, c.n_bins)
        # GO iff: enough sample AND beats the market baseline AND well-calibrated AND discriminating.
        go = (n >= c.min_n
              and skill > c.brier_skill_min
              and m.reliability <= c.reliability_max
              and m.resolution > m.reliability)
        return CalibrationReport(category, n, n_disputed, n_void, bot_b, market_b, skill,
                                 m.reliability, m.resolution, m.uncertainty,
                                 go, Decimal(1) if go else Decimal(0))
