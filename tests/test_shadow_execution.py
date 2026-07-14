"""POL-16 best-bid maker planner, dispatcher, and mark wiring."""

from dataclasses import replace
from decimal import Decimal

import pytest

from polybot.ers.intent_store import PendingIntent, ShadowExecutionRecord
from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.ers.validator import Decision
from polybot.harness.execution import make_shadow_execution_planner
from polybot.ingestion.orderbook import LocalBook
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.reward import reward_accrual


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
