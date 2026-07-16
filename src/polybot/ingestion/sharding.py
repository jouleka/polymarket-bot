"""Shard the CLOB market channel across multiple connections.

The venue caps assets per connection (~500), so a large universe is split into
shards. Each shard is its OWN ``MarketStream`` + ``MarketSocket``, but every shard
shares the one ``MonotonicStamper`` (so ``observed_at`` is globally ordered across
shards) and the one sink (unified Market-Memory store). Per-shard streams give
per-shard isolation: a single shard's disconnect marks only its own books stale
(via that socket's ``mark_all_stale``), and the others keep streaming.

All shard sockets run as concurrent asyncio tasks in one event loop under a
``TaskGroup``, so a HALT (unknown ``event_type`` = format change) in any shard
tears down the whole group — fail-loud, since a venue format change is global.

INVARIANT: the shared ``sink`` MUST be synchronous (no ``await``). ``MarketStream.
ingest`` stamps observed_at, mutates the shard's book, and calls the sink with no
suspension point in between; that atomicity (and thus deterministic cross-shard
ordering) holds only while the sink does not yield the loop. A blocking synchronous
sink is safe for ordering but can stall sibling shards, so the production sink must
also be FAST.

OFF-LOOP WRITES (POL-12 / C2): a per-frame ``EventStore.append`` commit IS slow and,
on the loop, stalled sibling shards — which is why prod shards were capped at 2. The
fix keeps the sink synchronous but fast: wrap the store in
``storage.event_writer.QueuedEventWriter`` so ``PersistingSink``'s ``append`` only
enqueues (microseconds, no I/O) and a dedicated thread commits off the loop. The
synchronous-sink invariant above is preserved (enqueue does not await); with the
writer in place the shard count may rise past the previously-cap-of-2.

Two caveats when raising the shard count: (1) all shards funnel into ONE writer thread,
so the cap is now bounded by that single committer's sustained throughput — exceed it and
the backlog hits ``max_queued`` and HALTs (fail-loud, by design), not silently. (2) the
off-loop writer adds a small hard-crash data-loss window (rows queued but not yet
committed) that the old on-loop commit did not have; see ``event_writer`` for the trade.
"""

import asyncio

from polybot.ingestion.market_socket import MarketSocket
from polybot.ingestion.market_stream import MarketStream


class ShardedMarketCollector:
    def __init__(
        self,
        connect,
        stamper,
        asset_ids,
        *,
        sink=None,
        max_assets_per_shard=25,
        detector=None,
        synthetic_sink=None,
        **socket_kwargs,
    ):
        if max_assets_per_shard <= 0:
            raise ValueError("max_assets_per_shard must be > 0")
        ids = list(asset_ids)
        if not ids:
            raise ValueError("asset_ids must be non-empty")
        seen = set()
        dupes = {a for a in ids if a in seen or seen.add(a)}
        if dupes:
            raise ValueError(f"duplicate asset_ids would be double-subscribed: {sorted(dupes)}")
        self._shards = []  # list[(MarketStream, MarketSocket)]
        self._stream_by_asset = {}  # asset_id -> owning shard's MarketStream
        for start in range(0, len(ids), max_assets_per_shard):
            chunk = ids[start:start + max_assets_per_shard]
            stream = MarketStream(stamper, sink=sink, asset_ids=chunk,
                                  detector=detector, synthetic_sink=synthetic_sink)
            socket = MarketSocket(connect, stream, asset_ids=chunk, **socket_kwargs)
            self._shards.append((stream, socket))
            for asset_id in chunk:
                self._stream_by_asset[asset_id] = stream

    @property
    def shard_count(self):
        return len(self._shards)

    def book_for(self, asset_id):
        stream = self._stream_by_asset.get(asset_id)
        return stream.book_for(asset_id) if stream is not None else None

    def last_frame_at(self):
        """MIN of the shard streams' ``last_frame_at`` (stamper-ns): collector
        health is the LAGGING shard's health. ``None`` if there are no shards or
        if ANY shard has not seen a frame yet -- one dead/silent shard means the
        collector cannot vouch for the whole universe (fail-closed; the L5 WS
        sentinel reads None as +inf age = down). Non-consuming.
        """
        if not self._shards:
            return None
        stamps = [stream.last_frame_at() for stream, _socket in self._shards]
        if any(stamp is None for stamp in stamps):
            return None
        return min(stamps)

    async def run(self, max_connections=1):
        async with asyncio.TaskGroup() as tg:
            for _stream, socket in self._shards:
                tg.create_task(socket.run(max_connections=max_connections))
