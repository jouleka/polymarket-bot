"""S8 / POL-10 — maker fill/settlement ledger (append-only, restart-stable, dispute-honest)."""

import sqlite3
from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.maker.ledger import MakerFillRecord, MakerLedger


def _ledger(path):
    return MakerLedger(path, MonotonicStamper())


def _fill(ledger, fid, *, token="t1", cond="c1", category="politics", side="BUY",
          shares="10", price_exec="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_fill(fid, token_id=token, condition_id=cond, category=category,
                              side=side, shares=Decimal(shares),
                              price_exec=Decimal(price_exec), fill_mid=Decimal(fill_mid),
                              reward_accrued=Decimal(reward))


def test_maker_v0_database_migrates_to_nullable_identity(tmp_path):
    path = str(tmp_path / "maker-v0.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE maker_fills (
            fill_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, condition_id TEXT NOT NULL,
            category TEXT NOT NULL, side TEXT NOT NULL, shares TEXT NOT NULL,
            price_exec TEXT NOT NULL, fill_mid TEXT NOT NULL, reward_accrued TEXT NOT NULL,
            created_at INTEGER NOT NULL, status TEXT, resolution_value TEXT, settled_at INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO maker_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "t1", "c1", "politics", "BUY", "10", "0.48", "0.50", "0.25",
         123, "WON", "1", 456),
    )
    conn.commit()
    conn.close()

    with _ledger(path) as ledger:
        columns = {
            row[1] for row in ledger._conn.execute("PRAGMA table_info(maker_fills)").fetchall()
        }
        assert columns - {
            "fill_id", "token_id", "condition_id", "category", "side", "shares",
            "price_exec", "fill_mid", "reward_accrued", "created_at", "status",
            "resolution_value", "settled_at",
        } == {
            "event_id", "outcome_slot", "sibling_token_ids", "resolution_numerator",
            "resolution_denominator", "terminal_id",
        }
        assert ledger.all() == [MakerFillRecord(
            "legacy", "t1", "c1", "politics", "BUY", Decimal("10"), Decimal("0.48"),
            Decimal("0.50"), Decimal("0.25"), 123, "WON", Decimal("1"), 456,
            None, None, None, None, None, None,
        )]


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


def test_rejects_an_invalid_settlement_status(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        with pytest.raises(ValueError, match="status"):
            l.record_settlement("f1", status="MAYBE", resolution_value=None)


def test_settling_an_unknown_fill_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(KeyError):
            l.record_settlement("nope", status="WON", resolution_value=Decimal("1"))


def test_won_and_lost_require_a_finite_in_range_resolution_value(tmp_path):
    # canonically 1/0 but any finite settle mark in [0,1] is accepted; None/NaN/1.5 are not.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        for bad in (None, Decimal("NaN"), Decimal("1.5")):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("f1", status="WON", resolution_value=bad)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("f1", status="LOST", resolution_value=None)


def test_disputed_and_void_require_resolution_value_none(tmp_path):
    # DISPUTED/VOID are excluded from the net sample -- a value here is a caller bug.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        for status in ("DISPUTED", "VOID"):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("f1", status=status, resolution_value=Decimal("0.5"))


def test_dispute_reflip_to_won_requires_a_fresh_resolution_value(tmp_path):
    # after a DISPUTED flip clears the stale value, a re-flip back to WON must supply
    # a FRESH value -- None cannot silently leak the cleared stale one.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("f1", status="DISPUTED", resolution_value=None)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("f1", status="WON", resolution_value=None)
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON" and r.resolution_value == Decimal("1")


def test_record_fill_rejects_a_bad_side(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(ValueError, match="side"):
            _fill(l, "f1", side="HOLD")


def test_record_fill_rejects_non_positive_or_non_finite_shares(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        for bad in ("0", "-5", "NaN", "Infinity"):
            with pytest.raises(ValueError, match="shares"):
                _fill(l, "f1", shares=bad)


def test_record_fill_rejects_out_of_range_prices(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(ValueError, match="price_exec"):
            _fill(l, "f1", price_exec="1.5")
        with pytest.raises(ValueError, match="price_exec"):
            _fill(l, "f1", price_exec="-0.1")
        with pytest.raises(ValueError, match="fill_mid"):
            _fill(l, "f1", fill_mid="NaN")


def test_record_fill_rejects_negative_or_non_finite_reward_accrued(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        for bad in ("-0.01", "NaN"):
            with pytest.raises(ValueError, match="reward_accrued"):
                _fill(l, "f1", reward=bad)
        assert l.all() == []  # nothing garbage entered the no-backfill store
