"""POL-16 best-bid maker planner, dispatcher, and mark wiring."""

from dataclasses import replace
from decimal import Decimal

import pytest

from polybot.ers.intent_store import PendingIntent, ShadowExecutionRecord
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.ers.validator import Decision
from polybot.harness.execution import (
    ShadowExecutionDispatcher,
    make_mark_for,
    make_shadow_execution_planner,
)
from polybot.harness.ledger import ShadowLedger
from polybot.ingestion.orderbook import LocalBook
from polybot.core.clock import MonotonicStamper
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.inventory import MakerFill, adverse_selection
from polybot.maker.ledger import MakerLedger
from polybot.maker.reward import reward_accrual
from polybot.resolution.errors import ConditionAlreadyTerminal, SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)


def _book(*, bid="0.48", ask="0.52"):
    book = LocalBook()
    book.apply_book(
        {
            "bids": [] if bid is None else [{"price": bid, "size": "100"}],
            "asks": [] if ask is None else [{"price": ask, "size": "100"}],
        }
    )
    return book


def _intent(**overrides):
    intent = PendingIntent(
        intent_id="intent-1",
        status="PROPOSED",
        token_id="101",
        condition_id="0x" + "11" * 32,
        event_id="event-1",
        side="SELL",
        target_price=Decimal("0.01"),
        max_price=Decimal("0.90"),
        size_usd_suggestion=Decimal("999"),
        p=Decimal("0.80"),
        p_confidence=Decimal("0.75"),
        resolution_summary="",
        thesis="",
        citations=(),
        created_at=1,
    )
    return replace(intent, **overrides)


def _subject(**overrides):
    values = dict(
        event_id="event-1",
        condition_id="0x" + "11" * 32,
        category="politics",
        token_id="101",
        outcome_slot=0,
        sibling_token_ids=("101", "202"),
    )
    values.update(overrides)
    return ResolutionSubjectMetadata(**values)


def _config():
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _enqueue_execution(store, execution=None):
    if execution is None:
        execution = ShadowExecutionRecord(
            execution_id="intent-1", token_id="101",
            condition_id="0x" + "11" * 32, event_id="event-1",
            category="politics", outcome_slot=0, sibling_token_ids=("101", "202"),
            side="BUY", shares=Decimal("25"), price_exec=Decimal("0.48"),
            fill_mid=Decimal("0.50"), reward_accrued=Decimal("2.50"),
        )
    store.propose_trade(
        execution.execution_id, token_id=execution.token_id,
        condition_id=execution.condition_id, event_id=execution.event_id,
        side="SELL", target_price="0.01", max_price="0.90",
        size_usd_suggestion="999", p="0.80", p_confidence="0.75",
    )
    store.record_decision(
        execution.execution_id,
        Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "cap"),
        shadow_execution=execution,
    )
    return execution


def _terminal(*, dispute=DisputeState.CLEAR, payout=PayoutVector((1, 0), 1)):
    return TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + "11" * 32, ("101", "202"), "politics"
        ),
        payout=payout,
        dispute=dispute,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=("99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",),
        provider_ids=("archive-a", "archive-b"),
    )


def test_planner_uses_fresh_best_bid_forced_buy_and_ers_approved_notional():
    book = _book()
    planner = make_shadow_execution_planner(
        book_for=lambda token_id: book,
        subject_for=lambda intent: _subject(),
        maker_config=_config(),
    )

    execution = planner(
        _intent(),
        Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
    )

    assert isinstance(execution, ShadowExecutionRecord)
    assert execution.execution_id == "intent-1"
    assert execution.side == "BUY"  # proposal said SELL; selected outcome tokens are always bought
    assert execution.price_exec == Decimal("0.48")  # best bid, not target 0.01 or ask 0.52
    assert execution.shares == Decimal("25")  # approved $12 / maker price $0.48
    assert execution.fill_mid == Decimal("0.50")
    assert execution.reward_accrued == reward_accrual(
        Decimal("25"), Decimal("0.02"), config=_config()
    )
    assert execution.event_id == "event-1"
    assert execution.outcome_slot == 0
    assert execution.sibling_token_ids == ("101", "202")


@pytest.mark.parametrize(
    "book",
    [
        None,
        _book(bid=None),
        _book(bid="0.55", ask="0.45"),
        _book(bid="0", ask="0.52"),
    ],
)
def test_planner_returns_none_for_missing_or_unusable_maker_book(book):
    planner = make_shadow_execution_planner(
        book_for=lambda token_id: book,
        subject_for=lambda intent: _subject(),
        maker_config=_config(),
    )
    assert planner(
        _intent(), Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap")
    ) is None


def test_planner_returns_none_when_freshly_refetched_book_turns_stale():
    stale = _book()
    stale.mark_stale()
    books = iter((stale, _book(bid="0.47", ask="0.53")))
    planner = make_shadow_execution_planner(
        book_for=lambda token_id: next(books),
        subject_for=lambda intent: _subject(),
        maker_config=_config(),
    )
    decision = Decision("ACCEPT", Decimal("9.40"), Decimal("0.52"), "per_trade_cap")

    assert planner(_intent(), decision) is None
    assert planner(_intent(intent_id="intent-2"), decision).price_exec == Decimal("0.47")


@pytest.mark.parametrize(
    "decision",
    [
        Decision("REJECT", None, None, "no_book"),
        Decision("ACCEPT", None, Decimal("0.52"), "bad"),
        Decision("ACCEPT", Decimal("0"), Decimal("0.52"), "bad"),
        Decision("ACCEPT", Decimal("NaN"), Decimal("0.52"), "bad"),
    ],
)
def test_planner_fails_loud_on_non_accept_or_bad_approved_stake(decision):
    planner = make_shadow_execution_planner(
        book_for=lambda token_id: _book(),
        subject_for=lambda intent: _subject(),
        maker_config=_config(),
    )
    with pytest.raises(ValueError):
        planner(_intent(), decision)


@pytest.mark.parametrize(
    "subject",
    [
        object(),
        _subject(event_id="wrong-event"),
        _subject(condition_id="0x" + "22" * 32),
        _subject(token_id="202", outcome_slot=1),
    ],
)
def test_planner_rejects_noncanonical_or_mismatched_subject(subject):
    planner = make_shadow_execution_planner(
        book_for=lambda token_id: _book(),
        subject_for=lambda intent: subject,
        maker_config=_config(),
    )
    with pytest.raises((TypeError, ValueError), match="subject"):
        planner(
            _intent(),
            Decision("ACCEPT", Decimal("12"), Decimal("0.52"), "per_trade_cap"),
        )


def test_dispatcher_projects_exact_canonical_execution_to_both_ledgers(tmp_path):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        _enqueue_execution(store)

        assert ShadowExecutionDispatcher(store, maker, shadow).drain(2) == 2

        assert store.pending_shadow_executions(10) == ()
        maker_row = maker.all()[0]
        shadow_row = shadow.all()[0]
        assert maker_row.fill_id == shadow_row.trade_id == "intent-1"
        assert maker_row.token_id == shadow_row.token_id == "101"
        assert maker_row.condition_id == shadow_row.condition_id == "0x" + "11" * 32
        assert maker_row.event_id == shadow_row.event_id == "event-1"
        assert maker_row.category == shadow_row.category == "politics"
        assert maker_row.outcome_slot == shadow_row.outcome_slot == 0
        assert maker_row.sibling_token_ids == shadow_row.sibling_token_ids == ("101", "202")
        assert maker_row.side == shadow_row.side == "BUY"
        assert maker_row.shares == shadow_row.shares == Decimal("25")
        assert maker_row.price_exec == shadow_row.fill_price == Decimal("0.48")
        assert maker_row.fill_mid == shadow_row.fill_mid == Decimal("0.50")
        assert maker_row.reward_accrued == shadow_row.reward_accrued == Decimal("2.50")


def test_dispatcher_crash_after_maker_commit_replays_then_reaches_shadow(tmp_path):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        _enqueue_execution(store)
        dispatcher = ShadowExecutionDispatcher(store, maker, shadow)

        def crash(record, changed):
            assert record.role == "MAKER"
            assert changed is True
            raise RuntimeError("crash after maker commit")

        dispatcher._after_apply = crash
        with pytest.raises(RuntimeError, match="maker commit"):
            dispatcher.drain(2)
        assert len(maker.all()) == 1
        assert shadow.all() == []
        assert [record.role for record in store.pending_shadow_executions(10)] == [
            "MAKER", "SHADOW",
        ]

        replay = []
        dispatcher._after_apply = lambda record, changed: replay.append((record.role, changed))
        assert dispatcher.drain(2) == 2
        assert replay == [("MAKER", False), ("SHADOW", True)]
        assert len(maker.all()) == len(shadow.all()) == 1
        assert store.pending_shadow_executions(10) == ()


def test_dispatcher_does_not_acknowledge_contradictory_duplicate(tmp_path):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        execution = _enqueue_execution(store)
        maker.record_fill(
            execution.execution_id, token_id=execution.token_id,
            condition_id=execution.condition_id, category=execution.category,
            side=execution.side, shares=Decimal("999"), price_exec=execution.price_exec,
            fill_mid=execution.fill_mid, reward_accrued=execution.reward_accrued,
            event_id=execution.event_id, outcome_slot=execution.outcome_slot,
            sibling_token_ids=execution.sibling_token_ids,
        )

        with pytest.raises(SettlementConflict, match="contradicts shadow execution"):
            ShadowExecutionDispatcher(store, maker, shadow).drain(2)
        assert [record.role for record in store.pending_shadow_executions(10)] == [
            "MAKER", "SHADOW",
        ]
        assert shadow.all() == []


def test_terminal_before_execution_replay_inserts_exact_already_settled_rows(tmp_path):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        execution = _enqueue_execution(store)
        terminal = _terminal()
        assert maker.apply_terminal(terminal) == 0
        assert shadow.apply_terminal(terminal) == 0

        with pytest.raises(ConditionAlreadyTerminal):
            maker.record_fill(
                "legacy-late", token_id="101", condition_id=execution.condition_id,
                category="politics", side="BUY", shares=Decimal("1"),
                price_exec=Decimal("0.48"), fill_mid=Decimal("0.50"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )
        with pytest.raises(ConditionAlreadyTerminal):
            shadow.record_trade(
                "legacy-late", token_id="101", condition_id=execution.condition_id,
                category="politics", side="BUY", shares=Decimal("1"),
                fill_price=Decimal("0.48"), fill_mid=Decimal("0.50"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )

        assert ShadowExecutionDispatcher(store, maker, shadow).drain(2) == 2
        maker_row = maker.all()[0]
        shadow_row = shadow.all()[0]
        assert maker_row.status == shadow_row.status == "WON"
        assert maker_row.resolution_value == shadow_row.resolution_value == Decimal("1")
        assert maker_row.resolution_numerator == shadow_row.resolution_numerator == 1
        assert maker_row.resolution_denominator == shadow_row.resolution_denominator == 1
        assert maker_row.terminal_id == shadow_row.terminal_id == terminal.terminal_id


@pytest.mark.parametrize(
    ("terminal", "status", "value", "numerator", "denominator"),
    [
        (_terminal(payout=PayoutVector((3, 1), 4)), "SETTLED", Decimal("0.75"), 3, 4),
        (_terminal(dispute=DisputeState.DISPUTED), "DISPUTED", None, 1, 1),
    ],
)
def test_terminal_race_preserves_fractional_and_dispute_authority(
        tmp_path, terminal, status, value, numerator, denominator):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        _enqueue_execution(store)
        maker.apply_terminal(terminal)
        shadow.apply_terminal(terminal)

        assert ShadowExecutionDispatcher(store, maker, shadow).drain(2) == 2
        for row in (maker.all()[0], shadow.all()[0]):
            assert row.status == status
            assert row.resolution_value == value
            assert row.resolution_numerator == numerator
            assert row.resolution_denominator == denominator
            assert row.terminal_id == terminal.terminal_id


def test_terminal_race_subject_contradiction_stays_pending_and_loud(tmp_path):
    stamper = MonotonicStamper()
    with (
        IntentStore(str(tmp_path / "intent.db"), stamper) as store,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        _enqueue_execution(store)
        wrong = replace(
            _terminal(),
            subject=ResolutionSubject(
                "wrong-event", "0x" + "11" * 32, ("101", "202"), "politics"
            ),
        )
        maker.apply_terminal(wrong)

        with pytest.raises(SettlementConflict, match="terminal subject contradicts"):
            ShadowExecutionDispatcher(store, maker, shadow).drain(2)
        assert [record.role for record in store.pending_shadow_executions(10)] == [
            "MAKER", "SHADOW",
        ]


def test_mark_uses_live_midpoint_until_terminal_then_terminal_value_dominates(tmp_path):
    stamper = MonotonicStamper()
    with MakerLedger(str(tmp_path / "maker.db"), stamper) as maker:
        execution = ShadowExecutionRecord(
            execution_id="intent-1", token_id="101",
            condition_id="0x" + "11" * 32, event_id="event-1",
            category="politics", outcome_slot=0, sibling_token_ids=("101", "202"),
            side="BUY", shares=Decimal("25"), price_exec=Decimal("0.48"),
            fill_mid=Decimal("0.50"), reward_accrued=Decimal("2.50"),
        )
        maker.apply_shadow_execution(execution)
        mark_for = make_mark_for(maker, book_for=lambda token_id: _book(bid="0.40", ask="0.60"))
        assert mark_for("101") == Decimal("0.50")

        maker.apply_terminal(_terminal())
        terminal_mark = make_mark_for(
            maker,
            book_for=lambda token_id: (_ for _ in ()).throw(
                AssertionError("terminal mark must not consult live book")
            ),
        )
        assert terminal_mark("101") == Decimal("1")


def test_mark_returns_none_for_unknown_missing_stale_and_disputed_data(tmp_path):
    stamper = MonotonicStamper()
    with ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow:
        execution = ShadowExecutionRecord(
            execution_id="intent-1", token_id="101",
            condition_id="0x" + "11" * 32, event_id="event-1",
            category="politics", outcome_slot=0, sibling_token_ids=("101", "202"),
            side="BUY", shares=Decimal("25"), price_exec=Decimal("0.48"),
            fill_mid=Decimal("0.50"), reward_accrued=Decimal("2.50"),
        )
        shadow.apply_shadow_execution(execution)

        assert make_mark_for(
            shadow,
            book_for=lambda token_id: (_ for _ in ()).throw(
                AssertionError("unknown token must not consult book")
            ),
        )("unknown") is None
        assert make_mark_for(shadow, book_for=lambda token_id: None)("101") is None
        stale = _book()
        stale.mark_stale()
        assert make_mark_for(shadow, book_for=lambda token_id: stale)("101") is None

        shadow.apply_terminal(_terminal(dispute=DisputeState.DISPUTED))
        assert make_mark_for(
            shadow,
            book_for=lambda token_id: (_ for _ in ()).throw(
                AssertionError("disputed terminal must not consult book")
            ),
        )("101") is None


def test_fractional_terminal_mark_and_live_mark_feed_adverse_selection(tmp_path):
    stamper = MonotonicStamper()
    with MakerLedger(str(tmp_path / "maker.db"), stamper) as maker:
        execution = ShadowExecutionRecord(
            execution_id="intent-1", token_id="101",
            condition_id="0x" + "11" * 32, event_id="event-1",
            category="politics", outcome_slot=0, sibling_token_ids=("101", "202"),
            side="BUY", shares=Decimal("10"), price_exec=Decimal("0.48"),
            fill_mid=Decimal("0.50"), reward_accrued=Decimal("0"),
        )
        maker.apply_shadow_execution(execution)
        mark_for = make_mark_for(maker, book_for=lambda token_id: _book(bid="0.40", ask="0.50"))
        fills = [
            MakerFill(
                token_id="101", condition_id=execution.condition_id, category="politics",
                side="BUY", shares=Decimal("10"), price_exec=Decimal("0.48"),
                fill_mid=Decimal("0.50"),
            )
        ]
        assert mark_for("101") == Decimal("0.45")
        assert adverse_selection(fills, mark_for) == Decimal("0.30")

        maker.apply_terminal(_terminal(payout=PayoutVector((3, 1), 4)))
        assert mark_for("101") == Decimal("0.75")


def test_mark_fails_loud_on_contradictory_terminal_rows_for_one_token(tmp_path):
    stamper = MonotonicStamper()
    with MakerLedger(str(tmp_path / "maker.db"), stamper) as maker:
        first = ShadowExecutionRecord(
            execution_id="intent-1", token_id="101",
            condition_id="0x" + "11" * 32, event_id="event-1",
            category="politics", outcome_slot=0, sibling_token_ids=("101", "202"),
            side="BUY", shares=Decimal("1"), price_exec=Decimal("0.48"),
            fill_mid=Decimal("0.50"), reward_accrued=Decimal("0"),
        )
        second = replace(
            first,
            execution_id="intent-2",
            condition_id="0x" + "22" * 32,
            event_id="event-2",
            sibling_token_ids=("101", "303"),
        )
        maker.apply_shadow_execution(first)
        maker.apply_shadow_execution(second)
        maker.apply_terminal(_terminal())
        maker.apply_terminal(
            replace(
                _terminal(payout=PayoutVector((0, 1), 1)),
                subject=ResolutionSubject(
                    "event-2", "0x" + "22" * 32, ("101", "303"), "politics"
                ),
            )
        )

        with pytest.raises(SettlementConflict, match="contradictory terminal shadow marks"):
            make_mark_for(maker, book_for=lambda token_id: _book())("101")
