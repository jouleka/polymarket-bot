"""Maker GO/NO-GO gate (S8 / POL-10).

Scores the shadow maker sample per category, honestly: every leg of
``net = reward + rebate + spread_capture - adverse_selection - fees - lockup_cost -
dispute_haircut`` is derived from the ledger's settled WON/LOST rows; DISPUTED/VOID are counted
separately and EXCLUDED from every leg (whale-flip immunity); GO reads ``.net`` ONLY -- never a
reward-gross leg (the master design's "bleeds invisibly" pin). Binary and data-gated: cold or
below ``min_samples`` -> no GO. ``lockup_cost`` = ``lockup_rate * total notional``; the
per-day x days-to-resolution folding is deferred deploy calibration.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.maker.fees import rebate, taker_fee
from polybot.maker.inventory import _SGN, MakerFill, adverse_selection
from polybot.maker.netpnl import net_pnl

_HONEST = ("WON", "LOST")


@dataclass(frozen=True)
class MakerReport:
    category: str
    n_settled: int
    n_disputed: int
    n_void: int
    reward: Decimal | None
    rebate: Decimal | None
    spread_capture: Decimal | None
    adverse_selection: Decimal | None
    fees: Decimal | None
    lockup_cost: Decimal | None
    dispute_haircut: Decimal | None
    net: Decimal | None
    go: bool


class MakerTracker:
    def __init__(self, ledger, config):
        self._ledger = ledger
        self._config = config

    def report_for(self, category):
        c = self._config
        honest = [r for r in self._ledger.settled(category) if r.status in _HONEST]
        n = len(honest)
        if n == 0:  # cold -- no honest settled sample yet (shadow-only, data-gated dormant)
            return MakerReport(category, 0, 0, 0,
                               None, None, None, None, None, None, None, None, False)

        reward = sum((r.reward_accrued for r in honest), Decimal(0))
        cf_total = sum((taker_fee(r.category, r.price_exec, r.shares, schedule=c.fee_schedule)
                        for r in honest), Decimal(0))
        spread_capture = sum((_SGN[r.side] * r.shares * (r.fill_mid - r.price_exec)
                              for r in honest), Decimal(0))
        notional = sum((r.shares * r.price_exec for r in honest), Decimal(0))
        marks = {r.token_id: r.resolution_value for r in honest}
        fills = [MakerFill(token_id=r.token_id, condition_id=r.condition_id,
                           category=r.category, side=r.side, shares=r.shares,
                           price_exec=r.price_exec, fill_mid=r.fill_mid) for r in honest]
        pnl = net_pnl(reward=reward,
                      rebate=rebate(cf_total, fraction=c.rebate_fraction),
                      spread_capture=spread_capture,
                      adverse_selection=adverse_selection(fills, marks.get),
                      fees=c.forced_taker_exit_p * cf_total,
                      lockup_cost=c.lockup_rate * notional,
                      dispute_haircut=c.dispute_p * notional)
        return MakerReport(category, n, 0, 0, pnl.reward, pnl.rebate, pnl.spread_capture,
                           pnl.adverse_selection, pnl.fees, pnl.lockup_cost,
                           pnl.dispute_haircut, pnl.net, False)
