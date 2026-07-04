"""S8 / POL-10 — maker fill/settlement ledger (append-only, restart-stable, dispute-honest)."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.maker.ledger import MakerLedger


def _ledger(path):
    return MakerLedger(path, MonotonicStamper())


def _fill(ledger, fid, *, token="t1", cond="c1", category="politics", side="BUY",
          shares="10", price_exec="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_fill(fid, token_id=token, condition_id=cond, category=category,
                              side=side, shares=Decimal(shares),
                              price_exec=Decimal(price_exec), fill_mid=Decimal(fill_mid),
                              reward_accrued=Decimal(reward))


def test_record_fill_round_trips_every_field_via_all(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        assert _fill(l, "f1") is True
        rows = l.all()
        assert len(rows) == 1
        r = rows[0]
        assert r.fill_id == "f1" and r.token_id == "t1" and r.condition_id == "c1"
        assert r.category == "politics" and r.side == "BUY"
        # exact Decimal round-trip (stored as exact strings)
        assert r.shares == Decimal("10") and r.price_exec == Decimal("0.48")
        assert r.fill_mid == Decimal("0.50") and r.reward_accrued == Decimal("0.25")
        assert r.created_at is not None
        assert r.status is None and r.resolution_value is None and r.settled_at is None


def test_record_fill_is_idempotent_on_fill_id(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        assert _fill(l, "f1") is True
        assert _fill(l, "f1", price_exec="0.99") is False  # duplicate ignored
        assert l.all()[0].price_exec == Decimal("0.48")    # original preserved


def test_settlement_sets_status_value_and_settled_at(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON"
        assert r.resolution_value == Decimal("1")
        assert r.settled_at is not None
        assert [x.fill_id for x in l.settled()] == ["f1"]


def test_unsettled_fills_are_excluded_from_settled(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        _fill(l, "f2")
        l.record_settlement("f1", status="LOST", resolution_value=Decimal("0"))
        assert [x.fill_id for x in l.settled()] == ["f1"]


def test_settled_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1", category="politics")
        _fill(l, "f2", category="sports")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("f2", status="LOST", resolution_value=Decimal("0"))
        assert [x.fill_id for x in l.settled(category="sports")] == ["f2"]


def test_settlement_overwrites_on_a_dispute_flip(tmp_path):
    # a whale-captured UMA dispute can flip an apparent WON to DISPUTED later;
    # the flip must also CLEAR the stale resolution value.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("f1", status="DISPUTED", resolution_value=None)
        r = l.all()[0]
        assert r.status == "DISPUTED" and r.resolution_value is None


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "m.db")
    with _ledger(path) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
    with _ledger(path) as l2:
        r = l2.all()[0]
        assert r.shares == Decimal("10") and r.price_exec == Decimal("0.48")
        assert r.status == "WON" and r.resolution_value == Decimal("1")
