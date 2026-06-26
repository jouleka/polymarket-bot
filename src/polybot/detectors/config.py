"""Detector knobs (S7 / POL-9), consistency-checked at construction (fails LOUD on nonsense)."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DetectorConfig:
    # Luck filter (the skill gate).
    min_resolved: int = 50                              # min resolved bets before a wallet can be SHARP
    win_significance: Decimal = Decimal("0.001")        # one-sided p the wins beat the price-implied baseline
    edge_ci_confidence: Decimal = Decimal("0.99")       # one-sided lower-bound confidence on mean edge
    max_event_dominance: Decimal = Decimal("0.5")       # no single bet > this share of the total positive edge
    # Market-maker exclusion.
    mm_min_trades: int = 100
    mm_balance_min: Decimal = Decimal("0.4")            # min(buy,sell)/max(buy,sell) above this = two-sided MM
    # D1 toxicity.
    toxicity_ratio_min: Decimal = Decimal("0.75")       # one-sided order-flow imbalance ratio
    toxicity_z_min: Decimal = Decimal("2.0")            # z of the imbalance vs the market's own baseline
    # Composite 0-10 bands + single-Critical override.
    band_low_max: Decimal = Decimal("2.5")
    band_med_max: Decimal = Decimal("5.0")
    band_high_max: Decimal = Decimal("7.5")
    critical_subscore: Decimal = Decimal("0.8")         # a single sub-score >= this escalates the band to >=High

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if self.min_resolved <= 0:
            raise ValueError(f"min_resolved must be > 0, got {self.min_resolved}")
        if not (Decimal(0) < self.win_significance < Decimal("0.5")):
            raise ValueError(f"win_significance must be in (0, 0.5), got {self.win_significance}")
        if not (Decimal(0) < self.edge_ci_confidence < Decimal(1)):
            raise ValueError(f"edge_ci_confidence must be in (0, 1), got {self.edge_ci_confidence}")
        if not (Decimal(0) < self.max_event_dominance <= Decimal(1)):
            raise ValueError(f"max_event_dominance must be in (0, 1], got {self.max_event_dominance}")
        if self.mm_min_trades <= 0:
            raise ValueError(f"mm_min_trades must be > 0, got {self.mm_min_trades}")
        if not (Decimal(0) < self.mm_balance_min <= Decimal(1)):
            raise ValueError(f"mm_balance_min must be in (0, 1], got {self.mm_balance_min}")
        if not (Decimal(0) < self.toxicity_ratio_min <= Decimal(1)):
            raise ValueError(f"toxicity_ratio_min must be in (0, 1], got {self.toxicity_ratio_min}")
        if self.toxicity_z_min <= 0:
            raise ValueError(f"toxicity_z_min must be > 0, got {self.toxicity_z_min}")
        if not (Decimal(0) < self.band_low_max < self.band_med_max < self.band_high_max < Decimal(10)):
            raise ValueError(
                f"band cutoffs must satisfy 0 < low < med < high < 10, got "
                f"{self.band_low_max} / {self.band_med_max} / {self.band_high_max}"
            )
        if not (Decimal(0) < self.critical_subscore <= Decimal(1)):
            raise ValueError(f"critical_subscore must be in (0, 1], got {self.critical_subscore}")
