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
    liquidity_impact_cents: Decimal = Decimal("1")  # carried/hashed but the <=1c impact term is
    # DEFERRED to the full multi-level book-walk (slice >3); only liquidity_depth_frac is enforced
    # today. Its absence only ever makes sizing looser WITHIN the already-bound touch-depth cap.
    # L7 real-time unrealized-drawdown breaker (§4 L7, slice 3): freeze adds at $18, FLATTEN
    # at $30, and freeze on a fast rise of $18 within 15 min.
    l7_freeze_floor: Decimal = Decimal("18")
    l7_flatten_floor: Decimal = Decimal("30")
    l7_velocity_delta: Decimal = Decimal("18")
    l7_velocity_window_seconds: int = 900
    # S4 / POL-6 safety-envelope fields (DECISIONS-S0 §4; DOC-only until now). All frozen +
    # _verify-checked + auto-covered by content_hash (asdict serialisation).
    weekly_loss_halt: Decimal = Decimal("36")          # realized weekly loss -> halt+human-review
    consecutive_loss: int = 3                          # N losing trades in a row -> halt
    new_positions_per_hour: int = 2                    # budget-independent rate counter
    new_positions_per_day: int = 6
    gtd_bracket_aggregate: Decimal = Decimal("60")     # aggregate standing-exit ceiling (= total_open_risk)
    clock_skew_tolerance_seconds: int = 2              # wall vs NTP skew that halts SIGNING (L5)
    signing_canary_interval_seconds: int = 300         # cadence of the sign+place+cancel canary
    dead_man_switch_timeout_seconds: int = 30          # stale-heartbeat age -> supervisor FLATTEN_AND_KILL
    reconcile_tolerance: Decimal = Decimal("0.50")     # 3-way divergence tolerance (settle-window-aware)

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
        # L7 breaker ordering: a freeze floor must sit below the flatten floor, and FLATTEN
        # must not exceed the absolute at-risk ceiling (a $30 flatten under a $60 total-open).
        if not (Decimal(0) < self.l7_freeze_floor < self.l7_flatten_floor <= self.total_open_risk):
            raise ValueError(
                f"L7 ordering violated: need 0 < l7_freeze_floor < l7_flatten_floor <= "
                f"total_open_risk, got {self.l7_freeze_floor} / {self.l7_flatten_floor} / "
                f"{self.total_open_risk}"
            )
        if self.l7_velocity_delta <= 0:
            raise ValueError(f"l7_velocity_delta must be > 0, got {self.l7_velocity_delta}")
        if self.l7_velocity_window_seconds <= 0:
            raise ValueError(
                f"l7_velocity_window_seconds must be > 0, got {self.l7_velocity_window_seconds}"
            )
        # --- S4 / POL-6 additive invariants ---
        # A weekly realized-loss halt must not sit BELOW the daily-pending ceiling.
        if self.daily_pending_ceiling > self.weekly_loss_halt:
            raise ValueError(
                f"weekly_loss_halt({self.weekly_loss_halt}) must be >= daily_pending_ceiling "
                f"({self.daily_pending_ceiling})"
            )
        # The aggregate GTD standing-exit ceiling IS the absolute at-risk ceiling -- never looser.
        if self.gtd_bracket_aggregate != self.total_open_risk:
            raise ValueError(
                f"gtd_bracket_aggregate({self.gtd_bracket_aggregate}) must equal total_open_risk "
                f"({self.total_open_risk})"
            )
        # The hourly new-position rate cannot exceed the daily rate.
        if self.new_positions_per_hour > self.new_positions_per_day:
            raise ValueError(
                f"new_positions_per_hour({self.new_positions_per_hour}) must be <= "
                f"new_positions_per_day({self.new_positions_per_day})"
            )
        # All the new strictly-positive scalars.
        if self.weekly_loss_halt <= 0:
            raise ValueError(f"weekly_loss_halt must be > 0, got {self.weekly_loss_halt}")
        if self.reconcile_tolerance <= 0:
            raise ValueError(f"reconcile_tolerance must be > 0, got {self.reconcile_tolerance}")
        for name in ("consecutive_loss", "new_positions_per_hour", "new_positions_per_day",
                     "clock_skew_tolerance_seconds", "signing_canary_interval_seconds",
                     "dead_man_switch_timeout_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")

    def cluster_cap(self, rho):
        """Aggregate worst-case-risk cap for a WARM co-move cluster with representative
        correlation ``rho`` (max pairwise). ``per_trade + (1 - rho) * (total_open - per_trade)``
        clamped to ``[per_trade, total_open_risk]``: rho->1 collapses the cluster to ONE per_trade
        bet; rho->0 relaxes to the global ceiling; rho<0 (hedged) clamps at total_open (no extra
        tightening). The cap is an ADDITIONAL min() term in the validator, so it can only tighten."""
        cap = self.per_trade + (Decimal(1) - rho) * (self.total_open_risk - self.per_trade)
        if cap < self.per_trade:
            return self.per_trade
        if cap > self.total_open_risk:
            return self.total_open_risk
        return cap

    def content_hash(self):
        """SHA-256 over the canonical (sorted, string-valued) fields -- tamper-evidence."""
        payload = json.dumps({k: str(v) for k, v in asdict(self).items()}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()
