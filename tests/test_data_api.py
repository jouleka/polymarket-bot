"""Tests for the Data API poller core (POL-3 / S1)."""

import asyncio
import json

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.data_api import DataApiPoller
from polybot.storage.market_memory import EventStore


def _fetch_returning(items):
    async def fetch(path, params):
        return items

    return fetch


def test_poll_once_persists_each_item_as_an_envelope(tmp_path):
    items = [
        {"id": "t1", "conditionId": "0xabc", "price": "0.50"},
        {"id": "t2", "conditionId": "0xdef", "price": "0.40"},
    ]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    count = asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert count == 2
    events = store.all()
    assert [e.event_id for e in events] == ["/trades:t1", "/trades:t2"]
    assert events[0].source == "data-api"
    assert events[0].market_links == ("0xabc",)
    assert json.loads(events[0].content)["price"] == "0.50"


def test_poll_once_is_idempotent_on_item_id(tmp_path):
    items = [{"id": "t1", "conditionId": "0xabc"}]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))
    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))  # overlapping window

    assert len(store.all()) == 1


def test_poll_once_handles_items_without_market_link(tmp_path):
    items = [{"id": "lead1", "proxyWallet": "0xwallet", "pnl": "123"}]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/leaderboard", source_tier="DATA"))

    assert store.all()[0].market_links == ()
