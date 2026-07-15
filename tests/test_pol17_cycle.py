"""POL-17 exact cycle ordering and resolution eligibility."""

import asyncio
from types import SimpleNamespace

from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.resolution.feed import PollDisposition
from polybot.runtime.shadow_cycle import (
    ResolutionBatch,
    ShadowCycleCoordinator,
    make_resolution_batch,
)


def test_cycle_orders_terminal_authority_before_ers_and_execution_projection():
    trace = []

    class Registry:
        def refresh(self):
            trace.append("registry_refresh")

        def require_fresh(self):
            trace.append("registry_require")
            return object()

    class Feed:
        def poll(self, subjects):
            trace.append(("resolution_poll", subjects))
            return (
                SimpleNamespace(
                    condition_id="condition-1",
                    disposition=PollDisposition.UNRESOLVED,
                ),
            )

    class ResolutionDispatcher:
        def drain(self, limit):
            trace.append(("resolution_dispatch", limit))

    class Controller:
        def apply_resolution_state(self, **state):
            trace.append(("resolution_state", state))

        def run_cycle(self, *, eligible_intent_ids):
            trace.append(("ers", eligible_intent_ids))

    class ExecutionDispatcher:
        def drain(self, limit):
            trace.append(("execution_dispatch", limit))

    async def run_blocking(call, *args):
        return call(*args)

    def subjects_for(registry):
        trace.append("subjects")
        return ResolutionBatch(
            subjects=("subject-1",),
            intent_ids_by_condition={"condition-1": frozenset({"intent-1"})},
        )

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: trace.append("heartbeat"),
        registry_provider=Registry(),
        subjects_for=subjects_for,
        resolution_feed=Feed(),
        resolution_dispatcher=ResolutionDispatcher(),
        controller=Controller(),
        execution_dispatcher=ExecutionDispatcher(),
        evidence_update=lambda: trace.append("evidence"),
        status_update=lambda: trace.append("status"),
        run_blocking=run_blocking,
        clock=lambda: 100.0,
        registry_refresh_seconds=300.0,
        resolution_poll_seconds=60.0,
        outbox_batch_limit=25,
    )

    asyncio.run(coordinator.run_cycle())

    assert trace == [
        "heartbeat",
        "registry_refresh",
        "registry_require",
        "subjects",
        ("resolution_poll", ("subject-1",)),
        ("resolution_dispatch", 25),
        ("resolution_state", {
            "terminal_condition_ids": (),
            "frozen_condition_ids": (),
        }),
        ("ers", frozenset({"intent-1"})),
        ("execution_dispatch", 25),
        "evidence",
        "status",
    ]


def test_resolution_batch_unions_unresolved_forecasts_and_pending_intents_by_condition():
    condition_id = "0x" + "11" * 32
    subject = ResolutionSubjectMetadata(
        event_id="event-1",
        condition_id=condition_id,
        category="politics",
        token_id="101",
        outcome_slot=0,
        sibling_token_ids=("101", "202"),
    )
    unresolved = SimpleNamespace(
        resolution_status=None,
        event_id="event-1",
        condition_id=condition_id,
        category="politics",
        sibling_token_ids=("101", "202"),
    )
    resolved = SimpleNamespace(
        resolution_status="WON",
        event_id="event-2",
        condition_id="0x" + "22" * 32,
        category="crypto",
        sibling_token_ids=("303", "404"),
    )
    intent = SimpleNamespace(intent_id="intent-1")
    forecast_ledger = SimpleNamespace(all=lambda: [unresolved, resolved])
    intent_store = SimpleNamespace(pending=lambda: [intent])
    registry = SimpleNamespace(resolution_subject_for=lambda candidate: subject)

    batch = make_resolution_batch(forecast_ledger, intent_store, registry)

    assert len(batch.subjects) == 1
    assert batch.subjects[0].condition_id == condition_id
    assert batch.subjects[0].token_ids == ("101", "202")
    assert batch.intent_ids_by_condition == {
        condition_id: frozenset({"intent-1"}),
    }


def test_cycle_drains_every_older_terminal_batch_before_ers():
    trace = []

    class Registry:
        def refresh(self):
            pass

        def require_fresh(self):
            return object()

    class Dispatcher:
        def __init__(self):
            self.counts = iter((2, 1))

        def drain(self, limit):
            trace.append(("resolution_dispatch", limit))
            return next(self.counts)

    class Controller:
        def apply_resolution_state(self, **_state):
            trace.append("resolution_state")

        def run_cycle(self, *, eligible_intent_ids):
            trace.append(("ers", eligible_intent_ids))

    async def run_blocking(call, *args):
        return call(*args)

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: None,
        registry_provider=Registry(),
        subjects_for=lambda _registry: ResolutionBatch((), {}),
        resolution_feed=SimpleNamespace(poll=lambda _subjects: ()),
        resolution_dispatcher=Dispatcher(),
        controller=Controller(),
        execution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
        evidence_update=lambda: None,
        status_update=lambda: None,
        run_blocking=run_blocking,
        clock=lambda: 100.0,
        registry_refresh_seconds=300.0,
        resolution_poll_seconds=60.0,
        outbox_batch_limit=2,
    )

    asyncio.run(coordinator.run_cycle())

    assert trace == [
        ("resolution_dispatch", 2),
        ("resolution_dispatch", 2),
        "resolution_state",
        ("ers", frozenset()),
    ]
