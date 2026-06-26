"""S7 / POL-9 — realized-PnL reconstruction from the on-chain cash-flow ledger (exact Decimal)."""

from decimal import Decimal

import pytest

from polybot.detectors.pnl import CashFlow, pnl_by_condition, realized_pnl


def _cf(kind, cond, usd):
    return CashFlow(kind=kind, condition_id=cond, usd=Decimal(str(usd)))


# c1: SELL 150 - BUY 100 = +50 ; c2: REDEEM 200 - BUY 80 = +120 ;
# c3: MERGE 30 + REWARD 5 - SPLIT 50 (+ open market value 40) = +25
_FLOWS = [
    _cf("BUY", "c1", 100), _cf("SELL", "c1", 150),
    _cf("BUY", "c2", 80), _cf("REDEEM", "c2", 200),
    _cf("SPLIT", "c3", 50), _cf("MERGE", "c3", 30), _cf("REWARD", "c3", 5),
]
_MV = {"c3": Decimal("40")}


def test_realized_pnl_matches_a_hand_computed_wallet():
    assert realized_pnl(_FLOWS, _MV) == Decimal("195")  # 50 + 120 + 25


def test_pnl_by_condition_buckets_per_market():
    by = pnl_by_condition(_FLOWS, _MV)
    assert by["c1"] == Decimal("50")
    assert by["c2"] == Decimal("120")
    assert by["c3"] == Decimal("25")


def test_without_market_value_it_is_realized_only():
    # c3 drops the +40 open value -> -15; total = 50 + 120 - 15 = 155.
    assert realized_pnl(_FLOWS) == Decimal("155")


def test_rejects_an_unknown_cash_flow_kind():
    with pytest.raises(ValueError, match="kind"):
        realized_pnl([_cf("GIFT", "c1", 10)])
