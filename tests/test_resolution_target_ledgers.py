"""Cross-ledger POL-15 target authority fences."""

import sqlite3
from decimal import Decimal

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger
from polybot.maker.ledger import MakerLedger
from polybot.resolution.errors import SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)


def _terminal(condition_id, *, payout=None, dispute=DisputeState.CLEAR):
    return TerminalResolution(
        subject=ResolutionSubject("event-1", condition_id, ("101", "202"), "politics"),
        payout=PayoutVector((1, 0), 1) if payout is None else payout,
        dispute=dispute,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=("99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",),
        provider_ids=("archive-a", "archive-b"),
    )


@pytest.mark.parametrize(("ledger_type", "ddl"), [
    (ForecastLedger, """
        CREATE TABLE forecasts (
            forecast_id TEXT PRIMARY KEY, category TEXT NOT NULL, condition_id TEXT NOT NULL,
            p TEXT NOT NULL, market_mid TEXT NOT NULL, created_at INTEGER NOT NULL,
            resolution_status TEXT, resolved_at INTEGER
        )
    """),
    (MakerLedger, """
        CREATE TABLE maker_fills (
            fill_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, condition_id TEXT NOT NULL,
            category TEXT NOT NULL, side TEXT NOT NULL, shares TEXT NOT NULL,
            price_exec TEXT NOT NULL, fill_mid TEXT NOT NULL, reward_accrued TEXT NOT NULL,
            created_at INTEGER NOT NULL, status TEXT, resolution_value TEXT, settled_at INTEGER
        )
    """),
    (ShadowLedger, """
        CREATE TABLE shadow_trades (
            trade_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, condition_id TEXT NOT NULL,
            category TEXT NOT NULL, side TEXT NOT NULL, shares TEXT NOT NULL,
            fill_price TEXT NOT NULL, fill_mid TEXT NOT NULL, reward_accrued TEXT NOT NULL,
            created_at INTEGER NOT NULL, status TEXT, resolution_value TEXT, settled_at INTEGER
        )
    """),
])
def test_target_ledgers_use_full_synchronous_durability(tmp_path, ledger_type, ddl):
    for generation in ("fresh", "migrated"):
        path = str(tmp_path / f"{ledger_type.__name__}-{generation}.db")
        if generation == "migrated":
            conn = sqlite3.connect(path)
            conn.execute(ddl)
            conn.commit()
            conn.close()
        with ledger_type(path, MonotonicStamper()) as ledger:
            assert ledger._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert ledger._conn.execute("PRAGMA synchronous").fetchone()[0] == 2


@pytest.mark.parametrize(("kind", "corrupt_column"), [
    ("forecast", "event_id"),
    ("forecast", "terminal_id"),
    ("maker", "event_id"),
    ("maker", "terminal_id"),
    ("shadow", "event_id"),
    ("shadow", "terminal_id"),
])
def test_target_row_decoders_reject_mixed_identity(tmp_path, kind, corrupt_column):
    stamper = MonotonicStamper()
    if kind == "forecast":
        ledger = ForecastLedger(str(tmp_path / f"{kind}-{corrupt_column}.db"), stamper)
        ledger.record_forecast(
            "row", category="politics", condition_id="legacy",
            p=Decimal("0.7"), market_mid=Decimal("0.6"),
        )
        table, key, read = "forecasts", "forecast_id", lambda: ledger.get("row")
    elif kind == "maker":
        ledger = MakerLedger(str(tmp_path / f"{kind}-{corrupt_column}.db"), stamper)
        ledger.record_fill(
            "row", token_id="legacy", condition_id="legacy", category="politics",
            side="BUY", shares=Decimal("10"), price_exec=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"),
        )
        table, key, read = "maker_fills", "fill_id", ledger.all
    else:
        ledger = ShadowLedger(str(tmp_path / f"{kind}-{corrupt_column}.db"), stamper)
        ledger.record_trade(
            "row", token_id="legacy", condition_id="legacy", category="politics",
            side="BUY", shares=Decimal("10"), fill_price=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"),
        )
        table, key, read = "shadow_trades", "trade_id", ledger.all
    try:
        ledger._conn.execute(
            f"UPDATE {table} SET {corrupt_column}=? WHERE {key}='row'",
            ("event-1" if corrupt_column == "event_id" else "terminal",),
        )
        ledger._conn.commit()
        with pytest.raises(SettlementConflict, match="identity|state"):
            read()
    finally:
        ledger.close()


@pytest.mark.parametrize("kind", ["forecast", "maker", "shadow"])
@pytest.mark.parametrize("sibling_json", ['{"101":0,"202":0}', '["101", "202"]'])
def test_target_decoders_require_exact_canonical_sibling_array(
        tmp_path, kind, sibling_json):
    condition_id = "0x" + "51" * 32
    terminal = _terminal(condition_id)
    stamper = MonotonicStamper()
    if kind == "forecast":
        ledger = ForecastLedger(str(tmp_path / f"{kind}.db"), stamper)
        ledger.record_forecast(
            "row", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key, read = "forecasts", "forecast_id", lambda: ledger.get("row")
    elif kind == "maker":
        ledger = MakerLedger(str(tmp_path / f"{kind}.db"), stamper)
        ledger.record_fill(
            "row", token_id="101", condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), price_exec=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key, read = "maker_fills", "fill_id", ledger.all
    else:
        ledger = ShadowLedger(str(tmp_path / f"{kind}.db"), stamper)
        ledger.record_trade(
            "row", token_id="101", condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), fill_price=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key, read = "shadow_trades", "trade_id", ledger.all
    try:
        ledger._conn.execute(
            f"UPDATE {table} SET sibling_token_ids=? WHERE {key}='row'", (sibling_json,)
        )
        ledger._conn.commit()
        with pytest.raises(SettlementConflict, match="canonical identity"):
            read()
        with pytest.raises(SettlementConflict, match="canonical identity"):
            ledger.apply_terminal(terminal)
    finally:
        ledger.close()


@pytest.mark.parametrize("kind", ["forecast", "maker", "shadow"])
@pytest.mark.parametrize("corruption", [
    "terminal_id", "status", "resolution_value", "resolution_numerator",
    "resolution_denominator", "settled_at", "missing_receipt",
])
@pytest.mark.parametrize("terminal_case", [
    "binary", "fractional", "disputed", "manual", "fractional_disputed",
    "fractional_manual", "half", "half_disputed", "half_manual",
])
@pytest.mark.parametrize("outcome_slot", [0, 1])
def test_target_terminal_replay_validates_settled_rows(
        tmp_path, kind, corruption, terminal_case, outcome_slot):
    condition_id = "0x" + "52" * 32
    if terminal_case.startswith("fractional"):
        dispute = {
            "fractional_disputed": DisputeState.DISPUTED,
            "fractional_manual": DisputeState.MANUAL,
        }.get(terminal_case, DisputeState.CLEAR)
        terminal = _terminal(
            condition_id, payout=PayoutVector((1, 2), 3), dispute=dispute
        )
    elif terminal_case.startswith("half"):
        dispute = {
            "half_disputed": DisputeState.DISPUTED,
            "half_manual": DisputeState.MANUAL,
        }.get(terminal_case, DisputeState.CLEAR)
        terminal = _terminal(
            condition_id, payout=PayoutVector((1, 1), 2), dispute=dispute
        )
    elif terminal_case == "disputed":
        terminal = _terminal(condition_id, dispute=DisputeState.DISPUTED)
    elif terminal_case == "manual":
        terminal = _terminal(condition_id, dispute=DisputeState.MANUAL)
    else:
        terminal = _terminal(condition_id)
    stamper = MonotonicStamper()
    if kind == "forecast":
        ledger = ForecastLedger(
            str(tmp_path / f"{kind}-{terminal_case}-{outcome_slot}-{corruption}.db"), stamper
        )
        ledger.record_forecast(
            "row", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id=terminal.subject.token_ids[outcome_slot], outcome_slot=outcome_slot,
            sibling_token_ids=terminal.subject.token_ids,
        )
        table, key = "forecasts", "forecast_id"
        status_column, settled_column = "resolution_status", "resolved_at"
    elif kind == "maker":
        ledger = MakerLedger(
            str(tmp_path / f"{kind}-{terminal_case}-{outcome_slot}-{corruption}.db"), stamper
        )
        ledger.record_fill(
            "row", token_id=terminal.subject.token_ids[outcome_slot],
            condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), price_exec=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=outcome_slot, sibling_token_ids=terminal.subject.token_ids,
        )
        table, key = "maker_fills", "fill_id"
        status_column, settled_column = "status", "settled_at"
    else:
        ledger = ShadowLedger(
            str(tmp_path / f"{kind}-{terminal_case}-{outcome_slot}-{corruption}.db"), stamper
        )
        ledger.record_trade(
            "row", token_id=terminal.subject.token_ids[outcome_slot],
            condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), fill_price=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=outcome_slot, sibling_token_ids=terminal.subject.token_ids,
        )
        table, key = "shadow_trades", "trade_id"
        status_column, settled_column = "status", "settled_at"
    try:
        assert ledger.apply_terminal(terminal) == 1
        if corruption == "resolution_value":
            ledger._conn.execute(
                f"UPDATE {table} SET resolution_value='9' WHERE {key}='row'"
            )
        elif corruption == "status":
            ledger._conn.execute(
                f"UPDATE {table} SET {status_column}='BROKEN' WHERE {key}='row'"
            )
        elif corruption in ("resolution_numerator", "resolution_denominator"):
            ledger._conn.execute(
                f"UPDATE {table} SET {corruption}='9' WHERE {key}='row'"
            )
        elif corruption == "settled_at":
            ledger._conn.execute(
                f"UPDATE {table} SET {settled_column}=NULL WHERE {key}='row'"
            )
        elif corruption == "terminal_id":
            ledger._conn.execute(
                f"UPDATE {table} SET terminal_id='different' WHERE {key}='row'"
            )
        else:
            ledger._conn.execute(
                "DELETE FROM resolution_receipts WHERE condition_id=?", (condition_id,)
            )
        ledger._conn.commit()

        with pytest.raises(SettlementConflict, match="settled|terminal|receipt|projection"):
            ledger.apply_terminal(terminal)
        expected_receipts = 0 if corruption == "missing_receipt" else 1
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM resolution_receipts"
        ).fetchone()[0] == expected_receipts
    finally:
        ledger.close()


def test_legacy_settlement_mutators_reject_canonical_pending_rows(tmp_path):
    stamper = MonotonicStamper()

    forecast_condition = "0x" + "41" * 32
    with ForecastLedger(str(tmp_path / "forecast.db"), stamper) as ledger:
        ledger.record_forecast(
            "legacy", category="politics", condition_id="legacy-f",
            p=Decimal("0.7"), market_mid=Decimal("0.6"),
        )
        ledger.record_resolution("legacy", "WON")
        ledger.record_forecast(
            "canonical", category="politics", condition_id=forecast_condition,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        with pytest.raises(SettlementConflict):
            ledger.record_resolution("canonical", "WON")
        ledger.apply_terminal(_terminal(forecast_condition))
        with pytest.raises(SettlementConflict):
            ledger.record_resolution("canonical", "LOST")

    maker_condition = "0x" + "42" * 32
    maker_values = dict(
        token_id="101", category="politics", side="BUY", shares=Decimal("10"),
        price_exec=Decimal("0.48"), fill_mid=Decimal("0.50"),
        reward_accrued=Decimal("0.25"),
    )
    with MakerLedger(str(tmp_path / "maker.db"), stamper) as ledger:
        ledger.record_fill("legacy", condition_id="legacy-m", **maker_values)
        ledger.record_settlement("legacy", status="WON", resolution_value=Decimal("1"))
        ledger.record_fill(
            "canonical", condition_id=maker_condition, **maker_values, event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        with pytest.raises(SettlementConflict):
            ledger.record_settlement(
                "canonical", status="WON", resolution_value=Decimal("1")
            )
        ledger.apply_terminal(_terminal(maker_condition))
        with pytest.raises(SettlementConflict):
            ledger.record_settlement(
                "canonical", status="LOST", resolution_value=Decimal("0")
            )

    shadow_condition = "0x" + "43" * 32
    shadow_values = dict(
        token_id="101", category="politics", side="BUY", shares=Decimal("10"),
        fill_price=Decimal("0.48"), fill_mid=Decimal("0.50"),
        reward_accrued=Decimal("0.25"),
    )
    with ShadowLedger(str(tmp_path / "shadow.db"), stamper) as ledger:
        ledger.record_trade("legacy", condition_id="legacy-s", **shadow_values)
        ledger.record_settlement("legacy", status="WON", resolution_value=Decimal("1"))
        ledger.record_trade(
            "canonical", condition_id=shadow_condition, **shadow_values, event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        with pytest.raises(SettlementConflict):
            ledger.record_settlement(
                "canonical", status="WON", resolution_value=Decimal("1")
            )
        ledger.apply_terminal(_terminal(shadow_condition))
        with pytest.raises(SettlementConflict):
            ledger.record_settlement(
                "canonical", status="LOST", resolution_value=Decimal("0")
            )
