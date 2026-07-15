import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.facade import ProposeOnlyFacade
from polybot.hermes.mcp_bridge import ProposalMcpServer
from polybot.hermes.read_views import BookReadView, LedgerReadView
from polybot.hermes.rpc import (
    ProposalRpcClient,
    ProposalRpcDispatcher,
    ProposalRpcServer,
    RpcRemoteError,
)
from polybot.harness.evidence import evaluate_category
from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.orderbook import LocalBook
from polybot.resolution.models import (
    PUSD_ADDRESS,
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
)
from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_build import build_shadow_components
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)
from polybot.storage.market_memory import EventStore


CONDITION = "0x" + "11" * 32
TOKENS = ("101", "202")
BLOCK_HASH = "0x" + "22" * 32


def _config(tmp_path):
    path = lambda name: str(tmp_path / name)
    return ShadowRuntimeConfig(
        ingestion=IngestionConfig(db_path=path("events.db")),
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
    from polybot.ers.market_meta import MarketRegistry
    return MarketRegistry.from_gamma_snapshots(
        [{
            "conditionId": CONDITION,
            "question": "Will the reviewed event happen?",
            "endDate": "2030-01-01T00:00:00Z",
            "clobTokenIds": json.dumps(TOKENS),
            "events": [{"id": "event-1"}],
        }],
        [{
            "id": "event-1",
            "tags": [{"id": "2"}],
            "markets": [{
                "conditionId": CONDITION,
                "clobTokenIds": json.dumps(TOKENS),
            }],
        }],
        clock=lambda: 1_750_000_000,
    )


def _book():
    book = LocalBook()
    book.apply_book({
        "bids": [{"price": "0.48", "size": "100000"}],
        "asks": [{"price": "0.52", "size": "100000"}],
    })
    return book


class _Provider:
    def __init__(self, provider_id):
        self.provider_id = provider_id
        self.terminal = False

    def chain_id(self):
        return 137

    def latest_block(self):
        return 20

    def block_hash(self, block_number):
        assert block_number == 15
        return BLOCK_HASH

    def observe(self, subject, block_number):
        assert subject == ResolutionSubject("event-1", CONDITION, TOKENS, "politics")
        assert block_number == 15
        if not self.terminal:
            return ProviderObservation(
                self.provider_id, 15, BLOCK_HASH, LifecyclePhase.UNRESOLVED,
                None, DisputeState.UNKNOWN, None, None, None, None, (),
            )
        return ProviderObservation(
            self.provider_id, 15, BLOCK_HASH, LifecyclePhase.FINALIZED,
            PayoutVector((1, 0), 1), DisputeState.CLEAR,
            PUSD_ADDRESS, TOKENS, "0x" + "33" * 20,
            "0x" + "44" * 32,
            ("14:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",),
        )

    def verify_terminal(self, _terminal):
        return None


def _open_components(config, registry, providers, book, stamper):
    health_ns = 100_000_000_000
    ingestion = SimpleNamespace(
        stamper=stamper,
        collector=SimpleNamespace(last_frame_at=lambda: health_ns),
        book_for=lambda token_id: book if token_id == "101" else None,
    )
    return build_shadow_components(
        config,
        ingestion=ingestion,
        registry_provider=SimpleNamespace(require_fresh=lambda: registry),
        resolution_providers=providers,
        wall_clock=lambda: 1_750_000_000.25,
        health_clock_seconds=lambda: 100.0,
        health_clock_ns=lambda: health_ns,
    )


def _warm_calibration(ledger):
    for index in range(150):
        won = index % 2 == 0
        ledger.record_forecast(
            f"seed-{index}", category="politics", condition_id=f"legacy-{index}",
            p=Decimal("0.9") if won else Decimal("0.1"),
            market_mid=Decimal("0.5"),
        )
        ledger.record_resolution(f"seed-{index}", "WON" if won else "LOST")


def test_whole_slice_survives_apply_before_ack_restart_and_terminal_fanout(tmp_path):
    config = _config(tmp_path)
    stamper = MonotonicStamper(clock=iter(range(1, 10000)).__next__)
    with EventStore(config.ingestion.db_path) as events:
        for source, event_id in (("fed-press", "citation-1"),
                                 ("sec-press", "citation-2")):
            events.append(make_envelope(
                stamper, source=source, source_tier="PRIMARY",
                event_id=event_id, content="reviewed evidence",
            ))
    registry = _registry()
    book = _book()
    providers = (_Provider("a"), _Provider("b"))
    subject = ResolutionSubject("event-1", CONDITION, TOKENS, "politics")

    first = _open_components(config, registry, providers, book, stamper)
    try:
        _warm_calibration(first.forecast_ledger)
        first.controller.boot()
        facade = ProposeOnlyFacade(
            first.intent_store,
            market_reader=lambda **_params: {
                "markets": [{"condition_id": CONDITION, "token_ids": list(TOKENS)}],
            },
            book_reader=BookReadView(
                lambda token_id: book if token_id in TOKENS else None,
                token_ids=TOKENS,
            ),
            ledger_reader=LedgerReadView(
                first.forecast_ledger, categories=("politics",),
            ),
            flags_reader=lambda: {
                "runtime_ready": True, "trading_permission": False,
            },
        )

        def proposal(intent_id):
            return {
                "intent_id": intent_id,
                "token_id": "101",
                "condition_id": CONDITION,
                "event_id": "event-1",
                "side": "BUY",
                "target_price": "0.49",
                "max_price": "0.60",
                "size_usd_suggestion": "12",
                "p": "0.90",
                "p_confidence": "0.75",
                "citations": ["citation-1", "citation-2"],
            }

        async def with_restarted_brain(action):
            socket_path = tmp_path / "whole-slice.sock"
            server = ProposalRpcServer(
                socket_path, ProposalRpcDispatcher(facade),
                runtime_ready=lambda: True,
            )
            server_task = asyncio.create_task(server.run())
            try:
                await asyncio.wait_for(server.started.wait(), timeout=1)
                bridge = ProposalMcpServer(ProposalRpcClient(socket_path))
                await action(bridge, socket_path)
            finally:
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
            assert not socket_path.exists()

        async def disconnected_and_stale_brain_phase(bridge, socket_path):
            book.mark_stale()
            with pytest.raises(RpcRemoteError, match="request_rejected"):
                await bridge.call_tool("get_book", {"token_id": "101"})
            assert first.intent_store.pending() == []
            book.apply_book({
                "bids": [{"price": "0.48", "size": "100000"}],
                "asks": [{"price": "0.52", "size": "100000"}],
            })

            _reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(b'{"version":1,"id":"disconnected"')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0)
            assert first.intent_store.pending() == []
            assert await bridge.call_tool(
                "propose_trade", proposal("intent-stale-validation"),
            ) is True

        asyncio.run(with_restarted_brain(disconnected_and_stale_brain_phase))
        book.mark_stale()
        first.controller.run_cycle(
            eligible_intent_ids=frozenset({"intent-stale-validation"})
        )
        assert first.intent_store.get("intent-stale-validation").status == "REJECTED"
        assert first.intent_store.pending_shadow_executions(10) == ()
        book.apply_book({
            "bids": [{"price": "0.48", "size": "100000"}],
            "asks": [{"price": "0.52", "size": "100000"}],
        })

        async def stale_execution_proposal(bridge, _socket_path):
            assert await bridge.call_tool(
                "propose_trade", proposal("intent-stale-execution"),
            ) is True

        asyncio.run(with_restarted_brain(stale_execution_proposal))
        real_planner = first.controller._shadow_planner

        def stale_second_fetch(intent, decision):
            book.mark_stale()
            return real_planner(intent, decision)

        first.controller._shadow_planner = stale_second_fetch
        first.controller.run_cycle(
            eligible_intent_ids=frozenset({"intent-stale-execution"})
        )
        assert first.intent_store.get("intent-stale-execution").status == "REJECTED"
        assert first.intent_store.pending_shadow_executions(10) == ()
        first.controller._shadow_planner = real_planner
        book.apply_book({
            "bids": [{"price": "0.48", "size": "100000"}],
            "asks": [{"price": "0.52", "size": "100000"}],
        })

        async def successful_proposal(bridge, _socket_path):
            assert (await bridge.call_tool("get_book", {"token_id": "101"}))[
                "midpoint"] == "0.50"
            assert (await bridge.call_tool("get_market", {"limit": 1}))[
                "markets"][0]["condition_id"] == CONDITION
            assert (await bridge.call_tool(
                "get_ledger", {"category": "politics", "limit": 1},
            ))["records"]
            assert (await bridge.call_tool("get_flags", {}))[
                "trading_permission"] is False
            with pytest.raises(ValueError, match="not approved"):
                await bridge.call_tool("record_decision", {})
            assert await bridge.call_tool(
                "propose_trade", proposal("intent-1"),
            ) is True

        asyncio.run(with_restarted_brain(successful_proposal))
        unresolved, = first.resolution_feed.poll((subject,))
        assert unresolved.disposition.value == "UNRESOLVED"

        first.controller.run_cycle(eligible_intent_ids=frozenset({"intent-1"}))

        assert first.intent_store.get("intent-1").status == "ACCEPTED"
        assert len(first.intent_store.fills_log()) == 1
        assert len(first.intent_store.flow_log()) == 1
        assert first.signer.gtd_exits == [{
            "token_id": "101",
            "exit_price": Decimal("0.1040"),
            "expiry": 1_750_086_400,
            "size": Decimal("12"),
        }]
        assert [row.role for row in first.intent_store.pending_shadow_executions(10)] == [
            "MAKER", "SHADOW",
        ]

        def crash(record, changed):
            assert (record.role, changed) == ("MAKER", True)
            raise RuntimeError("process died after Maker commit")

        first.execution_dispatcher._after_apply = crash
        with pytest.raises(RuntimeError, match="Maker commit"):
            first.execution_dispatcher.drain(2)
        assert len(first.maker_ledger.all()) == 1
        assert first.shadow_ledger.all() == []
    finally:
        first.close()

    restarted = _open_components(config, registry, providers, book, stamper)
    try:
        portfolio = restarted.controller.boot()
        assert [position.condition_id for position in portfolio.positions] == [CONDITION]
        replay = []
        restarted.execution_dispatcher._after_apply = (
            lambda record, changed: replay.append((record.role, changed))
        )
        assert restarted.execution_dispatcher.drain(2) == 2
        assert replay == [("MAKER", False), ("SHADOW", True)]

        for provider in providers:
            provider.terminal = True
        accepted, = restarted.resolution_feed.poll((subject,))
        assert accepted.disposition.value == "ACCEPTED"
        apply_terminal = restarted.maker_ledger.apply_terminal
        restarted.maker_ledger.apply_terminal = lambda _terminal: (
            _ for _ in ()
        ).throw(RuntimeError("Maker target unavailable"))
        with pytest.raises(RuntimeError, match="target unavailable"):
            restarted.resolution_dispatcher.drain(3)
        assert [row.role for row in restarted.resolution_store.pending_outbox(10)] == [
            "MAKER", "SHADOW",
        ]
        restarted.maker_ledger.apply_terminal = apply_terminal
        assert restarted.resolution_dispatcher.drain(3) == 2
        state = restarted.resolution_store.runtime_state()
        restarted.controller.apply_resolution_state(
            terminal_condition_ids=state.terminal_condition_ids,
            frozen_condition_ids=state.frozen_condition_ids,
        )

        maker, = restarted.maker_ledger.all()
        shadow, = restarted.shadow_ledger.all()
        forecast = restarted.forecast_ledger.get("intent-1")
        assert maker.terminal_id == shadow.terminal_id == forecast.terminal_id
        assert restarted.maker_mark_for("101") == Decimal("1")
        assert restarted.shadow_mark_for("101") == Decimal("1")
        assert restarted.controller.current_portfolio().positions == ()
        evidence = evaluate_category(
            "politics",
            shadow_ledger=restarted.shadow_ledger,
            forecast_ledger=restarted.forecast_ledger,
            calibration_gate=restarted.calibration_gate,
            maker_gate=restarted.maker_gate,
            ramp_config=restarted.ramp_config,
            maker_config=restarted.maker_config,
            family_size=1,
        )
        assert evidence.n_resolved == 1
        assert evidence.ready is False
    finally:
        restarted.close()
