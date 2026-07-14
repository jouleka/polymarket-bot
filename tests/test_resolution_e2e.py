"""POL-15 whole-slice resolution and settlement verification."""

from decimal import Decimal

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.errors import ConditionAlreadyTerminal
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionStore


def _terminal(condition_byte, *, payout=PayoutVector((3, 1), 4)):
    return TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + condition_byte * 32,
            ("101", "202"), "politics",
        ),
        payout=payout,
        dispute=DisputeState.CLEAR,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=(
            "99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",
        ),
        provider_ids=("archive-a", "archive-b"),
    )


def test_fractional_terminal_fans_out_crash_safely_to_all_real_ledgers(tmp_path):
    terminal = _terminal("91")
    empty_terminal = _terminal("92", payout=PayoutVector((1, 0), 1))
    condition_id = terminal.subject.condition_id
    empty_condition_id = empty_terminal.subject.condition_id
    stamper = MonotonicStamper()

    with (
        ResolutionStore(str(tmp_path / "resolution.db"), stamper) as store,
        ForecastLedger(str(tmp_path / "forecast.db"), stamper) as forecast,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        forecast.record_forecast(
            "forecast-legacy", category="politics", condition_id=condition_id,
            p=Decimal("0.5"), market_mid=Decimal("0.5"),
        )
        forecast.record_forecast(
            "forecast-canonical", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0,
            sibling_token_ids=("101", "202"),
        )
        maker.record_fill(
            "maker-legacy", token_id="legacy", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"),
        )
        maker.record_fill(
            "maker-canonical", token_id="202", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=1,
            sibling_token_ids=("101", "202"),
        )
        shadow.record_trade(
            "shadow-legacy", token_id="legacy", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"),
        )
        shadow.record_trade(
            "shadow-canonical", token_id="101", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
            sibling_token_ids=("101", "202"),
        )

        store.accept_terminal(terminal)
        store.accept_terminal(empty_terminal)
        dispatcher = ResolutionDispatcher(store, forecast, maker, shadow)

        def crash_after_forecast(record, changed):
            assert record.role == "FORECAST"
            assert changed == 1
            raise RuntimeError("crash after real forecast commit")

        dispatcher._after_apply = crash_after_forecast
        with pytest.raises(RuntimeError, match="real forecast commit"):
            dispatcher.drain(6)
        assert [record.sequence for record in store.pending_outbox(10)] == [
            1, 2, 3, 4, 5, 6,
        ]

        dispatcher._after_apply = lambda record, changed: None
        assert dispatcher.drain(6) == 6
        assert store.pending_outbox(10) == ()

        forecasts = {row.forecast_id: row for row in forecast.all()}
        assert forecasts["forecast-canonical"].resolution_status == "VOID"
        assert forecasts["forecast-canonical"].resolution_value == Decimal("0.75")
        assert forecasts["forecast-canonical"].resolution_numerator == 3
        assert forecasts["forecast-canonical"].resolution_denominator == 4
        assert forecasts["forecast-canonical"].terminal_id == terminal.terminal_id
        assert forecasts["forecast-legacy"].resolution_status is None
        assert forecasts["forecast-legacy"].terminal_id is None

        fills = {row.fill_id: row for row in maker.all()}
        assert fills["maker-canonical"].status == "SETTLED"
        assert fills["maker-canonical"].resolution_value == Decimal("0.25")
        assert fills["maker-canonical"].resolution_numerator == 1
        assert fills["maker-canonical"].resolution_denominator == 4
        assert fills["maker-canonical"].terminal_id == terminal.terminal_id
        assert fills["maker-legacy"].status is None
        assert fills["maker-legacy"].terminal_id is None

        trades = {row.trade_id: row for row in shadow.all()}
        assert trades["shadow-canonical"].status == "SETTLED"
        assert trades["shadow-canonical"].resolution_value == Decimal("0.75")
        assert trades["shadow-canonical"].resolution_numerator == 3
        assert trades["shadow-canonical"].resolution_denominator == 4
        assert trades["shadow-canonical"].terminal_id == terminal.terminal_id
        assert trades["shadow-legacy"].status is None
        assert trades["shadow-legacy"].terminal_id is None

        with pytest.raises(ConditionAlreadyTerminal):
            forecast.record_forecast(
                "late-forecast", category="politics",
                condition_id=empty_condition_id, p=Decimal("0.7"),
                market_mid=Decimal("0.6"), event_id="event-1", token_id="101",
                outcome_slot=0, sibling_token_ids=("101", "202"),
            )
        with pytest.raises(ConditionAlreadyTerminal):
            maker.record_fill(
                "late-maker", token_id="101", condition_id=empty_condition_id,
                category="politics", side="BUY", shares=Decimal("2"),
                price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )
        with pytest.raises(ConditionAlreadyTerminal):
            shadow.record_trade(
                "late-shadow", token_id="101", condition_id=empty_condition_id,
                category="politics", side="BUY", shares=Decimal("2"),
                fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )
