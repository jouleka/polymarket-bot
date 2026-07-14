"""S5 / POL-7 — forecast->outcome ledger (append-only, point-in-time, restart-stable)."""

import sqlite3
from decimal import Decimal
from threading import Event, Thread

import pytest

from polybot.calibration.ledger import ForecastLedger, ForecastRecord
from polybot.core.clock import MonotonicStamper
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.errors import ConditionAlreadyTerminal, SettlementConflict


def _ledger(path):
    return ForecastLedger(path, MonotonicStamper())


def _rec(ledger, fid, *, category="politics", p="0.7", mid="0.6", cond="c1"):
    return ledger.record_forecast(fid, category=category, condition_id=cond,
                                  p=Decimal(p), market_mid=Decimal(mid))


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


def test_signing_guard_serializes_a_competing_terminal_writer(tmp_path):
    path = str(tmp_path / "guard.db")
    attempted = Event()
    blocked = Event()
    retry = Event()
    completed = Event()
    errors = []

    def settlement_writer():
        conn = sqlite3.connect(path, timeout=0)
        try:
            attempted.set()
            try:
                conn.execute(
                    "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
                    "VALUES (?, ?, ?)", ("condition", "terminal", b"payload")
                )
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" not in str(exc):
                    raise
                blocked.set()
                if not retry.wait(2):
                    raise AssertionError("signing guard never released the settlement retry")
                conn.execute(
                    "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
                    "VALUES (?, ?, ?)", ("condition", "terminal", b"payload")
                )
                conn.commit()
            else:
                raise AssertionError("settlement committed while the signing guard was held")
        except Exception as exc:  # surfaced in the owning test thread below
            errors.append(exc)
        finally:
            conn.close()
            completed.set()

    with _ledger(path) as ledger:
        worker = Thread(target=settlement_writer)
        with ledger.signing_guard("condition"):
            worker.start()
            assert attempted.wait(2)
            assert blocked.wait(2)
            assert not completed.is_set()
        retry.set()
        worker.join(2)
        assert not worker.is_alive()
        assert errors == []
        with pytest.raises(ConditionAlreadyTerminal):
            ledger.require_condition_open("condition")


def test_forecast_clear_terminal_projects_exact_slot_value(tmp_path):
    binary_condition = "0x" + "11" * 32
    binary = _terminal(binary_condition, PayoutVector((1, 0), 1))
    with _ledger(str(tmp_path / "binary.db")) as ledger:
        for slot, token in enumerate(binary.subject.token_ids):
            ledger.record_forecast(
                f"f{slot}", category="politics", condition_id=binary_condition,
                p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
                token_id=token, outcome_slot=slot, sibling_token_ids=binary.subject.token_ids,
            )
        assert ledger.apply_terminal(binary) == 2
        won, lost = ledger.all()
        assert (won.resolution_status, won.resolution_value) == ("WON", Decimal("1"))
        assert (lost.resolution_status, lost.resolution_value) == ("LOST", Decimal("0"))
        assert (won.resolution_numerator, lost.resolution_numerator) == (1, 0)
        assert won.resolution_denominator == lost.resolution_denominator == 1
        assert won.terminal_id == lost.terminal_id == binary.terminal_id
        assert ledger._conn.execute(
            "SELECT payload FROM resolution_receipts WHERE condition_id=?",
            (binary_condition,),
        ).fetchone()[0] == binary.canonical_bytes

    fractional_condition = "0x" + "12" * 32
    fractional = _terminal(fractional_condition, PayoutVector((1, 2), 3))
    with _ledger(str(tmp_path / "fractional.db")) as ledger:
        ledger.record_forecast(
            "fractional", category="politics", condition_id=fractional_condition,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        assert ledger.apply_terminal(fractional) == 1
        record = ledger.get("fractional")
        assert record.resolution_status == "VOID"
        assert str(record.resolution_value) == "0." + "3" * 78
        assert (record.resolution_numerator, record.resolution_denominator) == (1, 3)
        assert record.terminal_id == fractional.terminal_id


def test_forecast_disputed_or_manual_terminal_is_non_economic(tmp_path):
    for byte, dispute in (("13", DisputeState.DISPUTED), ("14", DisputeState.MANUAL)):
        condition_id = "0x" + byte * 32
        terminal = _terminal(
            condition_id, PayoutVector((3, 1), 4), dispute=dispute
        )
        with _ledger(str(tmp_path / f"{dispute.value}.db")) as ledger:
            ledger.record_forecast(
                dispute.value, category="politics", condition_id=condition_id,
                p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
                token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
            )
            assert ledger.apply_terminal(terminal) == 1
            record = ledger.get(dispute.value)
            assert record.resolution_status == "DISPUTED_LOST"
            assert record.resolution_value is None
            assert record.terminal_id == terminal.terminal_id


def test_forecast_terminal_conflict_rolls_back_every_row_and_receipt(tmp_path):
    condition_id = "0x" + "15" * 32
    terminal = _terminal(condition_id, PayoutVector((1, 0), 1))
    with _ledger(str(tmp_path / "conflict.db")) as ledger:
        for forecast_id, event_id, slot, token in (
            ("first", "event-1", 0, "101"),
            ("later-conflict", "wrong-event", 1, "202"),
        ):
            ledger.record_forecast(
                forecast_id, category="politics", condition_id=condition_id,
                p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id=event_id,
                token_id=token, outcome_slot=slot, sibling_token_ids=("101", "202"),
            )

        with pytest.raises(SettlementConflict, match="identity"):
            ledger.apply_terminal(terminal)

        assert all(
            row.resolution_status is None and row.terminal_id is None
            for row in ledger.all()
        )
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM resolution_receipts"
        ).fetchone()[0] == 0


def test_forecast_zero_row_receipt_blocks_later_creation(tmp_path):
    condition_id = "0x" + "16" * 32
    terminal = _terminal(condition_id, PayoutVector((1, 0), 1))
    with _ledger(str(tmp_path / "zero.db")) as ledger:
        assert ledger.apply_terminal(terminal) == 0
        assert ledger.apply_terminal(terminal) == 0
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM resolution_receipts WHERE condition_id=?", (condition_id,)
        ).fetchone()[0] == 1

        with pytest.raises(ConditionAlreadyTerminal):
            ledger.record_forecast(
                "late", category="politics", condition_id=condition_id,
                p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
                token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
            )
        assert ledger.get("late") is None


def test_forecast_v0_database_migrates_to_nullable_identity(tmp_path):
    path = str(tmp_path / "forecast-v0.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE forecasts (
            forecast_id TEXT PRIMARY KEY, category TEXT NOT NULL,
            condition_id TEXT NOT NULL, p TEXT NOT NULL, market_mid TEXT NOT NULL,
            created_at INTEGER NOT NULL, resolution_status TEXT, resolved_at INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy", "politics", "c1", "0.7", "0.6", 123, "WON", 456),
    )
    conn.commit()
    conn.close()

    with _ledger(path) as ledger:
        columns = {
            row[1] for row in ledger._conn.execute("PRAGMA table_info(forecasts)").fetchall()
        }
        assert columns - {
            "forecast_id", "category", "condition_id", "p", "market_mid", "created_at",
            "resolution_status", "resolved_at",
        } == {
            "token_id", "event_id", "outcome_slot", "sibling_token_ids", "resolution_value",
            "resolution_numerator", "resolution_denominator", "terminal_id",
        }
        assert ledger.get("legacy") == ForecastRecord(
            "legacy", "politics", "c1", Decimal("0.7"), Decimal("0.6"), 123, "WON", 456,
            None, None, None, None, None, None, None, None,
        )


def test_forecast_canonical_identity_is_all_or_none_and_slot_matches_token(tmp_path):
    condition_id = "0x" + "ab" * 32
    siblings = ("11", "22")
    with _ledger(str(tmp_path / "f.db")) as ledger:
        assert ledger.record_forecast(
            "canonical", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="e1", token_id="22",
            outcome_slot=1, sibling_token_ids=siblings,
        ) is True
        record = ledger.get("canonical")
        assert (
            record.event_id, record.token_id, record.outcome_slot, record.sibling_token_ids
        ) == ("e1", "22", 1, siblings)
        assert ledger._conn.execute(
            "SELECT sibling_token_ids FROM forecasts WHERE forecast_id='canonical'"
        ).fetchone()[0] == '["11","22"]'

        for forecast_id, identity in (
            ("mixed", {"event_id": "e1", "token_id": "22", "outcome_slot": 1}),
            ("wrong-slot", {
                "event_id": "e1", "token_id": "22", "outcome_slot": 0,
                "sibling_token_ids": siblings,
            }),
        ):
            with pytest.raises(ValueError, match="identity|slot|token"):
                ledger.record_forecast(
                    forecast_id, category="politics", condition_id=condition_id,
                    p=Decimal("0.7"), market_mid=Decimal("0.6"), **identity,
                )
            assert ledger.get(forecast_id) is None


def test_record_and_get_round_trips(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        assert _rec(l, "f1") is True
        r = l.get("f1")
        assert r.category == "politics" and r.condition_id == "c1"
        assert r.p == Decimal("0.7") and r.market_mid == Decimal("0.6")
        assert r.resolution_status is None and r.resolved_at is None
        assert r.created_at is not None


def test_record_is_idempotent_on_forecast_id(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        assert _rec(l, "f1") is True
        assert _rec(l, "f1", p="0.9") is False  # duplicate ignored
        assert l.get("f1").p == Decimal("0.7")  # original preserved


def test_resolution_sets_status_and_appears_in_resolved(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
        r = l.get("f1")
        assert r.resolution_status == "WON" and r.resolved_at is not None
        assert [x.forecast_id for x in l.resolved()] == ["f1"]


def test_unresolved_forecasts_are_excluded_from_resolved(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        _rec(l, "f2")
        l.record_resolution("f1", "LOST")
        assert [x.forecast_id for x in l.resolved()] == ["f1"]


def test_resolved_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1", category="politics")
        _rec(l, "f2", category="sports")
        l.record_resolution("f1", "WON")
        l.record_resolution("f2", "LOST")
        assert [x.forecast_id for x in l.resolved(category="sports")] == ["f2"]


def test_rejects_an_invalid_resolution_status(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        with pytest.raises(ValueError, match="status"):
            l.record_resolution("f1", "MAYBE")


def test_resolving_an_unknown_forecast_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(KeyError):
            l.record_resolution("nope", "WON")


def test_re_resolution_overwrites_on_a_dispute_flip(tmp_path):
    # a UMA dispute can flip an apparent WON to DISPUTED_LOST later.
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
        l.record_resolution("f1", "DISPUTED_LOST")
        assert l.get("f1").resolution_status == "DISPUTED_LOST"


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "f.db")
    with _ledger(path) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
    with _ledger(path) as l2:
        assert l2.get("f1").resolution_status == "WON"


def test_rejects_a_non_finite_forecast_p(tmp_path):
    # review H1: a NaN/Inf forecast must never enter the no-backfill calibration substrate.
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(ValueError, match="p"):
            l.record_forecast("f1", category="x", condition_id="c",
                              p=Decimal("NaN"), market_mid=Decimal("0.5"))


def test_rejects_an_out_of_range_market_mid(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(ValueError, match="market_mid"):
            l.record_forecast("f1", category="x", condition_id="c",
                              p=Decimal("0.5"), market_mid=Decimal("1.5"))
