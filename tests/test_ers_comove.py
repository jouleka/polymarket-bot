"""S3 / POL-5 slice 3 -- co-move estimator + ClusterModel + per-cluster cap."""

import json
import tempfile
from decimal import Decimal

from polybot.core.models import Envelope
from polybot.ers.comove import ClusterModel, build_bar_series, correlation
from polybot.ingestion.midpoint import MIDPOINT_SCHEMA, MIDPOINT_SOURCE
from polybot.ers.validator import ClusterView
from polybot.storage.market_memory import EventStore


def _bars(*mids):
    """A token's midpoint bars on consecutive bar indices 0..n-1."""
    return {i: Decimal(str(m)) for i, m in enumerate(mids)}


# --- correlation(returns_a, returns_b): Pearson over aligned return series ------------------

def _d(*xs):
    return [Decimal(str(x)) for x in xs]


def test_correlation_perfectly_correlated_returns_one():
    assert correlation(_d(1, 2, 3, 4), _d(2, 4, 6, 8)) == Decimal("1")


def test_correlation_perfectly_anticorrelated_returns_minus_one():
    assert correlation(_d(1, 2, 3, 4), _d(4, 3, 2, 1)) == Decimal("-1")


def test_correlation_independent_zigzag_near_zero():
    # orthogonal-ish patterns -> |rho| well below the warm-cap regime.
    rho = correlation(_d(1, -1, 1, -1, 1, -1), _d(1, 1, -1, -1, 1, 1))
    assert abs(rho) < Decimal("0.5")


def test_correlation_flat_series_fails_closed_to_one():
    # zero variance -> correlation undefined -> fail closed to +1 (max tightening).
    assert correlation(_d(1, 1, 1), _d(1, 2, 3)) == Decimal("1")


def test_correlation_too_few_points_fails_closed_to_one():
    assert correlation(_d(1), _d(2)) == Decimal("1")


# --- ClusterModel.view(token_ids) -> ClusterView(warm, rho) ---------------------------------

# A & B move identically (B = A shifted), C moves 2x A's increments -> A-pairs are rho=1.
_A = _bars(0.50, 0.52, 0.51, 0.55, 0.54)   # returns [.02, -.01, .04, -.01]
_B = _bars(0.40, 0.42, 0.41, 0.45, 0.44)   # same returns -> corr(A,B) = 1
_C = _bars(0.30, 0.34, 0.32, 0.40, 0.38)   # 2x returns -> corr(A,C) = corr(B,C) = 1


def test_view_single_token_is_cold():
    m = ClusterModel({"A": _A}, min_observations=1)
    v = m.view(["A"])
    assert v == ClusterView(warm=False, rho=None)


def test_view_insufficient_paired_observations_is_cold():
    # 4 paired returns < min_observations=5 -> fail closed to cold (count-gated).
    m = ClusterModel({"A": _A, "B": _B}, min_observations=5)
    v = m.view(["A", "B"])
    assert v.warm is False and v.rho is None


def test_view_warm_correlated_pair_reports_rho():
    m = ClusterModel({"A": _A, "B": _B}, min_observations=4)
    v = m.view(["A", "B"])
    assert v.warm is True
    assert v.rho == Decimal("1")


def test_view_warm_cluster_uses_max_pairwise_rho():
    m = ClusterModel({"A": _A, "B": _B, "C": _C}, min_observations=4)
    v = m.view(["A", "B", "C"])
    assert v.warm is True
    assert v.rho == Decimal("1")  # every pair here is perfectly correlated


def test_view_one_cold_pair_makes_whole_cluster_cold():
    # B has too few shared bars -> a cold pair -> the whole cluster is cold.
    short_b = {0: Decimal("0.40"), 1: Decimal("0.42")}  # only 1 paired return
    m = ClusterModel({"A": _A, "B": short_b, "C": _C}, min_observations=4)
    v = m.view(["A", "B", "C"])
    assert v.warm is False and v.rho is None


def test_view_unknown_token_with_no_bars_is_cold():
    m = ClusterModel({"A": _A}, min_observations=4)
    v = m.view(["A", "ZZZ"])
    assert v.warm is False and v.rho is None


# --- build_bar_series(store, bar_ns, until): EventStore -> {token: {bar_index: mid}} ---------

def _book_frame(asset, bid, ask):
    return {"event_type": "book", "asset_id": asset,
            "bids": [{"price": bid, "size": "100"}], "asks": [{"price": ask, "size": "100"}]}


def _ws_env(asset, frame, observed_at):
    return Envelope(source="clob-ws", source_tier="VENUE",
                    event_id=f"{asset}:book:{observed_at}", observed_at=observed_at,
                    content=json.dumps(frame), market_links=(asset,))


def _mid_env(observed_at, books):
    payload = {
        "schema": MIDPOINT_SCHEMA,
        "books": {
            token: {"bid": bid, "ask": ask, "mid": mid}
            for token, (bid, ask, mid) in books.items()
        },
    }
    return Envelope(
        source=MIDPOINT_SOURCE,
        source_tier="VENUE",
        event_id=f"midpoint:{observed_at}",
        observed_at=observed_at,
        content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        market_links=tuple(sorted(books)),
    )


def test_build_bar_series_reads_midpoint_batches_and_uses_bar_close():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_mid_env(0, {
            "A": ("0.60", "0.62", "0.61"),
            "B": ("0.30", "0.32", "0.31"),
        }))
        store.append(_mid_env(10, {
            "A": ("0.50", "0.52", "0.51"),
        }))
        store.append(_mid_env(1000, {
            "A": ("0.70", "0.72", "0.71"),
            "B": ("0.34", "0.36", "0.35"),
        }))

        bars = build_bar_series(store, bar_ns=1000)

    assert bars["A"] == {0: Decimal("0.51"), 1: Decimal("0.71")}
    assert bars["B"] == {0: Decimal("0.31"), 1: Decimal("0.35")}


def test_build_bar_series_midpoints_respect_until_cutoff():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_mid_env(0, {"A": ("0.60", "0.62", "0.61")}))
        store.append(_mid_env(1000, {"A": ("0.70", "0.72", "0.71")}))
        store.append(_mid_env(2000, {"A": ("0.80", "0.82", "0.81")}))

        bars = build_bar_series(store, bar_ns=1000, until=1500)

    assert bars["A"] == {0: Decimal("0.61"), 1: Decimal("0.71")}


def test_build_bar_series_does_not_forward_fill_omitted_midpoint():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_mid_env(0, {
            "A": ("0.60", "0.62", "0.61"),
            "B": ("0.30", "0.32", "0.31"),
        }))
        store.append(_mid_env(1000, {
            "A": ("0.70", "0.72", "0.71"),
        }))

        bars = build_bar_series(store, bar_ns=1000)

    assert bars["A"] == {0: Decimal("0.61"), 1: Decimal("0.71")}
    assert bars["B"] == {0: Decimal("0.31")}


def test_build_bar_series_auto_never_merges_raw_and_midpoint_rows():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_ws_env("RAW", _book_frame("RAW", "0.90", "0.92"), 0))
        store.append(_mid_env(10, {"A": ("0.50", "0.52", "0.51")}))

        automatic = build_bar_series(store, bar_ns=1000)
        forced_raw = build_bar_series(store, bar_ns=1000, source="clob-ws")

    assert automatic == {"A": {0: Decimal("0.51")}}
    assert forced_raw == {"RAW": {0: Decimal("0.91")}}


def test_build_bar_series_takes_last_midpoint_in_each_bar():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_ws_env("A", _book_frame("A", "0.60", "0.62"), 0))     # bar0, mid .61
        store.append(_ws_env("A", _book_frame("A", "0.50", "0.52"), 10))    # bar0, mid .51 (last)
        store.append(_ws_env("A", _book_frame("A", "0.70", "0.72"), 1000))  # bar1, mid .71
        bars = build_bar_series(store, bar_ns=1000)
    assert bars["A"] == {0: Decimal("0.51"), 1: Decimal("0.71")}


def test_build_bar_series_tracks_multiple_assets():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_ws_env("A", _book_frame("A", "0.60", "0.62"), 0))
        store.append(_ws_env("B", _book_frame("B", "0.30", "0.32"), 5))
        store.append(_ws_env("A", _book_frame("A", "0.64", "0.66"), 1000))
        store.append(_ws_env("B", _book_frame("B", "0.34", "0.36"), 1005))
        bars = build_bar_series(store, bar_ns=1000)
    assert bars["A"] == {0: Decimal("0.61"), 1: Decimal("0.65")}
    assert bars["B"] == {0: Decimal("0.31"), 1: Decimal("0.35")}


def test_build_bar_series_respects_until_cutoff():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_ws_env("A", _book_frame("A", "0.60", "0.62"), 0))     # bar0
        store.append(_ws_env("A", _book_frame("A", "0.70", "0.72"), 1000))  # bar1
        store.append(_ws_env("A", _book_frame("A", "0.80", "0.82"), 2000))  # bar2 (excluded)
        bars = build_bar_series(store, bar_ns=1000, until=1500)
    assert bars["A"] == {0: Decimal("0.61"), 1: Decimal("0.71")}


def test_build_bar_series_skips_non_ws_rows():
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        store.append(_ws_env("A", _book_frame("A", "0.60", "0.62"), 0))
        store.append(Envelope(source="data-api", source_tier="VENUE", event_id="t1",
                              observed_at=5, content=json.dumps({"id": "t1"}), market_links=("Z",)))
        bars = build_bar_series(store, bar_ns=1000)
    assert "Z" not in bars
    assert bars["A"] == {0: Decimal("0.61")}
