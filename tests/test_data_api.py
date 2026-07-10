"""Tests for the Data API poller core (POL-3 / S1)."""

import asyncio
import json

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.data_api import DataApiPoller
from polybot.ingestion.ratelimit import RateLimiter
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


def test_trade_tape_retains_the_complete_source_item(tmp_path):
    item = {
        "id": "t-full",
        "conditionId": "0xabc",
        "asset": "123",
        "price": "0.50",
        "size": "17.25",
        "side": "BUY",
        "proxyWallet": "0xwallet",
        "timestamp": "1719331200000",
        "transactionHash": "0xhash",
        "feeRateBps": "25",
        "outcome": "Yes",
        "title": "full fidelity sentinel",
        "futureVenueField": {"nested": ["must", "survive"]},
    }
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning([item]), MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert json.loads(store.all()[0].content) == item


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


def test_poll_once_skips_items_without_an_id_without_losing_the_rest(tmp_path):
    items = [
        {"id": "a", "conditionId": "0x1"},
        {"conditionId": "0x2"},  # no id key at all -> must be skipped, not fatal
        {"id": "c", "conditionId": "0x3"},
    ]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    count = asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert count == 2
    assert [e.event_id for e in store.all()] == ["/trades:a", "/trades:c"]


def test_poll_once_unwraps_paginated_data_envelope(tmp_path):
    response = {"data": [{"id": "t1", "conditionId": "0xabc"}], "next_cursor": "xyz"}

    async def fetch(path, params):
        return response

    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(fetch, MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert [e.event_id for e in store.all()] == ["/trades:t1"]


def test_poll_once_rejects_unexpected_response_shape(tmp_path):
    async def fetch(path, params):
        return {"unexpected": "shape"}

    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(fetch, MonotonicStamper(clock=lambda: 1), store)

    with pytest.raises(TypeError):
        asyncio.run(poller.poll_once("/trades", source_tier="DATA"))


def test_poll_once_links_all_present_market_keys(tmp_path):
    items = [{"id": "t1", "conditionId": "0xcond", "asset": "12345"}]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert store.all()[0].market_links == ("0xcond", "12345")


def test_poll_once_captures_item_timestamp_as_published_at(tmp_path):
    items = [{"id": "t1", "timestamp": "1719331200000"}]
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(_fetch_returning(items), MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.poll_once("/trades", source_tier="DATA"))

    assert store.all()[0].published_at == 1719331200000


def test_run_polls_repeatedly_until_max_polls(tmp_path):
    calls = []

    async def fetch(path, params):
        calls.append(path)
        return [{"id": f"t{len(calls)}", "conditionId": "0x1"}]

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(fetch, MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.run("/trades", interval=2.0, sleep=sleep, max_polls=3))

    assert len(calls) == 3
    assert len(store.all()) == 3            # three distinct ids persisted
    assert sleeps.count(2.0) >= 2           # interval slept between polls


def test_run_waits_for_the_rate_limiter(tmp_path):
    async def fetch(path, params):
        return []

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    now = [0.0]
    limiter = RateLimiter(rate_per_sec=1, capacity=1, clock=lambda: now[0])
    store = EventStore(str(tmp_path / "mm.db"))
    poller = DataApiPoller(fetch, MonotonicStamper(clock=lambda: 1), store)

    asyncio.run(poller.run("/trades", interval=0, limiter=limiter, sleep=sleep, max_polls=2))

    # capacity 1, frozen clock: 2nd poll must wait ~1s for a refilled token
    assert any(d == pytest.approx(1.0) for d in sleeps)
