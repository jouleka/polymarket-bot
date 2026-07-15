"""POL-16 whole-slice: ERS ACCEPT → crash-safe fills → POL-15 settlement → marks."""

from decimal import Decimal

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.harness.execution import (
    ShadowExecutionDispatcher,
    make_mark_for,
    make_shadow_execution_planner,
)
from polybot.harness.ledger import ShadowLedger
from polybot.ingestion.orderbook import LocalBook
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionStore


def _book():
    book = LocalBook()
    book.apply_book(
        {
            "bids": [{"price": "0.48", "size": "1000"}],
            "asks": [{"price": "0.52", "size": "1000"}],
        }
    )
    return book


def _subject():
    return ResolutionSubjectMetadata(
        event_id="event-1",
        condition_id="0x" + "11" * 32,
        category="politics",
        token_id="101",
        outcome_slot=0,
        sibling_token_ids=("101", "202"),
    )


def _terminal():
    return TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + "11" * 32, ("101", "202"), "politics"
        ),
        payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=("99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",),
        provider_ids=("archive-a", "archive-b"),
    )


def test_pol16_whole_slice_is_crash_safe_settleable_and_terminal_marked(tmp_path):
    stamper = MonotonicStamper()
    book = _book()
    maker_config = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as intents,
        ForecastLedger(str(tmp_path / "forecast.db"), stamper) as forecast,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
        ResolutionStore(str(tmp_path / "resolution.db"), stamper) as resolutions,
    ):
        intents.propose_trade(
            "intent-1",
            token_id="101",
            condition_id="0x" + "11" * 32,
            event_id="event-1",
            side="SELL",                 # untrusted and deliberately wrong
            target_price="0.01",         # untrusted and deliberately too favorable
            max_price="0.90",
            size_usd_suggestion="999",  # untrusted and deliberately oversized
            p="0.90",
            p_confidence="0.75",
        )
        planner = make_shadow_execution_planner(
            book_for=lambda token_id: book,
            subject_for=lambda intent: _subject(),
            maker_config=maker_config,
        )
        signer = PaperSigner()

        portfolio = process_pending(
            intents,
            book_for=lambda token_id: book,
            portfolio=Portfolio(nav=Decimal("300")),
            caps=RiskCaps(),
            signer=signer,
            shadow_planner=planner,
        )

        assert intents.get("intent-1").status == "ACCEPTED"
        assert portfolio.positions[0].worst_case_risk == Decimal("12")
        pending = intents.pending_shadow_executions(10)
        assert pending[0].execution.side == "BUY"
        assert pending[0].execution.price_exec == Decimal("0.48")
        assert pending[0].execution.shares == Decimal("25")

        executions = ShadowExecutionDispatcher(intents, maker, shadow)

        def crash_after_maker(record, changed):
            assert record.role == "MAKER" and changed is True
            raise RuntimeError("injected crash after maker commit")

        executions._after_apply = crash_after_maker
        with pytest.raises(RuntimeError, match="injected crash"):
            executions.drain(2)
        executions._after_apply = lambda record, changed: None
        assert executions.drain(2) == 2
        assert intents.pending_shadow_executions(10) == ()

        maker_row = maker.all()[0]
        shadow_row = shadow.all()[0]
        assert maker_row.fill_id == shadow_row.trade_id == "intent-1"
        assert maker_row.sibling_token_ids == shadow_row.sibling_token_ids == ("101", "202")
        assert make_mark_for(maker, book_for=lambda token_id: book)("101") == Decimal("0.50")
        assert make_mark_for(shadow, book_for=lambda token_id: book)("101") == Decimal("0.50")

        subject = _subject()
        forecast.record_forecast(
            "intent-1", category=subject.category, condition_id=subject.condition_id,
            p=Decimal("0.70"), market_mid=Decimal("0.50"), event_id=subject.event_id,
            token_id=subject.token_id, outcome_slot=subject.outcome_slot,
            sibling_token_ids=subject.sibling_token_ids,
        )
        terminal = _terminal()
        assert resolutions.accept_terminal(terminal) is True
        assert ResolutionDispatcher(resolutions, forecast, maker, shadow).drain(3) == 3

        assert maker.all()[0].status == shadow.all()[0].status == "WON"
        assert maker.all()[0].resolution_value == shadow.all()[0].resolution_value == Decimal("1")
        terminal_only = lambda token_id: (_ for _ in ()).throw(
            AssertionError("settled mark must not consult live book")
        )
        assert make_mark_for(maker, book_for=terminal_only)("101") == Decimal("1")
        assert make_mark_for(shadow, book_for=terminal_only)("101") == Decimal("1")
