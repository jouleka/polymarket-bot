"""Top-level single-process POL-17 composition root."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import threading
import time

from polybot.ers.heartbeat import Heartbeat
from polybot.ers.facade import ProposeOnlyFacade
from polybot.ers.market_meta import MarketSnapshotError
from polybot.hermes.read_views import (
    BookReadView,
    FlagsReadView,
    LedgerReadView,
    MarketReadView,
)
from polybot.hermes.rpc import (
    ProposalRateLimiter,
    ProposalRpcDispatcher,
    ProposalRpcServer,
)
from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST
from polybot.ingestion.news import NewsPoller
from polybot.ers.market_meta import DEFAULT_CATEGORY_POLICY
from polybot.harness.evidence import evaluate_category
from polybot.runtime.harness_runtime import HarnessEvidenceRuntime
from polybot.runtime.ingestion import build_ingestion_assembly
from polybot.runtime.registry_provider import FixedUniverseRegistryProvider
from polybot.runtime.shadow_build import build_shadow_components
from polybot.runtime.shadow_adapters import StopAwareResolutionProvider
from polybot.runtime.shadow_cycle import (
    ShadowCycleCoordinator,
    make_resolution_batch,
)
from polybot.runtime.shadow_runtime import ShadowRuntime
from polybot.runtime.status import RuntimeStatusReporter


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
                         extra_closers=(), lock_acquired=False,
                         proposal_socket_group_gid=None):
    """Construct the real paper runtime from one Gamma generation and one collector."""
    if config.proposal_socket_path is not None and (
            isinstance(proposal_socket_group_gid, bool)
            or not isinstance(proposal_socket_group_gid, int)
            or proposal_socket_group_gid < 0):
        raise ValueError("configured proposal endpoint requires its resolved group gid")
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
    worker_stop = threading.Event()
    construction_closers = [
        ingestion.writer.close,
        lambda: executor.shutdown(wait=True),
    ]

    def guarded(factory):
        try:
            return factory()
        except Exception as construction_error:
            cleanup_errors = []
            for close in reversed(construction_closers):
                try:
                    close()
                except Exception as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                raise ExceptionGroup(
                    "shadow root construction cleanup failed",
                    [construction_error, *cleanup_errors],
                ) from construction_error
            raise

    async def run_blocking(call, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, partial(call, *args))

    components = guarded(lambda: build_shadow_components(
        config,
        ingestion=ingestion,
        registry_provider=registry_provider,
        resolution_providers=tuple(
            StopAwareResolutionProvider(provider, worker_stop.is_set)
            for provider in resolution_providers
        ),
        wall_clock=time.time,
        health_clock_seconds=time.monotonic,
        health_clock_ns=time.monotonic_ns,
    ))
    construction_closers.append(components.close)
    heartbeat = (
        Heartbeat(config.ingestion.heartbeat_path).beat
        if config.ingestion.heartbeat_path else (lambda: None)
    )
    news_poller = guarded(lambda: NewsPoller(
        news_fetch, history_stamper, ingestion.writer, DEFAULT_ALLOWLIST
    ))
    last_news_results = {}
    proposal_admission = {"enabled": False}

    async def poll_news():
        while True:
            last_news_results.clear()
            last_news_results.update(await news_poller.poll_all())
            await asyncio.sleep(config.news_poll_seconds)

    categories = DEFAULT_CATEGORY_POLICY.precedence
    harness = guarded(lambda: HarnessEvidenceRuntime(
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
    ))

    def live_book_tokens():
        available = []
        for token_id in registry_provider.available_token_ids:
            book = ingestion.book_for(token_id)
            if (book is not None and not book.is_stale()
                    and book.midpoint() is not None):
                available.append(token_id)
        return tuple(available)

    def live_book_ready():
        return bool(live_book_tokens())

    proposal_server = None
    proposal_facade = None
    if config.proposal_socket_path is not None:
        def registry_fresh():
            try:
                registry_provider.require_fresh()
            except MarketSnapshotError:
                return False
            return True

        proposal_facade = guarded(lambda: ProposeOnlyFacade(
            components.intent_store,
            market_reader=MarketReadView(registry_provider),
            book_reader=BookReadView(
                ingestion.book_for, token_ids=ingestion.token_ids,
            ),
            ledger_reader=LedgerReadView(
                components.forecast_ledger, categories=categories,
            ),
            flags_reader=FlagsReadView(
                runtime_ready=lambda: proposal_admission["enabled"],
                controller_state=lambda: components.controller._controller.state(),
                resolution_state=components.resolution_store.runtime_state,
                registry_fresh=registry_fresh,
                live_book_tokens=live_book_tokens,
            ),
        ))
        proposal_server = guarded(lambda: ProposalRpcServer(
            config.proposal_socket_path,
            ProposalRpcDispatcher(
                proposal_facade,
                proposal_gate=ProposalRateLimiter(
                    config.proposal_max_per_minute, 60.0,
                ),
            ),
            runtime_ready=lambda: proposal_admission["enabled"],
            socket_group=proposal_socket_group_gid,
            request_timeout_seconds=config.proposal_request_timeout_seconds,
        ))

    status_reporter = RuntimeStatusReporter(
        config.status_path, readiness=readiness
    )

    def update_status():
        dispositions = {}
        for result in cycle.last_resolution_results:
            name = result.disposition.value
            dispositions[name] = dispositions.get(name, 0) + 1
        status_reporter.update({
            "controller": components.controller._controller.state(),
            "pending_intents": len(components.intent_store.pending()),
            "resolution_outbox": len(
                components.resolution_store.pending_outbox(2**31 - 1)
            ),
            "execution_outbox": len(
                components.intent_store.pending_shadow_executions(2**31 - 1)
            ),
            "resolution_dispositions": dispositions,
            "registry_error": cycle.last_registry_error,
            "news_failures": sorted(
                name for name, value in last_news_results.items()
                if isinstance(value, Exception)
            ),
            "promotion_recommendations": sorted(
                category for category, decision in harness.latest.decisions.items()
                if decision.promote_recommended
            ),
        })

    cycle = guarded(lambda: ShadowCycleCoordinator(
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
        status_update=update_status,
        run_blocking=run_blocking,
        clock=time.monotonic,
        registry_refresh_seconds=config.registry_refresh_seconds,
        resolution_poll_seconds=config.resolution_poll_seconds,
        outbox_batch_limit=config.outbox_batch_limit,
    ))

    async def recover_resolution():
        await run_blocking(components.resolution_feed.validate_providers)
        await run_blocking(components.resolution_feed.recover_pending)

    def apply_initial_resolution_state():
        state = components.resolution_store.runtime_state()
        components.controller.apply_resolution_state(
            terminal_condition_ids=state.terminal_condition_ids,
            frozen_condition_ids=state.frozen_condition_ids,
        )

    runtime_services = ingestion.services + (poll_news,)
    if proposal_server is not None:
        runtime_services += (proposal_server.run,)

    def set_proposal_admission(enabled):
        proposal_admission["enabled"] = enabled

    runtime = guarded(lambda: ShadowRuntime(
        services=runtime_services,
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
        before_writer_closers=(
            worker_stop.set, lambda: executor.shutdown(wait=True),
        ),
        closers=tuple(extra_closers) + (components.close,),
        lock_acquired=lock_acquired,
        stop_requested=worker_stop.set,
        set_proposal_admission=(
            set_proposal_admission if proposal_server is not None else None
        ),
        live_book_ready=live_book_ready,
    ))
    construction_closers.clear()  # ownership transferred to ShadowRuntime
    # Introspection is intentional for review/tests; none of these grants mutation
    # authority to Hermes or changes the runtime's public lifecycle surface.
    runtime._ingestion = ingestion
    runtime._components = components
    runtime._registry_provider = registry_provider
    runtime._cycle = cycle
    runtime._collector = ingestion.collector
    runtime._news_poller = news_poller
    runtime._harness = harness
    runtime._status_reporter = status_reporter
    runtime._proposal_server = proposal_server
    runtime._proposal_facade = proposal_facade
    return runtime
