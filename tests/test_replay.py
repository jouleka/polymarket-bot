"""Tests for deterministic replay reconstruction (POL-3 / S1 acceptance gate).

The replay-fidelity / no-look-ahead contract: a per-asset book reconstructed by
replaying stored frames in observed_at order must equal the book that was live at
that point, and a reconstruction as-of time T must depend ONLY on data with
observed_at <= T (no future frame may leak in). These are the properties
calibration / shadow backtesting rely on.
"""

import tempfile
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.replay import reconstruct, reconstruct_from_store
from polybot.storage.market_memory import EventStore


def _book(asset_id, bids, asks):
    return {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def _pc(asset_id, price, side, size, best_bid, best_ask, market="0xMKT", timestamp="1"):
    return {
        "event_type": "price_change",
        "market": market,
        "timestamp": timestamp,
        "price_changes": [{"asset_id": asset_id, "price": price, "side": side, "size": size,
                           "best_bid": best_bid, "best_ask": best_ask}],
    }


def _top(book):
    return (str(book.best_bid()), str(book.best_ask()))


def test_reconstruct_book_from_snapshot_and_deltas():
    stream = reconstruct([
        _book("A", [("0.60", "100")], [("0.62", "100")]),
        _pc("A", "0.61", "BUY", "50", "0.61", "0.62"),
    ])

    assert stream.book_for("A").best_bid() == Decimal("0.61")
    assert stream.book_for("A").best_ask() == Decimal("0.62")


def test_reconstruct_fans_out_to_both_legs():
    # One price_change frame carrying both legs reconstructs both books.
    stream = reconstruct([
        _book("A", [("0.60", "100")], [("0.62", "100")]),
        _book("B", [("0.38", "100")], [("0.40", "100")]),
        {"event_type": "price_change", "market": "0xMKT", "timestamp": "2", "price_changes": [
            {"asset_id": "A", "price": "0.61", "side": "BUY", "size": "50",
             "best_bid": "0.61", "best_ask": "0.62"},
            {"asset_id": "B", "price": "0.39", "side": "BUY", "size": "50",
             "best_bid": "0.39", "best_ask": "0.40"},
        ]},
    ])

    assert _top(stream.book_for("A")) == ("0.61", "0.62")
    assert _top(stream.book_for("B")) == ("0.39", "0.40")


def test_replay_is_deterministic():
    msgs = [
        _book("A", [("0.60", "100")], [("0.62", "100")]),
        _pc("A", "0.61", "BUY", "50", "0.61", "0.62"),
    ]

    assert _top(reconstruct(msgs).book_for("A")) == _top(reconstruct(msgs).book_for("A"))


def test_replay_prefix_matches_point_in_time_state_no_look_ahead():
    # A recorded sequence with the KNOWN live top-of-book after each frame. Each
    # prefix reconstruction must equal that point-in-time state using ONLY the
    # first k frames -- it cannot see (or be changed by) any later frame.
    seq = [
        (_book("A", [("0.60", "100")], [("0.62", "100")]),     ("0.60", "0.62")),
        (_pc("A", "0.61", "BUY", "50", "0.61", "0.62"),        ("0.61", "0.62")),
        (_pc("A", "0.59", "BUY", "50", "0.61", "0.62"),        ("0.61", "0.62")),  # deeper bid, top unchanged
        (_pc("A", "0.61", "BUY", "0", "0.60", "0.62"),         ("0.60", "0.62")),  # remove 0.61 -> top back to 0.60
    ]
    frames = [f for f, _ in seq]

    for k in range(1, len(seq) + 1):
        book = reconstruct(frames[:k]).book_for("A")
        assert _top(book) == seq[k - 1][1], f"prefix length {k}"


def test_reconstruct_from_store_replay_until_is_point_in_time():
    # The store-backed gate: persist live frames, then reconstruct as-of an
    # observed_at cutoff and assert it equals the live state at that cutoff -- and
    # that a LATER frame does not leak into an earlier reconstruction.
    stamper = MonotonicStamper()
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        stream = MarketStream(stamper, sink=PersistingSink(store), asset_ids=["A"])

        at = {}
        for label, msg in [
            ("snap", _book("A", [("0.60", "100")], [("0.62", "100")])),
            ("d1", _pc("A", "0.61", "BUY", "50", "0.61", "0.62")),
            ("d2", _pc("A", "0.61", "BUY", "0", "0.60", "0.62")),  # removes 0.61 -> top 0.60
        ]:
            stamps = stream.ingest(msg)
            at[label] = stamps if isinstance(stamps, int) else stamps[-1]

        # as-of d1: top is 0.61/0.62 and d2 (which lowers it to 0.60) MUST NOT show.
        assert _top(reconstruct_from_store(store, until=at["d1"]).book_for("A")) == ("0.61", "0.62")
        # as-of d2: the full point-in-time state.
        assert _top(reconstruct_from_store(store, until=at["d2"]).book_for("A")) == ("0.60", "0.62")


def test_reconstruct_from_store_ignores_non_ws_rows():
    # The store also holds Data-API rows (a different source/shape); replay must
    # reconstruct from the WS frames only and not choke on the others.
    stamper = MonotonicStamper()
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        ws = PersistingSink(store)  # source="clob-ws"
        api = PersistingSink(store, source="data-api", source_tier="VENUE")
        # interleave a Data-API-style observation (no event_type/book shape)
        from polybot.ingestion.market_stream import Observation
        ws(Observation("A", "book", stamper.stamp(),
                       _book("A", [("0.60", "100")], [("0.62", "100")])))
        api(Observation("Z", "trade", stamper.stamp(), {"id": "t1", "price": "0.5"}))

        stream = reconstruct_from_store(store)  # must not raise on the data-api row
        assert _top(stream.book_for("A")) == ("0.60", "0.62")
        assert stream.book_for("Z") is None
