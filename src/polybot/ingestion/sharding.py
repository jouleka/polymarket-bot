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
ordering) holds only while the sink does not yield the loop. (A blocking
synchronous sink is safe for ordering but can stall sibling shards — batching /
off-loop writes are a separate follow-up before scaling to many shards.)
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
        max_assets_per_shard=500,
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
            stream = MarketStream(stamper, sink=sink, asset_ids=chunk)
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

    async def run(self, max_connections=1):
        async with asyncio.TaskGroup() as tg:
            for _stream, socket in self._shards:
                tg.create_task(socket.run(max_connections=max_connections))
