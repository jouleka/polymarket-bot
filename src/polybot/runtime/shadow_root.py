"""Top-level single-process POL-17 composition root."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time

from polybot.ers.heartbeat import Heartbeat
from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST
from polybot.ingestion.news import NewsPoller
from polybot.ers.market_meta import DEFAULT_CATEGORY_POLICY
from polybot.harness.evidence import evaluate_category
from polybot.runtime.harness_runtime import HarnessEvidenceRuntime
from polybot.runtime.ingestion import build_ingestion_assembly
from polybot.runtime.registry_provider import FixedUniverseRegistryProvider
from polybot.runtime.shadow_build import build_shadow_components
from polybot.runtime.shadow_cycle import (
    ShadowCycleCoordinator,
    make_resolution_batch,
)
from polybot.runtime.shadow_runtime import ShadowRuntime


def _drain_fully(dispatcher, limit):
    def drain():
        while True:
            count = dispatcher.drain(limit)
            if count < limit:
                return
    return drain


def build_shadow_runtime(config, *, gamma_snapshot_fetch, resolution_providers,
                         ws_connect=None, data_fetch=None, history_stamper,
                         health_stamper, news_fetch, lock, readiness,
                         extra_closers=(), lock_acquired=False):
    """Construct the real paper runtime from one Gamma generation and one collector."""
    registry_provider = FixedUniverseRegistryProvider(
        fetch_snapshot=gamma_snapshot_fetch,
        wall_clock=time.time,
        age_clock=time.monotonic,
        max_age_seconds=config.registry_max_age_seconds,
    )
    registry_provider.load()
    ingestion = build_ingestion_assembly(
        config.ingestion,
        gamma_fetch=lambda _params: list(registry_provider.market_rows),
        ws_connect=ws_connect,
        data_fetch=data_fetch,
        stamper=history_stamper,
        health_stamper=health_stamper,
    )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pol17-blocking")

    async def run_blocking(call, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, partial(call, *args))

    components = build_shadow_components(
        config,
        ingestion=ingestion,
        registry_provider=registry_provider,
        resolution_providers=resolution_providers,
        wall_clock=time.time,
        health_clock_seconds=time.monotonic,
        health_clock_ns=time.monotonic_ns,
    )
    heartbeat = (
        Heartbeat(config.ingestion.heartbeat_path).beat
        if config.ingestion.heartbeat_path else (lambda: None)
    )
    news_poller = NewsPoller(
        news_fetch, history_stamper, ingestion.writer, DEFAULT_ALLOWLIST
    )

    async def poll_news():
        while True:
            await news_poller.poll_all()
            await asyncio.sleep(config.news_poll_seconds)

    categories = DEFAULT_CATEGORY_POLICY.precedence
    harness = HarnessEvidenceRuntime(
        categories=categories,
        evaluate=lambda category: evaluate_category(
            category,
            shadow_ledger=components.shadow_ledger,
            forecast_ledger=components.forecast_ledger,
            calibration_gate=components.calibration_gate,
            maker_gate=components.maker_gate,
            ramp_config=components.ramp_config,
            maker_config=components.maker_config,
            family_size=len(categories),
        ),
        ramp_controller=components.ramp_controller,
        portfolio_for=components.controller.current_portfolio,
    )

    cycle = ShadowCycleCoordinator(
        heartbeat=heartbeat,
        registry_provider=registry_provider,
        subjects_for=lambda registry: make_resolution_batch(
            components.forecast_ledger, components.intent_store, registry
        ),
        resolution_feed=components.resolution_feed,
        resolution_dispatcher=components.resolution_dispatcher,
        controller=components.controller,
        execution_dispatcher=components.execution_dispatcher,
        evidence_update=harness.update,
        status_update=lambda: None,
        run_blocking=run_blocking,
        clock=time.monotonic,
        registry_refresh_seconds=config.registry_refresh_seconds,
        resolution_poll_seconds=config.resolution_poll_seconds,
        outbox_batch_limit=config.outbox_batch_limit,
    )

    async def recover_resolution():
        await run_blocking(components.resolution_feed.recover_pending)

    def apply_initial_resolution_state():
        state = components.resolution_store.runtime_state()
        components.controller.apply_resolution_state(
            terminal_condition_ids=state.terminal_condition_ids,
            frozen_condition_ids=state.frozen_condition_ids,
        )

    runtime = ShadowRuntime(
        services=ingestion.services + (poll_news,),
        writer=ingestion.writer,
        lock=lock,
        recover_resolution=recover_resolution,
        drain_resolution=_drain_fully(
            components.resolution_dispatcher, config.outbox_batch_limit
        ),
        drain_execution=_drain_fully(
            components.execution_dispatcher, config.outbox_batch_limit
        ),
        collector=ingestion.collector,
        apply_initial_resolution_state=apply_initial_resolution_state,
        controller=components.controller,
        readiness=readiness,
        run_cycle=cycle.run_cycle,
        cycle_interval_seconds=config.cycle_interval_seconds,
        readiness_timeout_seconds=config.readiness_timeout_seconds,
        closers=tuple(extra_closers) + (
            components.close, lambda: executor.shutdown(wait=True),
        ),
        lock_acquired=lock_acquired,
    )
    # Introspection is intentional for review/tests; none of these grants mutation
    # authority to Hermes or changes the runtime's public lifecycle surface.
    runtime._ingestion = ingestion
    runtime._components = components
    runtime._registry_provider = registry_provider
    runtime._cycle = cycle
    runtime._collector = ingestion.collector
    runtime._news_poller = news_poller
    runtime._harness = harness
    return runtime
