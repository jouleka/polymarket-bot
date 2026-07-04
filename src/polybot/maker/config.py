"""Maker knobs + the parameterized fee schedule (S8 / POL-10), self-verifying at construction.

Several knobs gate live money downstream (the GO floor, the net margin the gate demands, the
rebate fraction in the net identity), so the config verifies its own envelope at construction
and fails LOUD on nonsense — the CalibrationConfig/DetectorConfig discipline. The fee schedule
is a conservative RE-PULLABLE seam (design Fork 3): the real live numbers are
documented-UNSPECIFIED and must be re-pulled at deploy; nothing here is a trusted constant.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FeeCategory:
    name: str
    fee_rate: Decimal
    exponent: Decimal
    active: bool  # False = planned/inactive -> taker_fee 0 until Polymarket activates it
    free: bool    # True = fee-free category -> taker_fee 0 regardless of rate (wins over active)


# Conservative re-pullable defaults: sports is the one ACTIVE fee category (the dossier
# correction: fee_rate 0.03, exponent 1); the other trading categories are planned-INACTIVE
# (same shape, fee 0 until activated); geopolitics is FREE by flag (actively traded, no fee —
# the flag, not a zero rate, is what zeroes it).
DEFAULT_FEE_SCHEDULE: tuple[FeeCategory, ...] = (
    FeeCategory(name="sports", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=True, free=False),
    FeeCategory(name="politics", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="finance", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="tech", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="econ", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="culture", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="weather", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="crypto", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="geopolitics", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=True, free=True),
)


@dataclass(frozen=True)
class MakerConfig:
    fee_schedule: tuple                          # tuple[FeeCategory, ...] — REQUIRED, no default
    rebate_fraction: Decimal = Decimal("0.20")   # maker share of taker fees; (0, 0.5]
    reward_b: Decimal = Decimal("1")             # S(v,s) pool constant; > 0 (deploy-calibrated)
    max_spread: Decimal = Decimal("0.03")        # reward eligibility (rest within this of mid); (0, 1)
    min_samples: int = 150                       # GO floor per category; > 0 (mirrors calibration min_n)
    net_margin_min: Decimal = Decimal("0")       # net must EXCEED this to GO; >= 0
    lockup_rate: Decimal = Decimal("0")          # locked-to-resolution opportunity-cost rate; >= 0
    forced_taker_exit_p: Decimal = Decimal("0")  # P(forced taker exit) in the fee hurdle; [0, 1]
    dispute_p: Decimal = Decimal("0")            # ex-ante P(dispute/void) for the haircut leg; [0, 1]

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if not (Decimal(0) < self.rebate_fraction <= Decimal("0.5")):
            raise ValueError(f"rebate_fraction must be in (0, 0.5], got {self.rebate_fraction}")
        if self.reward_b <= 0:
            raise ValueError(f"reward_b must be > 0, got {self.reward_b}")
        if not (Decimal(0) < self.max_spread < Decimal(1)):
            raise ValueError(f"max_spread must be in (0, 1), got {self.max_spread}")
        if self.min_samples <= 0:
            raise ValueError(f"min_samples must be > 0, got {self.min_samples}")
        if self.net_margin_min < 0:
            raise ValueError(f"net_margin_min must be >= 0, got {self.net_margin_min}")
        if self.lockup_rate < 0:
            raise ValueError(f"lockup_rate must be >= 0, got {self.lockup_rate}")
        if not (Decimal(0) <= self.forced_taker_exit_p <= Decimal(1)):
            raise ValueError(f"forced_taker_exit_p must be in [0, 1], got {self.forced_taker_exit_p}")
        if not (Decimal(0) <= self.dispute_p <= Decimal(1)):
            raise ValueError(f"dispute_p must be in [0, 1], got {self.dispute_p}")
        self._verify_schedule()

    def _verify_schedule(self):
        if not isinstance(self.fee_schedule, tuple) or not self.fee_schedule:
            raise ValueError(f"fee_schedule must be a non-empty tuple, got {self.fee_schedule!r}")
        seen = set()
        for entry in self.fee_schedule:
            if not isinstance(entry, FeeCategory):
                raise ValueError(f"fee_schedule entry must be a FeeCategory, got {entry!r}")
            if not entry.name:
                raise ValueError(f"fee_schedule entry name must be non-empty, got {entry!r}")
            if entry.name in seen:
                raise ValueError(f"fee_schedule entry names must be unique, got duplicate {entry.name!r}")
            seen.add(entry.name)
            if not entry.fee_rate.is_finite() or entry.fee_rate < 0:
                raise ValueError(f"fee_rate must be finite and >= 0, got {entry.fee_rate} for {entry.name!r}")
            if not entry.exponent.is_finite() or entry.exponent < 0:
                raise ValueError(f"exponent must be finite and >= 0, got {entry.exponent} for {entry.name!r}")
