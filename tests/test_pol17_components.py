"""POL-17 real safety/settlement/shadow component composition."""

import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from types import SimpleNamespace

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import MarketRegistry
from polybot.ers.service import HermesPipeline, PaperSigner
from polybot.fusion.component_log import ComponentLog
from polybot.harness.execution import ShadowExecutionDispatcher
from polybot.harness.ledger import ShadowLedger
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.feed import ResolutionFeed
from polybot.resolution.store import ResolutionStore
from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_build import ShadowComponents, build_shadow_components
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)
from polybot.storage.market_memory import EventStore, ReadOnlyEventStore


def _config(tmp_path):
    path = lambda name: str(tmp_path / name)
    return ShadowRuntimeConfig(
        ingestion=IngestionConfig(db_path=path("market_memory.db")),
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


def _registry():
    condition_id = "0x" + "11" * 32
    tokens = ("101", "202")
    return MarketRegistry.from_gamma_snapshots(
        [{
            "conditionId": condition_id,
            "question": "Will X happen?",
            "endDate": "2030-01-01T00:00:00Z",
            "clobTokenIds": json.dumps(list(tokens)),
            "events": [{"id": "event-1"}],
        }],
        [{
            "id": "event-1",
            "tags": [{"id": "2"}],
            "markets": [{
                "conditionId": condition_id,
                "clobTokenIds": json.dumps(list(tokens)),
            }],
        }],
        clock=time.time,
    )


def test_component_factory_wires_real_paper_safety_and_authority_types(tmp_path):
    config = _config(tmp_path)
    with EventStore(config.ingestion.db_path):
        pass
    registry = _registry()
    registry_provider = SimpleNamespace(require_fresh=lambda: registry)
    providers = (
        SimpleNamespace(provider_id="a"),
        SimpleNamespace(provider_id="b"),
    )
    health_ns = time.monotonic_ns()
    ingestion = SimpleNamespace(
        stamper=MonotonicStamper(),
        collector=SimpleNamespace(last_frame_at=lambda: health_ns),
        book_for=lambda _token_id: None,
    )

    components = build_shadow_components(
        config,
        ingestion=ingestion,
        registry_provider=registry_provider,
        resolution_providers=providers,
        wall_clock=lambda: 1_750_000_000.25,
        health_clock_seconds=lambda: health_ns / 1e9,
        health_clock_ns=lambda: health_ns,
    )
    try:
        assert isinstance(components.intent_store, IntentStore)
        assert isinstance(components.forecast_ledger, ForecastLedger)
        assert isinstance(components.component_log, ComponentLog)
        assert isinstance(components.maker_ledger, MakerLedger)
        assert isinstance(components.shadow_ledger, ShadowLedger)
        assert isinstance(components.resolution_store, ResolutionStore)
        assert isinstance(components.event_reader, ReadOnlyEventStore)
        assert isinstance(components.pipeline, HermesPipeline)
        assert isinstance(components.signer, PaperSigner)
        assert isinstance(components.controller, ERSController)
        assert isinstance(components.resolution_feed, ResolutionFeed)
        assert isinstance(components.resolution_dispatcher, ResolutionDispatcher)
        assert isinstance(components.execution_dispatcher, ShadowExecutionDispatcher)
        assert callable(components.maker_mark_for)
        assert callable(components.shadow_mark_for)
        assert components.maker_mark_for("unknown-token") is None
        assert components.shadow_mark_for("unknown-token") is None
        assert components.controller._anomaly is not None
        assert components.controller._lossbreakers is not None
        assert components.controller._reconciler is not None
        assert components.controller._accept_wall_clock is not None
        assert components.controller._controller._flow_gate is not None
        assert "signer" not in inspect.signature(build_shadow_components).parameters
        assert "private_key" not in inspect.signature(build_shadow_components).parameters
    finally:
        components.close()


def test_component_shutdown_attempts_every_owned_store_close():
    trace = []
    values = {
        field.name: None for field in fields(ShadowComponents)
        if field.init and field.name != "_closers"
    }
    components = ShadowComponents(
        **values,
        _closers=(
            lambda: trace.append("first"),
            lambda: (_ for _ in ()).throw(RuntimeError("second failed")),
        ),
    )

    with pytest.raises(ExceptionGroup, match="component shutdown"):
        components.close()

    assert trace == ["first"]


def test_resolution_store_is_owned_by_the_serial_blocking_worker(tmp_path):
    config = _config(tmp_path)
    with EventStore(config.ingestion.db_path):
        pass
    registry = _registry()
    health_ns = time.monotonic_ns()
    components = build_shadow_components(
        config,
        ingestion=SimpleNamespace(
            stamper=MonotonicStamper(),
            collector=SimpleNamespace(last_frame_at=lambda: health_ns),
            book_for=lambda _token_id: None,
        ),
        registry_provider=SimpleNamespace(require_fresh=lambda: registry),
        resolution_providers=(
            SimpleNamespace(provider_id="a"),
            SimpleNamespace(provider_id="b"),
        ),
        wall_clock=time.time,
        health_clock_seconds=time.monotonic,
        health_clock_ns=time.monotonic_ns,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(
                components.resolution_feed.recover_pending
            ).result() == 0
    finally:
        components.close()
