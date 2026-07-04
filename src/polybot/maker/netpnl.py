"""Maker net-PnL identity (S8 / POL-10).

THE honest after-all-costs figure -- the ONLY number the maker gate reads. net is computed
HERE, from the seven named legs, never caller-supplied: a report cannot show gross
reward+rebate+spread while adverse selection bleeds the book invisibly. There is
deliberately NO accessor exposing the credit side alone -- the identity is structural.
net_pnl() is the public construction path for MakerNetPnL.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MakerNetPnL:
    reward: Decimal
    rebate: Decimal
    spread_capture: Decimal
    adverse_selection: Decimal
    fees: Decimal
    lockup_cost: Decimal
    dispute_haircut: Decimal
    net: Decimal  # the after-ALL-costs figure; the ONLY number the gate reads


def net_pnl(*, reward, rebate, spread_capture, adverse_selection, fees, lockup_cost,
            dispute_haircut):
    """net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost
    − dispute_haircut. Fail LOUD ValueError: any non-finite leg; a negative one-signed
    leg (reward/rebate/fees/lockup_cost/dispute_haircut). spread_capture and
    adverse_selection may be either sign — a favorable mark is a negative adverse cost,
    and subtracting it INCREASES net."""
    legs = (("reward", reward), ("rebate", rebate), ("spread_capture", spread_capture),
            ("adverse_selection", adverse_selection), ("fees", fees),
            ("lockup_cost", lockup_cost), ("dispute_haircut", dispute_haircut))
    for name, value in legs:
        if not value.is_finite():
            raise ValueError(f"{name} must be a finite Decimal, got {value}")
    for name, value in legs:
        if name not in ("spread_capture", "adverse_selection") and value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
    net = (reward + rebate + spread_capture
           - adverse_selection - fees - lockup_cost - dispute_haircut)
    return MakerNetPnL(reward=reward, rebate=rebate, spread_capture=spread_capture,
                       adverse_selection=adverse_selection, fees=fees,
                       lockup_cost=lockup_cost, dispute_haircut=dispute_haircut, net=net)
