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

    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "80"}])

    assert book.best_bid() == Decimal("0.61")


def test_price_change_with_zero_size_removes_a_level():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.61", "50")], asks=[("0.62", "150")]))

    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "0"}])

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
    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "50"}])

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


# --- mid-stream sequence-gap detection (live price_change format) -------------
# Live frames (probed 2026-06-25) carry per-asset level changes as a list of
# {price, side, size, ...} dicts, each with the venue's resulting top-of-book in
# best_bid/best_ask. apply_price_change now takes that list directly; a dropped or
# misapplied delta makes our reconstructed top-of-book disagree with the venue's
# reported best_bid/best_ask -> verify_top_of_book marks the book stale (resync).


def test_apply_price_change_takes_a_list_of_level_changes():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "150")]))

    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "80"}])

    assert book.best_bid() == Decimal("0.61")


def test_apply_price_change_list_zero_size_removes_level():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.61", "50")], asks=[("0.62", "150")]))

    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "0"}])

    assert book.best_bid() == Decimal("0.60")


def test_apply_price_change_applies_multiple_levels_in_one_call():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "100")]))

    book.apply_price_change([
        {"price": "0.61", "side": "BUY", "size": "80"},
        {"price": "0.63", "side": "SELL", "size": "40"},
    ])

    assert book.best_bid() == Decimal("0.61")
    assert book.best_ask() == Decimal("0.62")  # 0.63 ask is worse than existing 0.62


def test_verify_top_of_book_consistent_keeps_book_fresh():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "100")]))
    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "50"}])

    # Venue's resulting top-of-book matches our reconstruction -> in sync.
    assert book.verify_top_of_book(best_bid="0.61", best_ask="0.62") is True
    assert not book.is_stale()
    assert book.midpoint() == Decimal("0.615")


def test_verify_top_of_book_divergence_marks_stale():
    # Models a DROPPED delta: we applied the one delta we saw (best bid -> 0.61),
    # but the venue's authoritative top is 0.63 because an intervening update never
    # reached us. The reconstruction has silently diverged -> mark stale + resync.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "100")]))
    book.apply_price_change([{"price": "0.61", "side": "BUY", "size": "50"}])

    assert book.verify_top_of_book(best_bid="0.63", best_ask="0.62") is False
    assert book.is_stale()
    assert book.midpoint() is None  # ERS must never size off a diverged book


def test_verify_top_of_book_matches_when_a_side_is_empty():
    # One-sided book: the venue reports an empty ask (""), and we also have none.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[]))

    assert book.best_ask() is None
    assert book.verify_top_of_book(best_bid="0.60", best_ask="") is True
    assert not book.is_stale()


def test_verify_top_of_book_divergence_when_venue_has_a_side_we_lack():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[]))

    # Venue reports an ask we don't have -> we missed the update that added it.
    assert book.verify_top_of_book(best_bid="0.60", best_ask="0.62") is False
    assert book.is_stale()


def test_verify_top_of_book_treats_zero_as_an_empty_side():
    # Polymarket order prices live in (0, 1); 0 is never a real top-of-book level.
    # If the venue encodes an empty side as "0" (rather than ""), it must read as
    # empty -> match our empty side, not false-diverge into a perpetual gap.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[]))

    assert book.best_ask() is None
    assert book.verify_top_of_book(best_bid="0.60", best_ask="0") is True
    assert not book.is_stale()


def test_verify_top_of_book_treats_one_as_an_empty_ask_boundary():
    # Live evidence (2026-07-17) showed one-sided snapshots with no ask while the
    # corresponding price_change top reports best_ask="1". Prices are strictly
    # inside (0, 1), so the ask boundary is an empty-side sentinel, not a level we
    # missed. Misclassifying it causes a permanent eight-reconnect storm.
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.999", "100")], asks=[]))

    assert book.best_ask() is None
    assert book.verify_top_of_book(best_bid="0.999", best_ask="1") is True
    assert not book.is_stale()


def test_verify_top_of_book_treats_zero_as_an_empty_bid_boundary():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[], asks=[("0.40", "10")]))

    assert book.verify_top_of_book(best_bid="0", best_ask="0.40") is True
    assert not book.is_stale()


def test_verify_top_of_book_rejects_one_as_an_empty_bid_boundary():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[], asks=[("0.40", "10")]))

    assert book.verify_top_of_book(best_bid="1", best_ask="0.40") is False
    assert book.is_stale()


def test_verify_top_of_book_rejects_empty_ask_boundary_when_ask_exists():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.40", "10")], asks=[("0.60", "10")]))

    assert book.verify_top_of_book(best_bid="0.40", best_ask="1") is False
    assert book.is_stale()


# --- depth helpers (feed the synthetic-event detectors) ----------------------


def test_top_of_book_returns_best_prices_and_sizes():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100"), ("0.58", "200")],
                              asks=[("0.62", "150"), ("0.65", "50")]))

    assert book.top_of_book() == (Decimal("0.60"), Decimal("100"),
                                  Decimal("0.62"), Decimal("150"))


def test_top_of_book_handles_empty_sides():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[]))

    assert book.top_of_book() == (Decimal("0.60"), Decimal("100"), None, None)


def test_size_at_returns_level_size_or_zero():
    book = LocalBook()
    book.apply_book(_snapshot(bids=[("0.60", "100")], asks=[("0.62", "150")]))

    assert book.size_at("BUY", "0.60") == Decimal("100")
    assert book.size_at("SELL", "0.62") == Decimal("150")
    assert book.size_at("BUY", "0.59") == Decimal("0")  # absent level -> 0
