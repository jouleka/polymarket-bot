"""POL-17 top-level single-process composition root."""

import json
import time
from types import SimpleNamespace

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.news import NewsPoller
from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)
from polybot.runtime.shadow_root import build_shadow_runtime
from polybot.runtime.shadow_runtime import ShadowRuntime


def _config(tmp_path):
    path = lambda name: str(tmp_path / name)
    return ShadowRuntimeConfig(
        ingestion=IngestionConfig(
            db_path=path("market_memory.db"), data_api_enabled=False,
        ),
        intents_db_path=path("intents.db"),
        forecasts_db_path=path("forecasts.db"),
        components_db_path=path("components.db"),
        maker_db_path=path("maker.db"),
        shadow_db_path=path("shadow.db"),
        resolution_db_path=path("resolution.db"),
        polygon_providers=(
            ReadOnlyPolygonProviderConfig("a", "https://a.example"),
            ReadOnlyPolygonProviderConfig("b", "https://b.example"),
        ),
    )


def _snapshot():
    condition_id = "0x" + "11" * 32
    tokens = ("101", "202")
    market = {
        "conditionId": condition_id,
        "question": "Will X happen?",
        "endDate": "2030-01-01T00:00:00Z",
        "clobTokenIds": json.dumps(list(tokens)),
        "events": [{"id": "event-1"}],
        "acceptingOrders": True,
        "volume24hr": 10,
        "active": True,
        "closed": False,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
    }
    event = {
        "id": "event-1",
        "tags": [{"id": "2"}],
        "markets": [{
            "conditionId": condition_id,
            "clobTokenIds": json.dumps(list(tokens)),
        }],
    }
    return [market], [event]


def test_root_shares_one_gamma_generation_and_one_live_collector(tmp_path):
    snapshot_calls = []

    def gamma_snapshot_fetch():
        snapshot_calls.append(1)
        return _snapshot()

    providers = (
        SimpleNamespace(provider_id="a"),
        SimpleNamespace(provider_id="b"),
    )
    noop = SimpleNamespace(acquire=lambda: None, release=lambda: None)
    readiness = SimpleNamespace(ready=lambda: None, stopping=lambda: None)
    history_stamper = MonotonicStamper(clock=lambda: time.time_ns())
    health_stamper = MonotonicStamper(clock=lambda: time.monotonic_ns())

    async def news_fetch(_url):
        return "<rss><channel></channel></rss>"

    runtime = build_shadow_runtime(
        _config(tmp_path),
        gamma_snapshot_fetch=gamma_snapshot_fetch,
        resolution_providers=providers,
        ws_connect=object(),
        data_fetch=object(),
        history_stamper=history_stamper,
        health_stamper=health_stamper,
        news_fetch=news_fetch,
        lock=noop,
        readiness=readiness,
    )
    try:
        assert isinstance(runtime, ShadowRuntime)
        assert snapshot_calls == [1]
        assert runtime._collector is runtime._ingestion.collector
        assert runtime._components.controller._book_for.__self__ is runtime._ingestion
        assert runtime._components.pipeline.market_meta is runtime._components.market_registry
        assert runtime._ingestion.token_ids == ("101", "202")
        assert isinstance(runtime._news_poller, NewsPoller)
        assert runtime._components.intent_store.pending() == []
        evidence = runtime._harness.update()
        assert evidence.reports
        assert all(not decision.promote_recommended
                   for decision in evidence.decisions.values())
    finally:
        runtime.close_unstarted()
