"""S9 / POL-11 — shadow trade ledger (append-only, restart-stable, dispute-honest)."""

import sqlite3
from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger, ShadowTradeRecord
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.errors import SettlementConflict


def _ledger(path, stamper=None):
    return ShadowLedger(path, stamper or MonotonicStamper())


def _trade(ledger, tid, *, token="t1", cond="c1", category="politics", side="BUY",
           shares="10", fill_price="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_trade(tid, token_id=token, condition_id=cond, category=category,
                               side=side, shares=Decimal(shares),
                               fill_price=Decimal(fill_price), fill_mid=Decimal(fill_mid),
                               reward_accrued=Decimal(reward))


def _terminal(condition_id, payout, *, dispute=DisputeState.CLEAR):
    return TerminalResolution(
        subject=ResolutionSubject("event-1", condition_id, ("101", "202"), "politics"),
        payout=payout,
        dispute=dispute,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=("99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",),
        provider_ids=("archive-a", "archive-b"),
    )


def test_shadow_terminal_projects_clear_and_excluded_values(tmp_path):
    clear_condition = "0x" + "31" * 32
    clear = _terminal(clear_condition, PayoutVector((1, 2), 3))
    with _ledger(str(tmp_path / "clear.db")) as ledger:
        ledger.record_trade(
            "fractional", token_id="101", condition_id=clear_condition,
            category="politics", side="BUY", shares=Decimal("10"),
            fill_price=Decimal("0.48"), fill_mid=Decimal("0.50"),
            reward_accrued=Decimal("0.25"), event_id="event-1", outcome_slot=0,
            sibling_token_ids=("101", "202"),
        )
        assert ledger.apply_terminal(clear) == 1
        record = ledger.all()[0]
        assert record.status == "SETTLED"
        assert str(record.resolution_value) == "0." + "3" * 78
        assert (record.resolution_numerator, record.resolution_denominator) == (1, 3)
        assert record.terminal_id == clear.terminal_id

    for byte, dispute in (("32", DisputeState.DISPUTED), ("33", DisputeState.MANUAL)):
        condition_id = "0x" + byte * 32
        terminal = _terminal(condition_id, PayoutVector((3, 1), 4), dispute=dispute)
        with _ledger(str(tmp_path / f"{dispute.value}.db")) as ledger:
            ledger.record_trade(
                dispute.value, token_id="101", condition_id=condition_id,
                category="politics", side="BUY", shares=Decimal("10"),
                fill_price=Decimal("0.48"), fill_mid=Decimal("0.50"),
                reward_accrued=Decimal("0.25"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )
            assert ledger.apply_terminal(terminal) == 1
            record = ledger.all()[0]
            assert record.status == "DISPUTED" and record.resolution_value is None
            assert record.terminal_id == terminal.terminal_id


def test_shadow_terminal_conflict_rolls_back_every_row_and_receipt(tmp_path):
    condition_id = "0x" + "34" * 32
    terminal = _terminal(condition_id, PayoutVector((1, 0), 1))
    with _ledger(str(tmp_path / "conflict.db")) as ledger:
        for trade_id, event_id, slot, token in (
            ("first", "event-1", 0, "101"),
            ("later-conflict", "wrong-event", 1, "202"),
        ):
            ledger.record_trade(
                trade_id, token_id=token, condition_id=condition_id, category="politics",
                side="BUY", shares=Decimal("10"), fill_price=Decimal("0.48"),
                fill_mid=Decimal("0.50"), reward_accrued=Decimal("0.25"),
                event_id=event_id, outcome_slot=slot, sibling_token_ids=("101", "202"),
            )

        with pytest.raises(SettlementConflict, match="identity"):
            ledger.apply_terminal(terminal)

        assert all(row.status is None and row.terminal_id is None for row in ledger.all())
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM resolution_receipts"
        ).fetchone()[0] == 0


def test_shadow_v0_database_migrates_to_nullable_identity(tmp_path):
    path = str(tmp_path / "shadow-v0.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE shadow_trades (
            trade_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, condition_id TEXT NOT NULL,
            category TEXT NOT NULL, side TEXT NOT NULL, shares TEXT NOT NULL,
            fill_price TEXT NOT NULL, fill_mid TEXT NOT NULL, reward_accrued TEXT NOT NULL,
            created_at INTEGER NOT NULL, status TEXT, resolution_value TEXT, settled_at INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO shadow_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "t1", "c1", "politics", "BUY", "10", "0.48", "0.50", "0.25",
         123, "WON", "1", 456),
    )
    conn.commit()
    conn.close()

    with _ledger(path) as ledger:
        columns = {
            row[1] for row in ledger._conn.execute("PRAGMA table_info(shadow_trades)").fetchall()
        }
        assert columns - {
            "trade_id", "token_id", "condition_id", "category", "side", "shares",
            "fill_price", "fill_mid", "reward_accrued", "created_at", "status",
            "resolution_value", "settled_at",
        } == {
            "event_id", "outcome_slot", "sibling_token_ids", "resolution_numerator",
            "resolution_denominator", "terminal_id",
        }
        assert ledger.all() == [ShadowTradeRecord(
            "legacy", "t1", "c1", "politics", "BUY", Decimal("10"), Decimal("0.48"),
            Decimal("0.50"), Decimal("0.25"), 123, "WON", Decimal("1"), 456,
            None, None, None, None, None, None,
        )]


def test_shadow_canonical_identity_is_all_or_none_and_slot_matches_token(tmp_path):
    condition_id = "0x" + "ab" * 32
    siblings = ("11", "22")
    base = {
        "token_id": "22", "condition_id": condition_id, "category": "politics",
        "side": "BUY", "shares": Decimal("10"), "fill_price": Decimal("0.48"),
        "fill_mid": Decimal("0.50"), "reward_accrued": Decimal("0.25"),
    }
    with _ledger(str(tmp_path / "s.db")) as ledger:
        assert ledger.record_trade(
            "canonical", **base, event_id="e1", outcome_slot=1,
            sibling_token_ids=siblings,
        ) is True
        record = ledger.all()[0]
        assert (
            record.event_id, record.outcome_slot, record.sibling_token_ids
        ) == ("e1", 1, siblings)
        assert ledger._conn.execute(
            "SELECT sibling_token_ids FROM shadow_trades WHERE trade_id='canonical'"
        ).fetchone()[0] == '["11","22"]'

        for trade_id, identity in (
            ("mixed", {"event_id": "e1", "outcome_slot": 1}),
            ("wrong-slot", {
                "event_id": "e1", "outcome_slot": 0, "sibling_token_ids": siblings,
            }),
        ):
            with pytest.raises(ValueError, match="identity|slot|token"):
                ledger.record_trade(trade_id, **base, **identity)
            assert [row.trade_id for row in ledger.all()] == ["canonical"]


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


def test_record_trade_rejects_a_bad_side(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(ValueError, match="side"):
            _trade(l, "d1", side="HOLD")


def test_record_trade_rejects_non_positive_or_non_finite_shares(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        for bad in ("0", "-5", "NaN", "Infinity"):
            with pytest.raises(ValueError, match="shares"):
                _trade(l, "d1", shares=bad)


def test_record_trade_rejects_out_of_range_prices(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(ValueError, match="fill_price"):
            _trade(l, "d1", fill_price="1.5")
        with pytest.raises(ValueError, match="fill_price"):
            _trade(l, "d1", fill_price="-0.1")
        with pytest.raises(ValueError, match="fill_mid"):
            _trade(l, "d1", fill_mid="NaN")


def test_record_trade_rejects_negative_or_non_finite_reward_accrued(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        for bad in ("-0.01", "NaN"):
            with pytest.raises(ValueError, match="reward_accrued"):
                _trade(l, "d1", reward=bad)
        assert l.all() == []  # nothing garbage entered the no-backfill store


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "s.db")
    with _ledger(path) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
    with _ledger(path) as l2:
        r = l2.all()[0]
        assert r.shares == Decimal("10") and r.fill_price == Decimal("0.48")
        assert r.status == "WON" and r.resolution_value == Decimal("1")
