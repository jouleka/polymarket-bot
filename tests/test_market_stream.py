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


def _price_change(*entries, market="0xmarket", timestamp="1"):
    """Live-format price_change frame (probed 2026-06-25): top-level ``market`` +
    ``price_changes`` list, with per-entry asset_id and the venue's resulting
    best_bid/best_ask. Each entry tuple is
    (asset_id, price, side, size, best_bid, best_ask).
    """
    return {
        "event_type": "price_change",
        "market": market,
        "timestamp": timestamp,
        "price_changes": [
            {"asset_id": a, "price": p, "side": sd, "size": sz,
             "best_bid": bb, "best_ask": ba}
            for (a, p, sd, sz, bb, ba) in entries
        ],
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

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

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


# --- live price_change fan-out + mid-stream gap -> resync ----------------------


def test_ingest_applies_live_price_change_to_a_tracked_book():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

    assert stream.book_for("A").best_bid() == Decimal("0.61")
    assert not stream.book_for("A").is_stale()


def test_ingest_price_change_ignores_untracked_sibling_asset():
    # A price_change frame is scoped to a market and carries BOTH legs (verified
    # live), but this shard only subscribed to A. The untracked sibling B must not
    # get a phantom, never-snapshotted book.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "50", "0.61", "0.62"),
        ("B", "0.39", "SELL", "50", "0.38", "0.39"),
    ))

    assert stream.book_for("A").best_bid() == Decimal("0.61")
    assert stream.book_for("B") is None  # no phantom book for the untracked leg


def test_ingest_price_change_emits_one_observation_per_tracked_asset():
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append)
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_book("B", [("0.40", "100")], [("0.45", "100")]))
    seen.clear()

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "50", "0.61", "0.62"),
        ("B", "0.41", "BUY", "50", "0.41", "0.45"),
    ))

    assert [o.asset_id for o in seen] == ["A", "B"]
    assert all(o.event_type == "price_change" for o in seen)
    assert seen[0].observed_at < seen[1].observed_at  # distinct, ordered stamps


def test_ingest_price_change_without_price_changes_list_halts():
    # The live frame schema MUST carry a price_changes list; its absence (e.g. the
    # old single-asset `changes` shape) is a format change -> HALT, not a silent skip.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with pytest.raises(ValueError, match="price_changes"):
        stream.ingest({"event_type": "price_change", "asset_id": "A",
                       "changes": [{"price": "0.61", "side": "BUY", "size": "50"}]})


def test_ingest_price_change_entry_missing_required_fields_halts():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with pytest.raises(ValueError, match="HALT on format change"):
        stream.ingest({"event_type": "price_change", "market": "m", "timestamp": "1",
                       "price_changes": [{"asset_id": "A", "price": "0.61"}]})  # no side/size


def test_ingest_price_change_entry_missing_best_bid_ask_halts():
    # best_bid/best_ask are the SOLE gap detector. Their absence is a format change
    # and MUST HALT loudly -- otherwise verify reads None, every frame false-diverges,
    # and the bot silently storms reconnects + parks midpoint at None instead of HALTing.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with pytest.raises(ValueError, match="HALT on format change"):
        stream.ingest({"event_type": "price_change", "market": "m", "timestamp": "1",
                       "price_changes": [{"asset_id": "A", "price": "0.61",
                                          "side": "BUY", "size": "50"}]})  # no best_bid/best_ask


def test_ingest_price_change_nonstring_numeric_field_halts():
    # The venue sends prices/sizes as strings; a JSON number is a lossy-float format
    # change (same stance as the Gamma normalizer) AND would desync apply (Decimal(x))
    # from verify -- HALT rather than silently mis-reconstruct.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with pytest.raises(ValueError, match="HALT on format change"):
        stream.ingest({"event_type": "price_change", "market": "m", "timestamp": "1",
                       "price_changes": [{"asset_id": "A", "price": 0.61, "side": "BUY",
                                          "size": "50", "best_bid": "0.61", "best_ask": "0.62"}]})


def test_ingest_price_change_present_but_null_field_halts():
    # A present-but-null required field is a format change too -> HALT (not a raw TypeError).
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with pytest.raises(ValueError, match="HALT on format change"):
        stream.ingest({"event_type": "price_change", "market": "m", "timestamp": "1",
                       "price_changes": [{"asset_id": "A", "price": None, "side": "BUY",
                                          "size": "50", "best_bid": "0.61", "best_ask": "0.62"}]})


def test_ingest_price_change_gap_marks_stale_and_requests_resync():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    assert not stream.consume_resync_request()

    # Venue's authoritative best_bid (0.70) is unreachable from our book after the
    # applied delta -> divergence -> book stale + a resync requested.
    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.70", "0.62")))

    assert stream.book_for("A").is_stale()
    assert stream.consume_resync_request() is True
    assert stream.consume_resync_request() is False  # consumed once: one resubscribe, not a storm


def test_ingest_consistent_price_change_requests_no_resync():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

    assert not stream.book_for("A").is_stale()
    assert stream.consume_resync_request() is False


def test_consume_clean_progress_true_after_a_consistent_price_change():
    # A clean applied delta is the signal the socket uses to reset its resync-storm
    # counter. A book snapshot is NOT delta progress; a consistent price_change is.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    assert stream.consume_clean_progress() is False  # a snapshot is not clean delta progress

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

    assert stream.consume_clean_progress() is True
    assert stream.consume_clean_progress() is False  # read-and-clear


def test_consume_clean_progress_false_after_a_gap():
    # A diverging delta must NOT count as clean progress (else a storm would never escalate).
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.70", "0.62")))  # gap

    assert stream.consume_clean_progress() is False
    assert stream.consume_resync_request() is True


def test_ingest_persists_pre_snapshot_delta_for_a_subscribed_asset():
    # The no-backfill store must capture everything we received. A delta for a
    # SUBSCRIBED asset that lands before its snapshot (a race after every (re)connect)
    # must still be ARCHIVED, even though it can't be APPLIED (no baseline). A truly
    # untracked sibling leg is still skipped (no phantom book, no cross-shard dup row).
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append, asset_ids=["A"])

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "50", "0.61", "0.62"),   # subscribed, snapshot not arrived yet
        ("B", "0.39", "SELL", "50", "0.38", "0.39"),  # untracked sibling leg
    ))

    assert [o.asset_id for o in seen] == ["A"]      # A archived; B (untracked) skipped
    assert seen[0].event_type == "price_change"
    assert stream.book_for("A") is None             # not applied: no baseline to apply onto
    assert stream.consume_resync_request() is False  # no book to verify -> not a gap


def test_ingest_price_change_observation_message_is_a_per_asset_slice():
    # Each fanned-out row in the no-backfill store must carry ONLY its own asset's
    # entries plus the frame's market+timestamp (published_at) -- never the sibling's.
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append)
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_book("B", [("0.40", "100")], [("0.45", "100")]))
    seen.clear()

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "50", "0.61", "0.62"),
        ("B", "0.41", "BUY", "50", "0.41", "0.45"),
        market="0xMKT", timestamp="999",
    ))

    by_asset = {o.asset_id: o.message for o in seen}
    assert by_asset["A"] == {
        "event_type": "price_change", "asset_id": "A", "market": "0xMKT", "timestamp": "999",
        "price_changes": [{"asset_id": "A", "price": "0.61", "side": "BUY", "size": "50",
                           "best_bid": "0.61", "best_ask": "0.62"}],
    }
    assert [e["asset_id"] for e in by_asset["B"]["price_changes"]] == ["B"]  # only B's entry


def test_ingest_price_change_gap_on_one_leg_does_not_poison_the_sibling():
    # The load-bearing per-asset isolation invariant: a divergence on leg A must not
    # mark leg B stale (B keeps feeding the ERS until the reconnect's mark_all_stale).
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append)
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_book("B", [("0.40", "100")], [("0.45", "100")]))
    seen.clear()

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "50", "0.70", "0.62"),   # A diverges
        ("B", "0.41", "BUY", "50", "0.41", "0.45"),   # B consistent
    ))

    assert stream.book_for("A").is_stale()             # only the gapped leg
    assert not stream.book_for("B").is_stale()         # sibling stays fresh
    assert stream.consume_resync_request() is True
    assert sorted(o.asset_id for o in seen) == ["A", "B"]  # both still persisted


def test_ingest_price_change_multi_entry_same_asset_verifies_last_entry():
    # Several level changes for ONE asset in a single frame: apply all, persist all in
    # one Observation, and gap-check against the LAST entry's resulting top-of-book.
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append)
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    seen.clear()

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "80", "0.61", "0.62"),    # intermediate level change
        ("A", "0.63", "SELL", "40", "0.61", "0.62"),   # final: venue top is 0.61 / 0.62
    ))

    book = stream.book_for("A")
    assert book.best_bid() == Decimal("0.61")
    assert book.best_ask() == Decimal("0.62")          # 0.63 ask worse than the existing 0.62
    assert not book.is_stale()                          # last entry's top matches -> no gap
    assert stream.consume_resync_request() is False
    assert [o.asset_id for o in seen] == ["A"]          # ONE row for A...
    assert len(seen[0].message["price_changes"]) == 2   # ...carrying BOTH entries


def test_ingest_price_change_multi_entry_gap_keys_off_the_last_entry():
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    stream.ingest(_price_change(
        ("A", "0.61", "BUY", "80", "0.61", "0.62"),     # consistent so far
        ("A", "0.615", "BUY", "40", "0.70", "0.62"),    # last entry's venue bid 0.70 unreachable -> gap
    ))

    assert stream.book_for("A").is_stale()
    assert stream.consume_resync_request() is True


def test_resync_request_survives_an_intervening_consistent_frame():
    # The socket consumes the resync request once per dispatch; a gap's request must
    # NOT be cleared by a later consistent-looking frame that lands before the consume.
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.70", "0.62")))  # gap -> request set

    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))  # consistent-looking

    assert stream.consume_resync_request() is True  # the gap's request survived


def test_ingest_price_change_malformed_later_entry_halts_atomically():
    # _group_by_asset validates EVERY entry before any mutation/persist, so a malformed
    # later entry HALTs with no partial fan-out (earlier asset's book untouched, sink empty).
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append)
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    seen.clear()

    with pytest.raises(ValueError, match="HALT on format change"):
        stream.ingest({"event_type": "price_change", "market": "m", "timestamp": "1",
                       "price_changes": [
                           {"asset_id": "A", "price": "0.61", "side": "BUY", "size": "50",
                            "best_bid": "0.61", "best_ask": "0.62"},
                           {"asset_id": "B", "price": "0.41"},  # malformed -> HALT
                       ]})

    assert stream.book_for("A").best_bid() == Decimal("0.60")  # A NOT mutated
    assert seen == []                                          # nothing partially persisted
