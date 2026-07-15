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
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import MarketRegistry
from polybot.ers.service import HermesPipeline, PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.fusion.component_log import ComponentLog
from polybot.harness.execution import ShadowExecutionDispatcher
from polybot.harness.ledger import ShadowLedger
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.feed import ResolutionFeed
from polybot.resolution.store import ResolutionStore
from polybot.runtime.config import IngestionConfig
import polybot.runtime.shadow_build as shadow_build
from polybot.runtime.shadow_build import ShadowComponents, build_shadow_components
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)
from polybot.storage.market_memory import EventStore, ReadOnlyEventStore
from polybot.ingestion.orderbook import LocalBook


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


def test_component_factory_wires_real_paper_safety_and_authority_types(
        tmp_path, monkeypatch):
    startup_checks = []
    monkeypatch.setattr(
        shadow_build,
        "verify_or_refuse",
        lambda caps, **kwargs: startup_checks.append((caps, kwargs)),
        raising=False,
    )
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
        assert len(startup_checks) == 1
        assert startup_checks[0][1]["expected_caps_hash"] == (
            "9c5265736b4930c1d8270788e3543c1d9144454cf4e99407520da4862c7b03ab"
        )
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


def test_production_planner_vetoes_accept_when_second_live_book_has_no_fill(tmp_path):
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
            # This is the second, execution-authority fetch: unavailable.
            book_for=lambda _token_id: None,
        ),
        registry_provider=SimpleNamespace(require_fresh=lambda: registry),
        resolution_providers=(
            SimpleNamespace(provider_id="a"),
            SimpleNamespace(provider_id="b"),
        ),
        wall_clock=lambda: 1_750_000_000.25,
        health_clock_seconds=time.monotonic,
        health_clock_ns=time.monotonic_ns,
    )
    validation_book = LocalBook()
    validation_book.apply_book({
        "bids": [{"price": "0.48", "size": "1000"}],
        "asks": [{"price": "0.52", "size": "1000"}],
    })
    try:
        components.intent_store.propose_trade(
            "intent", token_id="101", condition_id="0x" + "11" * 32,
            event_id="event-1", side="BUY", target_price="0.49",
            max_price="0.60", size_usd_suggestion="12", p="0.90",
            p_confidence="0.75",
        )

        process_pending(
            components.intent_store,
            book_for=lambda _token_id: validation_book,
            portfolio=Portfolio(nav=RiskCaps().nav),
            caps=RiskCaps(),
            signer=PaperSigner(),
            shadow_planner=components.controller._shadow_planner,
            accept_wall_clock=lambda: 1_750_000_000.25,
        )

        assert components.intent_store.get("intent").status == "REJECTED"
        assert components.intent_store.fills_log() == []
        assert components.intent_store.pending_shadow_executions(10) == ()
    finally:
        components.close()


def test_component_construction_unwinds_opened_stores_on_later_failure(
        tmp_path, monkeypatch):
    trace = []

    class Opened:
        def __init__(self, name):
            self.name = name
            trace.append(f"open_{name}")

        def close(self):
            trace.append(f"close_{self.name}")

    monkeypatch.setattr(
        shadow_build, "ReadOnlyEventStore", lambda _path: Opened("events")
    )
    monkeypatch.setattr(
        shadow_build, "IntentStore", lambda _path, _stamper: Opened("intents")
    )

    def fail_forecast(_path, _stamper):
        trace.append("open_forecasts")
        raise RuntimeError("forecast schema failed")

    monkeypatch.setattr(shadow_build, "ForecastLedger", fail_forecast)

    with pytest.raises(RuntimeError, match="forecast schema"):
        build_shadow_components(
            _config(tmp_path),
            ingestion=SimpleNamespace(stamper=MonotonicStamper()),
            registry_provider=object(),
            resolution_providers=(),
            wall_clock=time.time,
            health_clock_seconds=time.monotonic,
            health_clock_ns=time.monotonic_ns,
        )

    assert trace == [
        "open_events", "open_intents", "open_forecasts",
        "close_intents", "close_events",
    ]
