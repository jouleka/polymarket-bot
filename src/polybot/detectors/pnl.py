"""Realized-PnL reconstruction from the on-chain cash-flow ledger (S7 / POL-9).

The truth layer for "how much has this wallet actually made": realized PnL from the immutable
cash-flow events, bucketed per conditionId, exact Decimal. NEVER use /leaderboard PnL -- it is
mark-to-market and auto-redemption deletes winners from /positions, so it is corrupted. Pure.
"""

from dataclasses import dataclass
from decimal import Decimal

# Cash inflows (+) vs outflows (-) for a long-the-outcome wallet.
_INFLOW = {"SELL", "REDEEM", "MERGE", "REWARD"}
_OUTFLOW = {"BUY", "SPLIT"}
_VALID_KINDS = _INFLOW | _OUTFLOW


@dataclass(frozen=True)
class CashFlow:
    kind: str          # BUY | SELL | SPLIT | MERGE | REDEEM | REWARD
    condition_id: str
    usd: Decimal


def _signed(flow):
    if flow.kind in _INFLOW:
        return flow.usd
    if flow.kind in _OUTFLOW:
        return -flow.usd
    raise ValueError(f"unknown cash-flow kind {flow.kind!r}; expected one of {sorted(_VALID_KINDS)}")


def realized_pnl(cash_flows, market_value=None):
    """PnL = SELL + REDEEM + MERGE + REWARD - BUY - SPLIT + current market value of open positions."""
    total = sum((_signed(f) for f in cash_flows), Decimal(0))
    if market_value:
        total += sum(market_value.values(), Decimal(0))
    return total


def pnl_by_condition(cash_flows, market_value=None):
    """Per-conditionId realized PnL (each market's own cash flows + its open market value)."""
    by = {}
    for flow in cash_flows:
        by[flow.condition_id] = by.get(flow.condition_id, Decimal(0)) + _signed(flow)
    for condition_id, value in (market_value or {}).items():
        by[condition_id] = by.get(condition_id, Decimal(0)) + value
    return by
