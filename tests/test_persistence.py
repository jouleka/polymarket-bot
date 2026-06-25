"""Tests for the market-data persistence sink (POL-3 / S1)."""

import json

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.persistence import PersistingSink
from polybot.storage.market_memory import EventStore


def _book(asset_id, bids, asks, **extra):
    message = {
        "event_type": "book",
        "asset_id": asset_id,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }
    message.update(extra)
    return message


def test_persisting_sink_records_observation_to_store(tmp_path):
    store = EventStore(str(tmp_path / "mm.db"))
    stream = MarketStream(MonotonicStamper(clock=lambda: 5), sink=PersistingSink(store))

    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    events = store.all()
    assert len(events) == 1
    env = events[0]
    assert env.source == "clob-ws"
    assert env.market_links == ("A",)
    assert env.observed_at == 5
    assert json.loads(env.content)["event_type"] == "book"


def test_persisting_sink_survives_restart(tmp_path):
    path = str(tmp_path / "mm.db")
    with EventStore(path) as store:
        stream = MarketStream(MonotonicStamper(clock=lambda: 5), sink=PersistingSink(store))
        stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    with EventStore(path) as reopened:
        assert len(reopened.all()) == 1


def test_persisting_sink_dedups_on_stable_frame_hash(tmp_path):
    store = EventStore(str(tmp_path / "mm.db"))
    stream = MarketStream(MonotonicStamper(clock=lambda: 5), sink=PersistingSink(store))

    # Same frame (stable hash) re-delivered after a reconnect snapshot.
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")], hash="abc"))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")], hash="abc"))

    assert len(store.all()) == 1
