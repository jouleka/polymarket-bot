from polybot.ingestion.transport import WS_RECONNECT_ON
from scripts.book_resync_diagnostic import make_diagnostic_collector


async def _connect():
    raise AssertionError("construction test must not connect")


def test_diagnostic_collector_matches_sharding_without_persistence():
    token_ids = tuple(f"token-{index}" for index in range(26))

    collector = make_diagnostic_collector(
        token_ids,
        max_assets_per_shard=25,
        connect=_connect,
    )

    assert collector.shard_count == 2
    assert set(collector._stream_by_asset) == set(token_ids)
    assert all(stream._sink is None for stream, _socket in collector._shards)
    assert all(socket._reconnect_on == WS_RECONNECT_ON
               for _stream, socket in collector._shards)

