"""Earn-autonomy ramp thresholds (S9 / POL-11), self-verifying at construction.

Every knob gates whether the operator may advance a category toward live money
(the Stage-0 resolved floor, the OOS net margin the evidence demands, the
multiple-comparisons inflation, the tail-survival minimums, the Murphy
reliability ceiling), so the config verifies its own envelope at construction and
fails LOUD on nonsense -- the maker/config.py + calibration/config.py discipline
(is_finite() BEFORE every Decimal compare; a NaN ordered-compare raises
InvalidOperation, an Infinity sails through one-sided compares -- both must fail
by field name). oos_n_bins is a plan-time refinement of the design's "reliability"
knob: it pins the Murphy binning (mirrors calibration n_bins).
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RampConfig:
    min_resolved: int = 150                          # Stage-0 floor per category; > 0
    net_margin_min: Decimal = Decimal("0")           # OOS net must EXCEED this; >= 0 & finite
    oos_holdout_fraction: Decimal = Decimal("0.30")  # most-recent fraction held OOS; 0 < f < 1
    min_oos_resolved: int = 30                       # min honest rows in the OOS window; > 0
    mc_penalty: Decimal = Decimal("0")               # per-extra-category OOS-margin inflation; >= 0 & finite
    oos_n_bins: int = 10                             # Murphy binning for the OOS reliability; >= 1
    reliability_max: Decimal = Decimal("0.03")       # Murphy reliability ceiling (slope ~1); 0 < r <= 0.1
    min_resolved_disputed: int = 1                    # tail-survival: >= this many resolved DISPUTED; >= 0
    min_stress_episodes: int = 1                      # tail-survival: >= this many stress episodes; >= 0
    ramp_step_fraction: Decimal = Decimal("0.5")     # advisory widen step (reported only); 0 < s <= 1

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if self.min_resolved <= 0:
            raise ValueError(f"min_resolved must be > 0, got {self.min_resolved}")
        if self.min_oos_resolved <= 0:
            raise ValueError(f"min_oos_resolved must be > 0, got {self.min_oos_resolved}")
        if self.oos_n_bins <= 0:
            raise ValueError(f"oos_n_bins must be > 0, got {self.oos_n_bins}")
        if self.min_resolved_disputed < 0:
            raise ValueError(f"min_resolved_disputed must be >= 0, got {self.min_resolved_disputed}")
        if self.min_stress_episodes < 0:
            raise ValueError(f"min_stress_episodes must be >= 0, got {self.min_stress_episodes}")
        if not self.net_margin_min.is_finite() or self.net_margin_min < 0:
            raise ValueError(f"net_margin_min must be finite and >= 0, got {self.net_margin_min}")
        if not self.oos_holdout_fraction.is_finite() or not (Decimal(0) < self.oos_holdout_fraction < Decimal(1)):
            raise ValueError(f"oos_holdout_fraction must be finite and in (0, 1), got {self.oos_holdout_fraction}")
        if not self.mc_penalty.is_finite() or self.mc_penalty < 0:
            raise ValueError(f"mc_penalty must be finite and >= 0, got {self.mc_penalty}")
        if not self.reliability_max.is_finite() or not (Decimal(0) < self.reliability_max <= Decimal("0.1")):
            raise ValueError(f"reliability_max must be finite and in (0, 0.1], got {self.reliability_max}")
        if not self.ramp_step_fraction.is_finite() or not (Decimal(0) < self.ramp_step_fraction <= Decimal(1)):
            raise ValueError(f"ramp_step_fraction must be finite and in (0, 1], got {self.ramp_step_fraction}")
