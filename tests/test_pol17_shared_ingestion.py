"""POL-17 shares D4a's one live collector instead of duplicating transport."""

import asyncio

import pytest

from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import build_ingestion_assembly


def test_shared_ingestion_assembly_exposes_the_snapshotters_live_collector(
        tmp_path, monkeypatch):
    from polybot.runtime import ingestion

    captured = {"collector_count": 0}

    class Collector:
        def __init__(self, _connect, _stamper, token_ids, *, sink, **_kwargs):
            captured["collector_count"] += 1
            captured["sink"] = sink
            self.token_ids = tuple(token_ids)

        def book_for(self, token_id):
            return ("live", token_id)

        async def run(self, max_connections=None):
            raise AssertionError("not run by construction test")

    class Snapshotter:
        def __init__(self, **kwargs):
            captured["snapshot_book_for"] = kwargs["book_for"]

        async def run(self):
            raise AssertionError("not run by construction test")

    monkeypatch.setattr(ingestion, "ShardedMarketCollector", Collector)
    monkeypatch.setattr(ingestion, "MidpointSnapshotter", Snapshotter)
    config = IngestionConfig(
        db_path=str(tmp_path / "market_memory.db"),
        data_api_enabled=False,
    )

    assembly = build_ingestion_assembly(
        config,
        gamma_fetch=lambda _params: [{
            "conditionId": "c1",
            "acceptingOrders": True,
            "volume24hr": 10,
            "active": True,
            "closed": False,
            "clobTokenIds": '["t1", "t2"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
        }],
        ws_connect=object(),
        data_fetch=object(),
        stamper=object(),
    )
    try:
        assert captured["collector_count"] == 1
        assert captured["sink"] is None
        assert assembly.token_ids == ("t1", "t2")
        assert captured["snapshot_book_for"].__self__ is assembly.collector
        assert assembly.book_for("t1") == ("live", "t1")
    finally:
        assembly.writer.close()


def test_pol17_can_separate_live_health_from_persisted_history_clocks(
        tmp_path, monkeypatch):
    from polybot.runtime import ingestion

    captured = {}

    class Collector:
        def __init__(self, _connect, stamper, _token_ids, *, sink, **_kwargs):
            captured["collector_stamper"] = stamper
            assert sink is None

        def book_for(self, _token_id):
            return None

        async def run(self, max_connections=None):
            raise AssertionError("not run")

    class Snapshotter:
        def __init__(self, **kwargs):
            captured["snapshot_stamper"] = kwargs["stamper"]

        async def run(self):
            raise AssertionError("not run")

    monkeypatch.setattr(ingestion, "ShardedMarketCollector", Collector)
    monkeypatch.setattr(ingestion, "MidpointSnapshotter", Snapshotter)
    history_stamper = object()
    health_stamper = object()
    config = IngestionConfig(
        db_path=str(tmp_path / "market_memory.db"),
        data_api_enabled=False,
    )

    assembly = build_ingestion_assembly(
        config,
        gamma_fetch=lambda _params: [{
            "conditionId": "c1", "acceptingOrders": True, "volume24hr": 10,
            "active": True, "closed": False,
            "clobTokenIds": '["t1", "t2"]', "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
        }],
        ws_connect=object(),
        data_fetch=object(),
        stamper=history_stamper,
        health_stamper=health_stamper,
    )
    try:
        assert captured == {
            "collector_stamper": health_stamper,
            "snapshot_stamper": history_stamper,
        }
        assert assembly.stamper is history_stamper
        assert assembly.health_stamper is health_stamper
    finally:
        assembly.writer.close()


def test_every_assembled_service_schedules_the_websocket_collector_exactly_once(
        tmp_path, monkeypatch):
    from polybot.runtime import ingestion

    calls = []

    class Collector:
        def __init__(self, *_args, **_kwargs):
            pass

        def book_for(self, _token_id):
            return None

        async def run(self, max_connections=None):
            calls.append(("collector", max_connections))

    class Snapshotter:
        def __init__(self, **_kwargs):
            pass

        async def run(self):
            calls.append(("snapshotter", None))

    monkeypatch.setattr(ingestion, "ShardedMarketCollector", Collector)
    monkeypatch.setattr(ingestion, "MidpointSnapshotter", Snapshotter)
    assembly = build_ingestion_assembly(
        IngestionConfig(
            db_path=str(tmp_path / "market_memory.db"), data_api_enabled=False,
        ),
        gamma_fetch=lambda _params: [{
            "conditionId": "c1", "acceptingOrders": True, "volume24hr": 10,
            "active": True, "closed": False,
            "clobTokenIds": '["t1", "t2"]', "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
        }],
        ws_connect=object(), data_fetch=object(), stamper=object(),
    )
    try:
        for service in assembly.services:
            with pytest.raises(RuntimeError, match="returned unexpectedly"):
                asyncio.run(service())
        assert calls == [
            ("collector", None),
            ("snapshotter", None),
        ]
    finally:
        assembly.writer.close()
