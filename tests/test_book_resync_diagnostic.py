import asyncio

import pytest

from polybot.ingestion.transport import WS_RECONNECT_ON
from scripts import book_resync_diagnostic as diagnostic
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


def test_diagnostic_run_holds_production_lock_through_collection(monkeypatch):
    events = []

    class Lock:
        def __init__(self, path):
            events.append(("lock-created", path))

        def acquire(self):
            events.append("lock-acquired")

        def release(self):
            events.append("lock-released")

    class Config:
        gamma_url = "https://gamma.invalid"
        max_assets_per_shard = 25

    class Collector:
        shard_count = 1

        async def run(self, *, max_connections):
            events.append(("collector-run", max_connections))

    def load_config(path):
        events.append(("config-loaded", path))
        return Config()

    monkeypatch.setattr(diagnostic, "load_config", load_config)
    monkeypatch.setattr(diagnostic, "make_gamma_fetch", lambda _url: object())
    monkeypatch.setattr(diagnostic, "discover_universe", lambda _fetch, _config: ("A",))
    monkeypatch.setattr(diagnostic, "make_diagnostic_collector",
                        lambda *_args, **_kwargs: Collector())

    asyncio.run(diagnostic.run("config.toml", seconds=1, lock_factory=Lock))

    assert events == [
        ("lock-created", diagnostic.LOCK_PATH),
        "lock-acquired",
        ("config-loaded", "config.toml"),
        ("collector-run", None),
        "lock-released",
    ]


def test_diagnostic_run_releases_production_lock_on_failure(monkeypatch):
    events = []

    class Lock:
        def __init__(self, _path):
            pass

        def acquire(self):
            events.append("acquired")

        def release(self):
            events.append("released")

    class Config:
        gamma_url = "https://gamma.invalid"
        max_assets_per_shard = 25

    class Collector:
        shard_count = 1

        async def run(self, *, max_connections):
            raise RuntimeError(f"probe failed ({max_connections})")

    monkeypatch.setattr(diagnostic, "load_config", lambda _path: Config())
    monkeypatch.setattr(diagnostic, "make_gamma_fetch", lambda _url: object())
    monkeypatch.setattr(diagnostic, "discover_universe", lambda _fetch, _config: ("A",))
    monkeypatch.setattr(diagnostic, "make_diagnostic_collector",
                        lambda *_args, **_kwargs: Collector())

    with pytest.raises(RuntimeError, match="probe failed"):
        asyncio.run(diagnostic.run("config.toml", seconds=1, lock_factory=Lock))

    assert events == ["acquired", "released"]
