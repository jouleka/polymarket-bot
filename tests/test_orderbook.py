"""Tests for local order-book reconstruction (POL-3 / S1, CLOB WS core)."""

from decimal import Decimal

from polybot.ingestion.orderbook import LocalBook


def _snapshot(bids, asks, asset_id="123"):
    return {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def test_book_snapshot_sets_best_bid_ask_and_midpoint():
    book = LocalBook()

    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.58", "200")],
                              asks=[("0.62", "150"), ("0.65", "50")]))

    assert book.best_bid() == Decimal("0.60")
    assert book.best_ask() == Decimal("0.62")
    assert book.midpoint() == Decimal("0.61")


def test_price_change_adds_a_new_best_level():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "150")]))

    book.apply_price_change(
        {"asset_id": "123", "changes": [{"price": "0.61", "side": "BUY", "size": "80"}]}
    )

    assert book.best_bid() == Decimal("0.61")


def test_price_change_with_zero_size_removes_a_level():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.61", "50")], asks=[("0.62", "150")]))

    book.apply_price_change(
        {"asset_id": "123", "changes": [{"price": "0.61", "side": "BUY", "size": "0"}]}
    )

    assert book.best_bid() == Decimal("0.60")


def test_snapshot_ignores_zero_size_levels():
    book = LocalBook()

    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.99", "0")],
                              asks=[("0.62", "150")]))

    # The 0.99/0 phantom must not become the best bid.
    assert book.best_bid() == Decimal("0.60")


def test_midpoint_is_none_on_a_crossed_book():
    # best_bid >= best_ask means the book is crossed/locked (transient fast move
    # or corruption) — not a price to size off. Refuse to invent a midpoint.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.65", "100")], asks=[("0.60", "100")]))

    assert book.midpoint() is None


def test_book_is_stale_until_first_snapshot():
    # Without a full snapshot baseline the book can't be trusted for sizing.
    assert LocalBook().is_stale()


def test_book_is_fresh_after_a_snapshot():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "100")]))

    assert not book.is_stale()


def test_price_change_before_a_snapshot_stays_stale():
    book = LocalBook()
    book.apply_price_change(
        {"asset_id": "A", "changes": [{"price": "0.61", "side": "BUY", "size": "50"}]}
    )

    assert book.is_stale()  # deltas without a baseline are not a trustworthy book


def test_stale_book_yields_no_midpoint_but_keeps_levels():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "100")]))
    book.mark_stale()

    assert book.is_stale()
    assert book.midpoint() is None        # ERS must not size off an unverified book
    assert book.best_bid() == Decimal("0.60")  # last-known levels stay for diagnostics


def test_book_snapshot_replaces_previous_state():
    # A re-requested snapshot after a reconnect must fully resync, not merge.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.50", "100")], asks=[("0.70", "100")]))

    book.apply_book(_snapshot(bids=[("0.60", "10")], asks=[("0.62", "10")]))

    assert book.best_bid() == Decimal("0.60")
    assert book.best_ask() == Decimal("0.62")
