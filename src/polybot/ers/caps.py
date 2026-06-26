"""The signed risk-caps envelope (S3 / POL-5) -- the numbers that REPLACE the human.

``RiskCaps`` carries the DECISIONS-S0 §4 values and verifies its own internal
consistency AT CONSTRUCTION, so an inconsistent envelope -- the exact defects the S0
adversarial verification caught -- is impossible to build. ``content_hash`` gives
tamper-evidence (the seed of the signed-caps startup self-test; a real signature is S4).
All money fields are Decimal for exact math.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskCaps:
    # Capital band (NAV = $300): deployed (= worst-case risk) <= total_open_risk;
    # reserve = nav - total_open_risk; locked is a sub-budget inside deployed.
    nav: Decimal = Decimal("300")
    total_open_risk: Decimal = Decimal("60")          # 20% NAV -- absolute at-risk ceiling
    reserve_floor: Decimal = Decimal("240")           # = nav - total_open_risk
    # Per-intent caps (worst-case mark-to-resolution loss = notional for a long).
    per_trade: Decimal = Decimal("12")
    per_market: Decimal = Decimal("18")
    per_event_union: Decimal = Decimal("24")
    per_negrisk_event: Decimal = Decimal("18")
    per_source_open: Decimal = Decimal("30")
    per_source_locked_effective: Decimal = Decimal("18")
    max_locked_to_resolution: Decimal = Decimal("36")
    # Concurrency.
    max_concurrent: int = 4
    matrix_cold_concurrent: int = 3                   # positions with UNKNOWN pairwise corr
    # Breakers / sizing.
    daily_pending_ceiling: Decimal = Decimal("24")
    kelly_fraction: Decimal = Decimal("0.25")
    min_position_floor: Decimal = Decimal("5")
    # Liquidity cap (touch depth in slice 1): <= frac of resting depth AND <= impact cents.
    liquidity_depth_frac: Decimal = Decimal("0.10")
    liquidity_impact_cents: Decimal = Decimal("1")

    def __post_init__(self):
        self._verify()

    def _verify(self):
        # Breaker ordering (§Verification #1: a daily halt must not sit below per-trade loss).
        if not (self.per_trade < self.daily_pending_ceiling < self.total_open_risk):
            raise ValueError(
                f"breaker ordering violated: need per_trade < daily_pending_ceiling < "
                f"total_open_risk, got {self.per_trade} / {self.daily_pending_ceiling} / "
                f"{self.total_open_risk}"
            )
        # No zero-slack concurrency (§Verification #2: N x per_trade == total_open exactly).
        if self.max_concurrent * self.per_trade > self.total_open_risk:
            raise ValueError(
                f"zero-slack concurrency: max_concurrent({self.max_concurrent}) * "
                f"per_trade({self.per_trade}) = {self.max_concurrent * self.per_trade} > "
                f"total_open_risk({self.total_open_risk})"
            )
        if not (0 < self.matrix_cold_concurrent <= self.max_concurrent):
            raise ValueError("need 0 < matrix_cold_concurrent <= max_concurrent")
        # One capital band, no triple-counting: reserve = nav - total_open.
        if self.reserve_floor != self.nav - self.total_open_risk:
            raise ValueError(
                f"reserve_floor({self.reserve_floor}) must equal nav - total_open_risk "
                f"({self.nav - self.total_open_risk})"
            )
        # Taxonomy fix (§Verification #3): at-risk ceiling <= 20% NAV.
        if self.total_open_risk > Decimal("0.20") * self.nav:
            raise ValueError(
                f"at-risk ceiling total_open_risk({self.total_open_risk}) exceeds 20% NAV "
                f"({Decimal('0.20') * self.nav})"
            )
        if not (Decimal(0) < self.kelly_fraction <= Decimal("0.5")):
            raise ValueError(f"kelly_fraction must be in (0, 0.5], got {self.kelly_fraction}")
        if self.min_position_floor < Decimal("5"):
            raise ValueError(f"min_position_floor must be >= $5 dust floor, got {self.min_position_floor}")
        positives = ("nav", "total_open_risk", "per_trade", "per_market", "per_event_union",
                     "per_negrisk_event", "per_source_open", "per_source_locked_effective",
                     "max_locked_to_resolution", "daily_pending_ceiling", "liquidity_depth_frac",
                     "liquidity_impact_cents")
        for name in positives:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")

    def content_hash(self):
        """SHA-256 over the canonical (sorted, string-valued) fields -- tamper-evidence."""
        payload = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()
