"""Calibration knobs (S5 / POL-7), consistency-checked at construction.

Unlike ``RiskCaps`` (the signed §4 MONEY envelope) these are calibration/anchor TUNING
parameters, but several gate live money (the go-criteria, the Anchor Gate clamp), so the config
verifies its own internal consistency at construction and fails LOUD on a nonsense envelope.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CalibrationConfig:
    # GO/NO-GO go-criteria (the L3 sizing gate).
    min_n: int = 150                                   # min resolved/category before k can be 1 (§6: 150-200)
    n_bins: int = 10                                   # reliability-diagram bins for the Murphy decomposition
    reliability_max: Decimal = Decimal("0.03")         # Murphy reliability must be <= this (~0 = well-calibrated)
    brier_skill_min: Decimal = Decimal("0")            # bot Brier-skill vs the market-mid baseline must exceed this
    # Base-rate prior.
    longshot_lambda: Decimal = Decimal("0.9")          # shrink extreme priors toward 0.5 (favorite-longshot)
    # Anchor Gate (log-odds shift bounds).
    max_shift_uncorroborated: Decimal = Decimal("1.0")
    max_shift_corroborated: Decimal = Decimal("2.5")
    prior_decay_window_seconds: int = 86400            # within this of resolution, drop the prior anchor (24h)
    epsilon: Decimal = Decimal("0.001")                # clamp probabilities to (eps, 1-eps) before logit

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if self.min_n <= 0:
            raise ValueError(f"min_n must be > 0, got {self.min_n}")
        if self.n_bins < 1:
            raise ValueError(f"n_bins must be >= 1, got {self.n_bins}")
        # reliability_max gates "well-calibrated" (~0); a value near 1 would disable that arm of
        # the GO conjunction, so bound it to a sane ceiling (review M1).
        if not (Decimal(0) < self.reliability_max <= Decimal("0.1")):
            raise ValueError(f"reliability_max must be in (0, 0.1], got {self.reliability_max}")
        if self.brier_skill_min < 0:
            raise ValueError(f"brier_skill_min must be >= 0, got {self.brier_skill_min}")
        if not (Decimal(0) < self.longshot_lambda <= Decimal(1)):
            raise ValueError(f"longshot_lambda must be in (0, 1], got {self.longshot_lambda}")
        # Ordered AND absolutely bounded: an over-large corroborated shift removes the clamp and
        # makes the Anchor Gate toothless (review M2). 5.0 in log-odds ~ sigmoid 0.993.
        if not (Decimal(0) < self.max_shift_uncorroborated < self.max_shift_corroborated <= Decimal("5")):
            raise ValueError(
                f"need 0 < max_shift_uncorroborated < max_shift_corroborated <= 5, got "
                f"{self.max_shift_uncorroborated} / {self.max_shift_corroborated}"
            )
        if self.prior_decay_window_seconds < 0:
            raise ValueError(f"prior_decay_window_seconds must be >= 0, got {self.prior_decay_window_seconds}")
        if not (Decimal(0) < self.epsilon < Decimal("0.5")):
            raise ValueError(f"epsilon must be in (0, 0.5), got {self.epsilon}")
