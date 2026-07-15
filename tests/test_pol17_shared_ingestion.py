"""POL-17 shares D4a's one live collector instead of duplicating transport."""

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
