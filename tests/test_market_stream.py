"""Tests for the CLOB market-channel dispatcher (POL-3 / S1)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_stream import MarketStream


def _book(asset_id, bids, asks):
    return {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def _price_change(asset_id, price, side, size):
    return {
        "event_type": "price_change",
        "asset_id": asset_id,
        "changes": [{"price": price, "side": side, "size": size}],
    }


def test_ingest_book_builds_per_asset_local_book():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    book = stream.book_for("A")
    assert book.best_bid() == Decimal("0.60")
    assert book.best_ask() == Decimal("0.62")


def test_ingest_routes_price_change_to_the_correct_asset():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_book("B", [("0.40", "100")], [("0.45", "100")]))

    stream.ingest(_price_change("A", "0.61", "BUY", "50"))

    assert stream.book_for("A").best_bid() == Decimal("0.61")
    assert stream.book_for("B").best_bid() == Decimal("0.40")  # untouched


def test_ingest_stamps_strictly_increasing_observed_at():
    stream = MarketStream(MonotonicStamper(clock=lambda: 5))  # frozen clock

    first = stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    second = stream.ingest(_book("A", [("0.61", "100")], [("0.63", "100")]))

    assert first < second


def test_ingest_emits_observation_to_sink():
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 7), sink=seen.append)

    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    assert len(seen) == 1
    observation = seen[0]
    assert observation.asset_id == "A"
    assert observation.event_type == "book"
    assert observation.observed_at == 7


def test_ingest_skips_known_benign_event_types():
    # last_trade_price / tick_size_change are documented market-channel events we
    # don't book yet — skip them, don't HALT (and don't create a book for them).
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    result = stream.ingest({"event_type": "last_trade_price", "asset_id": "A"})

    assert result is None
    assert stream.book_for("A") is None


def test_ingest_truly_unknown_event_type_fails_loud():
    # An unrecognized schema is a format change -> HALT (auto-halt stance).
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    with pytest.raises(ValueError, match="event_type"):
        stream.ingest({"event_type": "wat", "asset_id": "A"})


def test_mark_all_stale_marks_every_book():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_book("B", [("0.40", "100")], [("0.45", "100")]))
    assert not stream.book_for("A").is_stale()

    stream.mark_all_stale()

    assert stream.book_for("A").is_stale()
    assert stream.book_for("B").is_stale()
