"""Tests for synthetic market-event detectors (POL-3 / S1).

Pure, deterministic detection of notable structural events from book deltas:
  - large_print: a single delta removes a large absolute resting size at a level.
  - liquidity_evaporation: a top-of-book level is removed or sharply thinned.
Thresholds are tunable; the events are objective observations.
"""

from decimal import Decimal

from polybot.ingestion.synthetic import SyntheticDetector

# top-of-book tuples are (best_bid, bid_size, best_ask, ask_size)
_BID_ASK = (Decimal("0.62"), Decimal("100"))  # an unchanged ask side for bid-focused cases


def _types(events, kind):
    return [e for e in events if e["event_type"] == kind]


# --- large_print --------------------------------------------------------------


def test_large_print_fires_on_a_big_size_removal():
    det = SyntheticDetector(large_print_size="5000", min_evaporation_size="1000000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("8000"), Decimal("500"))],  # removed 7500
        before_top=(Decimal("0.60"), Decimal("8000"), *_BID_ASK),
        after_top=(Decimal("0.60"), Decimal("500"), *_BID_ASK),
        observed_at=7,
    )
    lp = _types(events, "large_print")
    assert len(lp) == 1
    assert lp[0]["asset_id"] == "A" and lp[0]["side"] == "BUY"
    assert lp[0]["price"] == "0.60" and lp[0]["size_removed"] == "7500"
    assert lp[0]["triggered_at"] == 7


def test_large_print_does_not_fire_below_threshold():
    det = SyntheticDetector(large_print_size="5000", min_evaporation_size="1000000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("1000"), Decimal("500"))],  # removed 500
        before_top=(Decimal("0.60"), Decimal("1000"), *_BID_ASK),
        after_top=(Decimal("0.60"), Decimal("500"), *_BID_ASK),
        observed_at=1,
    )
    assert _types(events, "large_print") == []


def test_large_print_ignores_size_growth():
    # A level GROWING (fresh resting size posted) is not a print.
    det = SyntheticDetector(large_print_size="1000", min_evaporation_size="1000000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.59"), Decimal("100"), Decimal("9000"))],  # added 8900
        before_top=(Decimal("0.60"), Decimal("100"), *_BID_ASK),
        after_top=(Decimal("0.60"), Decimal("100"), *_BID_ASK),
        observed_at=1,
    )
    assert _types(events, "large_print") == []


# --- liquidity_evaporation ----------------------------------------------------


def test_evaporation_fires_when_best_level_is_removed():
    det = SyntheticDetector(large_print_size="1000000", min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("5000"), Decimal("0"))],
        before_top=(Decimal("0.60"), Decimal("5000"), *_BID_ASK),
        after_top=(Decimal("0.58"), Decimal("200"), *_BID_ASK),  # best bid dropped
        observed_at=3,
    )
    evap = _types(events, "liquidity_evaporation")
    assert len(evap) == 1
    assert evap[0]["side"] == "BUY" and evap[0]["price"] == "0.60"
    assert evap[0]["size_removed"] == "5000"


def test_evaporation_fires_on_a_sharp_partial_pull_at_the_touch():
    det = SyntheticDetector(large_print_size="1000000", evaporation_fraction="0.6",
                            min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("5000"), Decimal("1000"))],  # 80% pull
        before_top=(Decimal("0.60"), Decimal("5000"), *_BID_ASK),
        after_top=(Decimal("0.60"), Decimal("1000"), *_BID_ASK),  # same touch, thinned
        observed_at=1,
    )
    evap = _types(events, "liquidity_evaporation")
    assert len(evap) == 1 and evap[0]["size_removed"] == "4000"


def test_evaporation_ignores_an_improving_or_stable_touch():
    det = SyntheticDetector(large_print_size="1000000", evaporation_fraction="0.6",
                            min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.61"), Decimal("3000"), Decimal("3000"))],
        before_top=(Decimal("0.60"), Decimal("5000"), *_BID_ASK),
        after_top=(Decimal("0.61"), Decimal("3000"), *_BID_ASK),  # best bid IMPROVED
        observed_at=1,
    )
    assert _types(events, "liquidity_evaporation") == []


def test_evaporation_respects_min_size():
    det = SyntheticDetector(large_print_size="1000000", min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("200"), Decimal("0"))],  # only 200 removed
        before_top=(Decimal("0.60"), Decimal("200"), *_BID_ASK),
        after_top=(Decimal("0.58"), Decimal("500"), *_BID_ASK),
        observed_at=1,
    )
    assert _types(events, "liquidity_evaporation") == []


def test_evaporation_detects_the_ask_side_too():
    det = SyntheticDetector(large_print_size="1000000", min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("SELL", Decimal("0.62"), Decimal("4000"), Decimal("0"))],
        before_top=(Decimal("0.60"), Decimal("100"), Decimal("0.62"), Decimal("4000")),
        after_top=(Decimal("0.60"), Decimal("100"), Decimal("0.65"), Decimal("50")),  # best ask rose
        observed_at=1,
    )
    evap = _types(events, "liquidity_evaporation")
    assert len(evap) == 1 and evap[0]["side"] == "SELL" and evap[0]["price"] == "0.62"


def test_a_large_top_of_book_removal_emits_BOTH_events():
    # A big fill that clears the touch is both a large print AND a liquidity
    # evaporation -- two distinct lenses on one physical event (intentional).
    det = SyntheticDetector(large_print_size="5000", min_evaporation_size="1000")
    events = det.detect(
        "A",
        [("BUY", Decimal("0.60"), Decimal("9000"), Decimal("0"))],
        before_top=(Decimal("0.60"), Decimal("9000"), *_BID_ASK),
        after_top=(Decimal("0.58"), Decimal("100"), *_BID_ASK),
        observed_at=1,
    )
    assert len(_types(events, "large_print")) == 1
    assert len(_types(events, "liquidity_evaporation")) == 1


def test_large_print_emits_a_canonical_side():
    # Both event types must report side as canonical "BUY"/"SELL" so downstream
    # signals never branch on raw venue casing.
    det = SyntheticDetector(large_print_size="1000", min_evaporation_size="1000000")
    events = det.detect(
        "A",
        [("bid", Decimal("0.60"), Decimal("8000"), Decimal("500"))],  # raw venue "bid"
        before_top=(Decimal("0.60"), Decimal("8000"), *_BID_ASK),
        after_top=(Decimal("0.60"), Decimal("500"), *_BID_ASK),
        observed_at=1,
    )
    assert _types(events, "large_print")[0]["side"] == "BUY"


def test_unknown_side_halts_fail_loud():
    import pytest
    det = SyntheticDetector(large_print_size="1000", min_evaporation_size="1000000")
    with pytest.raises(ValueError):
        det.detect(
            "A",
            [("sideways", Decimal("0.60"), Decimal("8000"), Decimal("500"))],
            before_top=(Decimal("0.60"), Decimal("8000"), *_BID_ASK),
            after_top=(Decimal("0.60"), Decimal("500"), *_BID_ASK),
            observed_at=1,
        )
