"""Windowed net-of-everything PnL (S9 / POL-11).

THE honest after-all-costs figure for an arbitrary time-WINDOW of settled shadow trades --
the ONLY PnL the evidence evaluator reads (no gross accessor; the S8 "never reward-gross"
spine). Re-derives the exact same seven-leg fold as ``MakerTracker.report_for``
(net = reward + rebate + spread_capture - adverse_selection - fees - lockup_cost -
dispute_haircut) over a LIST of rows rather than the whole ledger, so S9 windows the OOS
slice without refactoring (or importing the internals of) the S8 tracker. Honest by
construction: DISPUTED/VOID rows are SKIPPED (whale-flip immunity), an empty honest window
is Decimal(0), and two honest rows on one token with divergent resolution marks fail LOUD
(ledger corruption, mirroring the S8d divergent-marks pin). Reuses the S8 primitives
(taker_fee/rebate/adverse_selection/net_pnl) unchanged.
"""

from decimal import Decimal

from polybot.maker.fees import rebate, taker_fee
from polybot.maker.inventory import _SGN, MakerFill, adverse_selection
from polybot.maker.netpnl import net_pnl

_HONEST = ("WON", "LOST", "SETTLED")


def window_net(rows, *, maker_config):
    """The S8 net identity over ``rows`` (a list of settled ShadowTradeRecords). Honest
    WON/LOST/SETTLED only -- DISPUTED/VOID skipped. Empty honest window -> Decimal(0). Fails LOUD on
    an unhandled status or on divergent resolution marks for one token."""
    c = maker_config
    honest = []
    for r in rows:
        if r.status in _HONEST:
            honest.append(r)
        elif r.status in ("DISPUTED", "VOID"):
            continue
        else:
            # Exhaustive: an unknown status is corruption -- fail loud, never silently vanish
            # from the accounting (mirrors MakerTracker).
            raise ValueError(f"unhandled settlement status {r.status!r}")

    if not honest:  # no honest settled sample in this window
        return Decimal(0)

    reward = sum((r.reward_accrued for r in honest), Decimal(0))
    cf_total = sum((taker_fee(r.category, r.fill_price, r.shares, schedule=c.fee_schedule)
                    for r in honest), Decimal(0))
    spread_capture = sum((_SGN[r.side] * r.shares * (r.fill_mid - r.fill_price)
                          for r in honest), Decimal(0))
    notional = sum((r.shares * r.fill_price for r in honest), Decimal(0))
    marks = {}
    for r in honest:
        # A token resolves ONCE: two honest rows on one token_id with DISTINCT non-None
        # resolution marks is corruption -- fail loud, never silently last-wins.
        prior = marks.get(r.token_id)
        if (prior is not None and r.resolution_value is not None
                and prior != r.resolution_value):
            raise ValueError(f"inconsistent resolution marks for token {r.token_id!r}: "
                             f"{prior} vs {r.resolution_value}")
        marks[r.token_id] = r.resolution_value
    fills = [MakerFill(token_id=r.token_id, condition_id=r.condition_id,
                       category=r.category, side=r.side, shares=r.shares,
                       price_exec=r.fill_price, fill_mid=r.fill_mid) for r in honest]
    return net_pnl(reward=reward,
                   rebate=rebate(cf_total, fraction=c.rebate_fraction),
                   spread_capture=spread_capture,
                   adverse_selection=adverse_selection(fills, marks.get),
                   fees=c.forced_taker_exit_p * cf_total,
                   lockup_cost=c.lockup_rate * notional,
                   dispute_haircut=c.dispute_p * notional).net
