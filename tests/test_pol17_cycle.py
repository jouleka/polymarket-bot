"""POL-17 exact cycle ordering and resolution eligibility."""

import asyncio
from types import SimpleNamespace

from polybot.resolution.feed import PollDisposition
from polybot.runtime.shadow_cycle import ResolutionBatch, ShadowCycleCoordinator


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
