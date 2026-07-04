"""S9 / POL-11 — shadow trade ledger (append-only, restart-stable, dispute-honest)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger


def _ledger(path, stamper=None):
    return ShadowLedger(path, stamper or MonotonicStamper())


def _trade(ledger, tid, *, token="t1", cond="c1", category="politics", side="BUY",
           shares="10", fill_price="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_trade(tid, token_id=token, condition_id=cond, category=category,
                               side=side, shares=Decimal(shares),
                               fill_price=Decimal(fill_price), fill_mid=Decimal(fill_mid),
                               reward_accrued=Decimal(reward))


def test_record_trade_round_trips_every_field_via_all(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        assert _trade(l, "d1") is True
        rows = l.all()
        assert len(rows) == 1
        r = rows[0]
        assert r.trade_id == "d1" and r.token_id == "t1" and r.condition_id == "c1"
        assert r.category == "politics" and r.side == "BUY"
        # exact Decimal round-trip (stored as exact strings)
        assert r.shares == Decimal("10") and r.fill_price == Decimal("0.48")
        assert r.fill_mid == Decimal("0.50") and r.reward_accrued == Decimal("0.25")
        assert r.created_at is not None
        assert r.status is None and r.resolution_value is None and r.settled_at is None
