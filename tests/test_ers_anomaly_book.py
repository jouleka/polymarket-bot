"""S4.4c -- L5 abnormal-book checks (DESIGN-S4.4-ANOMALY.md §3 trigger 1).

Driven purely through positions + book_for with REAL LocalBook instances; the monitor
is constructed bare (caps + clock only) because these checks need no seam.
"""

from decimal import Decimal

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_ABNORMAL_BOOK
from polybot.ers.validator import OpenPosition
from polybot.ingestion.orderbook import LocalBook


def _monitor():
    """Bare monitor: caps + clock only (0-arg monotonic-SECONDS clock, injected)."""
    return AnomalyMonitor(RiskCaps(), clock=lambda: 0.0)


def _pos(token_id, *, frozen=False):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal("8"), matrix_cold=False, token_id=token_id,
                        entry_price=Decimal("0.50"), frozen=frozen)


def _book(*, bid=None, ask=None, bid_size="500", ask_size="500"):
    """Fresh LocalBook from one full snapshot (apply_book marks it NON-stale).
    None on a side = that side empty."""
    bids = [{"price": bid, "size": bid_size}] if bid is not None else []
    asks = [{"price": ask, "size": ask_size}] if ask is not None else []
    book = LocalBook()
    book.apply_book({"bids": bids, "asks": asks})
    return book


def test_non_stale_crossed_book_fires_l5_abnormal_book():
    # Kills: deleting the structural midpoint()-is-None check entirely.
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")  # bid > ask -> crossed -> midpoint None
    assert book.is_stale() is False and book.midpoint() is None  # precondition sanity
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_locked_book_fires_l5_abnormal_book():
    # Kills: weakening the LocalBook contract's bid >= ask to bid > ask (locked = bid == ask).
    mon = _monitor()
    book = _book(bid="0.50", ask="0.50")
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_empty_ask_side_fires_l5_abnormal_book():
    # Kills: only checking crossed prices and skipping the empty-side midpoint-None case.
    mon = _monitor()
    book = _book(bid="0.40")  # asks empty; apply_book still marks the book non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_stale_crossed_book_does_not_fire_stale_is_breaker_domain():
    # Kills: dropping the is_stale() gate (stale books belong to validator book_stale /
    # breaker stale_mark, NOT L5 -- design §0 'abnormal-book checks run on NON-stale books only').
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    book.mark_stale()
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_frozen_position_book_is_still_checked_and_fires():
    # Kills: copying the breaker's 'if pos.frozen: continue' -- anomaly checks book
    # STRUCTURE, frozen positions still have books (pinned contract: skip frozen? NO).
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    state = mon.evaluate([_pos("t1", frozen=True)], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_healthy_book_fires_nothing_action_none_triggers_empty():
    # Kills: inverting the midpoint()-is-None condition (firing on every VALID book).
    mon = _monitor()
    book = _book(bid="0.49", ask="0.51")  # mid 0.50, both sides present, non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_missing_book_none_is_skipped_silently():
    # Kills: treating an ABSENT book as abnormal (book None = validator no_book domain),
    # or calling methods on None (AttributeError would escape evaluate).
    mon = _monitor()
    state = mon.evaluate([_pos("t1")], lambda token: None)
    assert state.action == NONE
    assert state.triggers == ()
