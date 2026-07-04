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


def test_record_trade_is_idempotent_on_trade_id(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        assert _trade(l, "d1") is True
        assert _trade(l, "d1", fill_price="0.99") is False  # duplicate ignored
        assert l.all()[0].fill_price == Decimal("0.48")     # original preserved


class _FixedClock:
    """A non-monotonic clock stub: returns the SAME tick every call so the stamper's
    strict-monotonic bump is bypassed only across DISTINCT stampers -- used to force two
    settlements onto the SAME settled_at and expose the rowid tiebreak in settled()."""
    def __init__(self, tick):
        self._tick = tick
    def __call__(self):
        return self._tick


def test_settlement_sets_status_value_and_settled_at(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON"
        assert r.resolution_value == Decimal("1")
        assert r.settled_at is not None
        assert [x.trade_id for x in l.settled()] == ["d1"]


def test_unsettled_trades_are_excluded_from_settled(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        _trade(l, "d2")
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled()] == ["d1"]


def test_settled_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1", category="politics")
        _trade(l, "d2", category="sports")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d2", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled(category="sports")] == ["d2"]


def test_settled_orders_by_settled_at_then_rowid(tmp_path):
    # settle d2 (inserted second) BEFORE d1 -> d2 gets the earlier settled_at, so
    # settled() must return d2 first even though d1 has the lower rowid.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        _trade(l, "d2")
        l.record_settlement("d2", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled()] == ["d2", "d1"]


def test_settled_tiebreaks_on_rowid_when_settled_at_is_equal(tmp_path):
    # a fixed clock -> both settlements share the SAME settled_at; the tiebreak is rowid,
    # so insertion order (d1 then d2) wins even though d2 was settled first.
    stamper = MonotonicStamper(clock=_FixedClock(500))
    with _ledger(str(tmp_path / "s.db"), stamper=stamper) as l:
        # NB: MonotonicStamper still bumps on <=, so drive the two settlements through two
        # separate stampers pinned to the same tick to guarantee equal settled_at.
        _trade(l, "d1")
        _trade(l, "d2")
        l._stamper = MonotonicStamper(clock=_FixedClock(700))
        l.record_settlement("d2", status="WON", resolution_value=Decimal("1"))
        l._stamper = MonotonicStamper(clock=_FixedClock(700))
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.settled_at for x in l.settled()] == [700, 700]
        assert [x.trade_id for x in l.settled()] == ["d1", "d2"]  # rowid tiebreak


def test_settlement_overwrites_on_a_dispute_flip(tmp_path):
    # a whale-captured UMA dispute can flip an apparent WON to DISPUTED later; the flip
    # must also CLEAR the stale resolution value.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="DISPUTED", resolution_value=None)
        r = l.all()[0]
        assert r.status == "DISPUTED" and r.resolution_value is None


def test_dispute_reflip_to_won_requires_a_fresh_resolution_value(tmp_path):
    # after a DISPUTED flip clears the stale value, a re-flip back to WON must supply a
    # FRESH value -- None cannot silently leak the cleared stale one.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="DISPUTED", resolution_value=None)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("d1", status="WON", resolution_value=None)
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON" and r.resolution_value == Decimal("1")


def test_rejects_an_invalid_settlement_status(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        with pytest.raises(ValueError, match="status"):
            l.record_settlement("d1", status="MAYBE", resolution_value=None)


def test_settling_an_unknown_trade_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(KeyError):
            l.record_settlement("nope", status="WON", resolution_value=Decimal("1"))


def test_won_and_lost_require_a_finite_in_range_resolution_value(tmp_path):
    # canonically 1/0 but any finite settle mark in [0,1] is accepted; None/NaN/1.5 are not.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        for bad in (None, Decimal("NaN"), Decimal("1.5")):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("d1", status="WON", resolution_value=bad)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("d1", status="LOST", resolution_value=None)


def test_disputed_and_void_require_resolution_value_none(tmp_path):
    # DISPUTED/VOID are excluded from the net sample -- a value here is a caller bug.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        for status in ("DISPUTED", "VOID"):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("d1", status=status, resolution_value=Decimal("0.5"))
