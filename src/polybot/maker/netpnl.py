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
    − dispute_haircut. adverse_selection may be negative (favorable marks) — subtracting
    a negative INCREASES net; spread_capture likewise two-signed."""
    net = (reward + rebate + spread_capture
           - adverse_selection - fees - lockup_cost - dispute_haircut)
    return MakerNetPnL(reward=reward, rebate=rebate, spread_capture=spread_capture,
                       adverse_selection=adverse_selection, fees=fees,
                       lockup_cost=lockup_cost, dispute_haircut=dispute_haircut, net=net)
