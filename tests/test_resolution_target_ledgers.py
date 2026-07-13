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


def _terminal(condition_id):
    return TerminalResolution(
        subject=ResolutionSubject("event-1", condition_id, ("101", "202"), "politics"),
        payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR,
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
@pytest.mark.parametrize("corruption", ["terminal_id", "resolution_value", "missing_receipt"])
def test_target_terminal_replay_validates_settled_rows(tmp_path, kind, corruption):
    condition_id = "0x" + "52" * 32
    terminal = _terminal(condition_id)
    stamper = MonotonicStamper()
    if kind == "forecast":
        ledger = ForecastLedger(str(tmp_path / f"{kind}-{corruption}.db"), stamper)
        ledger.record_forecast(
            "row", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key = "forecasts", "forecast_id"
    elif kind == "maker":
        ledger = MakerLedger(str(tmp_path / f"{kind}-{corruption}.db"), stamper)
        ledger.record_fill(
            "row", token_id="101", condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), price_exec=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key = "maker_fills", "fill_id"
    else:
        ledger = ShadowLedger(str(tmp_path / f"{kind}-{corruption}.db"), stamper)
        ledger.record_trade(
            "row", token_id="101", condition_id=condition_id, category="politics",
            side="BUY", shares=Decimal("10"), fill_price=Decimal("0.4"),
            fill_mid=Decimal("0.4"), reward_accrued=Decimal("0"), event_id="event-1",
            outcome_slot=0, sibling_token_ids=("101", "202"),
        )
        table, key = "shadow_trades", "trade_id"
    try:
        assert ledger.apply_terminal(terminal) == 1
        if corruption == "resolution_value":
            ledger._conn.execute(
                f"UPDATE {table} SET resolution_value='0' WHERE {key}='row'"
            )
        else:
            ledger._conn.execute(
                f"UPDATE {table} SET terminal_id='different' WHERE {key}='row'"
            )
            if corruption == "missing_receipt":
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
