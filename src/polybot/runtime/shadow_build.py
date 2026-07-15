"""Real paper-only component construction for POL-17."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from polybot.calibration.config import CalibrationConfig
from polybot.calibration.gate import CalibrationGate
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.prior import PriorEngine
from polybot.detectors.config import DetectorConfig
from polybot.detectors.orchestrator import DetectorOrchestrator
from polybot.ers.anomaly import AnomalyMonitor
from polybot.ers.breaker import DrawdownBreaker
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.flow import make_flow_gate
from polybot.ers.gtd import derive_bracket
from polybot.ers.intent_store import IntentStore
from polybot.ers.lossbreaker import LossBreakers
from polybot.ers.reconcile import ThreeWayReconciler, make_recon_provider
from polybot.ers.restart import RestartReconciler
from polybot.ers.safety import SafetyController
from polybot.ers.service import HermesPipeline, PaperSigner
from polybot.ers.startup_selftest import verify_or_refuse
from polybot.fusion.component_log import ComponentLog
from polybot.fusion.engine import FusionConfig
from polybot.harness.execution import (
    ShadowExecutionDispatcher,
    make_mark_for,
    make_shadow_execution_planner,
)
from polybot.harness.config import RampConfig
from polybot.harness.ledger import ShadowLedger
from polybot.harness.ramp_controller import RampController
from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerGate
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.feed import ResolutionFeed
from polybot.resolution.store import ResolutionStore
from polybot.storage.market_memory import ReadOnlyEventStore
from polybot.truthgate.gate import TruthGateConfig


EXPECTED_RISK_CAPS_HASH = (
    "9c5265736b4930c1d8270788e3543c1d9144454cf4e99407520da4862c7b03ab"
)


class CurrentMarketRegistry:
    """Read-through proxy so an immutable refresh generation becomes current atomically."""

    def __init__(self, provider):
        self._provider = provider

    def metadata_for(self, intent):
        return self._provider.require_fresh().metadata_for(intent)

    def resolution_subject_for(self, intent):
        return self._provider.require_fresh().resolution_subject_for(intent)


@dataclass
class ShadowComponents:
    event_reader: object
    intent_store: IntentStore
    forecast_ledger: ForecastLedger
    component_log: ComponentLog
    maker_ledger: MakerLedger
    shadow_ledger: ShadowLedger
    resolution_store: ResolutionStore
    pipeline: HermesPipeline
    calibration_gate: CalibrationGate
    maker_config: MakerConfig
    maker_gate: MakerGate
    ramp_config: RampConfig
    ramp_controller: RampController
    signer: PaperSigner
    controller: ERSController
    resolution_feed: ResolutionFeed
    resolution_dispatcher: ResolutionDispatcher
    execution_dispatcher: ShadowExecutionDispatcher
    maker_mark_for: object
    shadow_mark_for: object
    market_registry: CurrentMarketRegistry
    _closers: tuple = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self):
        if self._closed:
            return
        self._closed = True
        errors = []
        for close in reversed(self._closers):
            try:
                close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("shadow component shutdown failed", errors)


def build_shadow_components(config, *, ingestion, registry_provider,
                            resolution_providers, wall_clock,
                            health_clock_seconds, health_clock_ns):
    """Open real stores and bind every paper safety/settlement authority.

    There is deliberately no signer, wallet, key, or order-client injection surface.
    """
    caps = RiskCaps()
    verify_or_refuse(caps, expected_caps_hash=EXPECTED_RISK_CAPS_HASH)
    stamper = ingestion.stamper
    event_reader = ReadOnlyEventStore(config.ingestion.db_path)
    intent_store = IntentStore(config.intents_db_path, stamper)
    forecast_ledger = ForecastLedger(config.forecasts_db_path, stamper)
    component_log = ComponentLog(config.components_db_path, stamper=stamper)
    maker_ledger = MakerLedger(config.maker_db_path, stamper)
    shadow_ledger = ShadowLedger(config.shadow_db_path, stamper)
    # ResolutionFeed is the only off-loop store user. The root serializes its
    # worker with every event-loop dispatcher/state access and joins that worker
    # before closing this connection.
    resolution_store = ResolutionStore(
        config.resolution_db_path, stamper, check_same_thread=False
    )
    closers = (
        event_reader.close,
        intent_store.close,
        forecast_ledger.close,
        component_log.close,
        maker_ledger.close,
        shadow_ledger.close,
        resolution_store.close,
    )

    market_registry = CurrentMarketRegistry(registry_provider)
    calibration_gate = CalibrationGate(
        forecast_ledger, PriorEngine(), CalibrationConfig()
    )
    pipeline = HermesPipeline(
        calib_gate=calibration_gate,
        fusion_config=FusionConfig(
            w_news=0.20, w_base=0.30, w_micro=0.0,
            w_flow=0.0, clip_logodds=2.0,
        ),
        truth_gate_config=TruthGateConfig(
            freshness_window_ns=10**12,
            thin_book_depth_usd=Decimal("50"),
            thin_book_move=Decimal("0.02"),
        ),
        detectors=DetectorOrchestrator(DetectorConfig()),
        forecast_ledger=forecast_ledger,
        component_log=component_log,
        market_meta=market_registry,
        allowlist=DEFAULT_ALLOWLIST,
        event_store=event_reader,
        stamper=stamper,
    )

    safety = SafetyController(
        caps=caps, store=intent_store, clock=health_clock_seconds
    )
    safety.wire_flow_gate(make_flow_gate(
        intent_store, safety.active_caps, wall_clock=wall_clock
    ))
    reconciler = ThreeWayReconciler(caps=caps)
    recon_provider = make_recon_provider(
        intent_store, event_reader, reconciler,
        wallet=None, clock_ns=health_clock_ns,
    )
    anomaly = AnomalyMonitor(
        caps,
        clock=health_clock_seconds,
        ws_last_frame_at=ingestion.collector.last_frame_at,
        recon_provider=recon_provider,
    )
    lossbreakers = LossBreakers(
        store=intent_store,
        caps_provider=safety.active_caps,
        wall_clock=wall_clock,
    )
    restart = RestartReconciler(
        store=intent_store,
        event_store=event_reader,
        reconciler=reconciler,
        controller=safety,
        caps=caps,
        clock=health_clock_ns,
        wallet=None,
    )
    maker_config = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    maker_gate = MakerGate(maker_ledger, maker_config)
    ramp_config = RampConfig()
    ramp_controller = RampController(ramp_config=ramp_config, caps=caps)
    planner = make_shadow_execution_planner(
        book_for=ingestion.book_for,
        subject_for=market_registry.resolution_subject_for,
        maker_config=maker_config,
    )
    signer = PaperSigner()

    def gtd_for(decision, position, *, caps, standing_exit_total):
        # Paper-only passive backstop. The fixed 24-hour horizon is renewed per
        # accepted entry and carries no live venue/order authority.
        return derive_bracket(
            decision,
            position,
            caps=caps,
            expiry=int(wall_clock()) + 86_400,
            standing_exit_total=standing_exit_total,
        )

    controller = ERSController(
        store=intent_store,
        book_for=ingestion.book_for,
        caps=caps,
        signer=signer,
        controller=safety,
        breaker=DrawdownBreaker(caps, clock=health_clock_seconds),
        pipeline=pipeline,
        gtd_for=gtd_for,
        anomaly=anomaly,
        lossbreakers=lossbreakers,
        reconciler=restart,
        shadow_planner=planner,
        accept_wall_clock=wall_clock,
        clock=health_clock_seconds,
    )
    resolution_feed = ResolutionFeed(resolution_store, resolution_providers)
    resolution_dispatcher = ResolutionDispatcher(
        resolution_store, forecast_ledger, maker_ledger, shadow_ledger
    )
    execution_dispatcher = ShadowExecutionDispatcher(
        intent_store, maker_ledger, shadow_ledger
    )
    maker_mark_for = make_mark_for(maker_ledger, book_for=ingestion.book_for)
    shadow_mark_for = make_mark_for(shadow_ledger, book_for=ingestion.book_for)
    return ShadowComponents(
        event_reader=event_reader,
        intent_store=intent_store,
        forecast_ledger=forecast_ledger,
        component_log=component_log,
        maker_ledger=maker_ledger,
        shadow_ledger=shadow_ledger,
        resolution_store=resolution_store,
        pipeline=pipeline,
        calibration_gate=calibration_gate,
        maker_config=maker_config,
        maker_gate=maker_gate,
        ramp_config=ramp_config,
        ramp_controller=ramp_controller,
        signer=signer,
        controller=controller,
        resolution_feed=resolution_feed,
        resolution_dispatcher=resolution_dispatcher,
        execution_dispatcher=execution_dispatcher,
        maker_mark_for=maker_mark_for,
        shadow_mark_for=shadow_mark_for,
        market_registry=market_registry,
        _closers=closers,
    )
