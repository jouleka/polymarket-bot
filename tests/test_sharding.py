"""Tests for the WS shard coordinator (POL-3 / S1).

The venue caps assets per connection (~500), so a large universe is split into
shards: each shard is its own MarketStream + MarketSocket, but all shards share
the one stamper (global observed_at order) and one sink. Per-shard streams give
per-shard isolation (one shard's disconnect stales only its own books).

Uses FakeTransport + asyncio.run so the coordinator logic is exercised with no
network. On the default (non-eager) loop, tasks first-step in creation order
(FIFO call_soon) and nothing suspends before ``await self._connect()`` resolves,
so the ``_connect_from`` iterator hands transport[i] to shard[i]. Tests that span
reconnects (where that ordering is not robust) instead use a subscription-routed
transport, which serves frames by the assets a shard actually subscribes to.
"""

import asyncio
import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.sharding import ShardedMarketCollector


class FakeDisconnect(Exception):
    """Stand-in for websockets.ConnectionClosed."""


def _book_frame(asset_id, best_bid, best_ask):
    return json.dumps({
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": best_bid, "size": "100"}],
        "asks": [{"price": best_ask, "size": "100"}],
    })


class FakeTransport:
    def __init__(self, frames):
        self._frames = frames
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def __aiter__(self):
        for frame in self._frames:
            if isinstance(frame, BaseException):
                raise frame
            yield frame


def _connect_from(transports):
    it = iter(transports)

    async def connect():
        return next(it)

    return connect


def _scripted_connect(scripts):
    """Order-independent connect: each connection serves the frames for the assets
    it actually SUBSCRIBES to (the Nth connection of an asset gets scripts[asset][N]),
    so transport->shard assignment never depends on connect() call order — needed
    once shards reconnect at different times.

    scripts: {asset_id: [per-connection frame list, ...]}; a frame may be an
    exception instance to simulate a disconnect mid-stream.
    """
    served = {asset: 0 for asset in scripts}

    class _ScriptedTransport:
        def __init__(self):
            self.sent = []
            self._assets = []

        async def send(self, message):
            self.sent.append(message)
            payload = json.loads(message)
            if isinstance(payload, dict) and payload.get("type") == "market":
                self._assets = payload["assets_ids"]

        async def __aiter__(self):
            frames = []
            for asset in self._assets:
                index = served[asset]
                served[asset] = index + 1
                if index < len(scripts.get(asset, [])):
                    frames.extend(scripts[asset][index])
            for frame in frames:
                if isinstance(frame, BaseException):
                    raise frame
                yield frame

    async def connect():
        return _ScriptedTransport()

    return connect


def test_sharded_collector_splits_assets_routes_books_and_subscribes_per_shard():
    # 3 assets, max 2 per shard -> 2 shards: [A,B] and [C].
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62"), _book_frame("B", "0.40", "0.45")])
    t1 = FakeTransport([_book_frame("C", "0.30", "0.35")])

    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B", "C"], max_assets_per_shard=2,
    )

    assert collector.shard_count == 2

    asyncio.run(collector.run(max_connections=1))

    # books routed across shards
    assert collector.book_for("A").best_bid() == Decimal("0.60")
    assert collector.book_for("B").best_bid() == Decimal("0.40")
    assert collector.book_for("C").best_bid() == Decimal("0.30")
    assert collector.book_for("Z") is None  # unknown asset

    # each shard subscribed to ONLY its own chunk (matched by content, not by
    # which physical transport happened to take the connection)
    subs = [t0.sent[0], t1.sent[0]]
    sub_ab = next(s for s in subs if '"A"' in s)
    sub_c = next(s for s in subs if '"C"' in s)
    assert '"A"' in sub_ab and '"B"' in sub_ab and '"C"' not in sub_ab
    assert '"C"' in sub_c and '"A"' not in sub_c


async def _immediate(_delay):
    pass  # non-suspending backoff so shard tasks stay deterministic in tests


def test_sharded_collector_halts_the_whole_group_on_a_format_change_in_any_shard():
    # A venue format change is global: an unknown event_type in ANY shard must
    # tear down the whole collector (fail-loud), not just that one shard.
    import pytest

    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([json.dumps({"event_type": "brand_new_thing", "asset_id": "C"})])

    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "C"], max_assets_per_shard=1,
    )

    with pytest.raises(ExceptionGroup) as excinfo:
        asyncio.run(collector.run(max_connections=1))
    matched, _ = excinfo.value.split(ValueError)  # recurses; robust to nesting
    assert matched is not None  # the HALT (unknown event_type) is in the group


def test_sharded_collector_isolates_staleness_to_the_disconnected_shard():
    # shard0 (A) builds a book then disconnects; shard1 (B) stays clean. Only the
    # disconnected shard's book may read stale (the others keep their fresh books).
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62"), FakeDisconnect()])
    t1 = FakeTransport([_book_frame("B", "0.40", "0.45")])

    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"], max_assets_per_shard=1,
        reconnect_on=(FakeDisconnect,), sleep=_immediate,
    )

    asyncio.run(collector.run(max_connections=1))  # one connection each (no reconnect)

    assert collector.book_for("A").is_stale()        # disconnected shard
    assert not collector.book_for("B").is_stale()    # other shard untouched


def test_sharded_collector_reconnects_one_shard_while_sibling_keeps_streaming():
    # Shard A disconnects then RECONNECTS+resyncs while sibling shard B independently
    # crosses its own normal-close reconnect boundary and receives the same fresh
    # snapshot. max_connections=2 bounds both scripted shards so the run terminates.
    scripts = {
        "A": [[_book_frame("A", "0.60", "0.62"), FakeDisconnect()],  # conn1: book then drop
              [_book_frame("A", "0.61", "0.63")]],                   # conn2: resync snapshot
        "B": [[_book_frame("B", "0.40", "0.45")],                    # conn1: normal close
              [_book_frame("B", "0.40", "0.45")]],                   # conn2: replacement snapshot
    }
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _scripted_connect(scripts), stamper, ["A", "B"], max_assets_per_shard=1,
        reconnect_on=(FakeDisconnect,), sleep=_immediate,
    )

    asyncio.run(collector.run(max_connections=2))

    assert collector.book_for("A").best_bid() == Decimal("0.61")  # A resynced to the fresh snapshot
    assert not collector.book_for("A").is_stale()                 # ...and is no longer stale
    assert collector.book_for("B").best_bid() == Decimal("0.40")  # B independently resnapshotted
    assert not collector.book_for("B").is_stale()


def test_sharded_collector_shares_one_stamper_for_global_observed_at_order():
    # Frames from different shards must get distinct, strictly increasing observed_at
    # from the SINGLE shared stamper — not per-shard stampers, which would collide.
    observed = []
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([_book_frame("B", "0.40", "0.45")])

    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"],
        sink=lambda obs: observed.append(obs.observed_at), max_assets_per_shard=1,
    )

    asyncio.run(collector.run(max_connections=1))

    assert len(observed) == 2
    assert len(set(observed)) == 2          # no collision across shards
    assert observed == sorted(observed)     # globally ordered


def test_sharded_collector_rejects_nonpositive_shard_size():
    import pytest

    stamper = MonotonicStamper(clock=lambda: 1)
    with pytest.raises(ValueError):
        ShardedMarketCollector(_connect_from([]), stamper, ["A"], max_assets_per_shard=0)


def test_sharded_collector_rejects_duplicate_asset_ids():
    # A duplicate would be double-subscribed across shards (two books, doubled
    # persistence) and book_for would silently return only one -> fail loud.
    import pytest

    stamper = MonotonicStamper(clock=lambda: 1)
    with pytest.raises(ValueError):
        ShardedMarketCollector(_connect_from([]), stamper, ["A", "B", "A"], max_assets_per_shard=2)


def test_sharded_collector_chunk_boundaries():
    # Exactly max -> 1 shard (no empty trailing shard); max+1 -> 2 shards.
    stamper = MonotonicStamper(clock=lambda: 1)
    exact = ShardedMarketCollector(_connect_from([]), stamper, ["a", "b", "c", "d"], max_assets_per_shard=4)
    assert exact.shard_count == 1
    one_over = ShardedMarketCollector(_connect_from([]), stamper, ["a", "b", "c"], max_assets_per_shard=2)
    assert one_over.shard_count == 2  # [a,b] + [c]


def test_sharded_collector_default_limits_divergence_blast_radius():
    stamper = MonotonicStamper(clock=lambda: 1)
    assets = [f"asset-{index}" for index in range(26)]

    collector = ShardedMarketCollector(_connect_from([]), stamper, assets)

    assert collector.shard_count == 2


def test_sharded_collector_rejects_empty_asset_ids():
    # A collector that streams nothing returns from run() immediately, which a 24/7
    # supervisor can't distinguish from a clean shutdown -> almost certainly a config
    # bug, so refuse it at construction.
    import pytest

    stamper = MonotonicStamper(clock=lambda: 1)
    with pytest.raises(ValueError):
        ShardedMarketCollector(_connect_from([]), stamper, [])



def test_collector_forwards_detector_and_synthetic_sink():
    # The coordinator forwards an optional synthetic-event detector + sink to each
    # shard stream, so derived events (here: a liquidity-evaporation from the best
    # bid being consumed) reach the dedicated synthetic sink.
    from polybot.ingestion.synthetic import SyntheticDetector

    pc = json.dumps({"event_type": "price_change", "market": "m", "timestamp": "1",
                     "price_changes": [{"asset_id": "A", "price": "0.60", "side": "BUY",
                                        "size": "0", "best_bid": "", "best_ask": "0.62"}]})
    transport = FakeTransport([_book_frame("A", "0.60", "0.62"), pc])
    synth = []
    collector = ShardedMarketCollector(
        _connect_from([transport]), MonotonicStamper(), ["A"],
        detector=SyntheticDetector(min_evaporation_size="50", large_print_size="1000000"),
        synthetic_sink=synth.append,
    )

    asyncio.run(collector.run(max_connections=1))

    assert [o.event_type for o in synth] == ["liquidity_evaporation"]
    assert synth[0].message["asset_id"] == "A" and synth[0].message["price"] == "0.60"


# --- S4.4d: collector-level WS health = the LAGGING shard's health -------------


def test_collector_last_frame_at_is_none_before_any_frame():
    """Kills: initializing to 0/now -- an unstarted collector must read as
    never-saw-a-frame so the L5 wired-but-silent path fires."""
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(_connect_from([]), stamper, ["A", "B"],
                                       max_assets_per_shard=1)

    assert collector.last_frame_at() is None


def test_collector_last_frame_at_is_the_min_across_shards():
    """Collector health is the OLDEST shard stamp: one lagging shard defines the
    whole collector (fail-closed). Kills: min()->max() (the freshest shard would
    mask a lagging sibling) and any single-shard read."""
    observed = []
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([_book_frame("B", "0.40", "0.45")])
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"],
        sink=lambda obs: observed.append(obs.observed_at), max_assets_per_shard=1,
    )

    asyncio.run(collector.run(max_connections=1))

    assert len(observed) == 2 and len(set(observed)) == 2
    assert collector.last_frame_at() == min(observed)  # the OLDER shard stamp, not the newer


def test_collector_last_frame_at_is_none_when_any_shard_has_no_frame_yet():
    """Fail-closed: a shard that never received a frame = +inf staleness for the
    WHOLE collector, not 'min of the shards that did'. Kills: skipping None shards
    in the aggregation."""
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([])  # shard B connects+subscribes but never receives a frame
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"], max_assets_per_shard=1,
    )

    asyncio.run(collector.run(max_connections=1))

    assert collector.book_for("A") is not None   # shard A DID stream
    assert collector.last_frame_at() is None     # but shard B's silence wins (fail-closed)
