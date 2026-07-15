"""POL-17 exact cycle ordering and resolution eligibility."""

import asyncio
from types import SimpleNamespace

import pytest

from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.ers.market_meta import MarketSnapshotError
from polybot.resolution.feed import PollDisposition
from polybot.runtime.shadow_cycle import (
    ResolutionBatch,
    ShadowCycleCoordinator,
    make_resolution_batch,
)
from polybot.runtime.registry_provider import RegistryRefreshUnavailable


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


def test_transient_registry_refresh_uses_only_a_still_fresh_generation():
    trace = []

    class Registry:
        def refresh(self):
            raise RegistryRefreshUnavailable("Gamma transport unavailable")

        def require_fresh(self):
            trace.append("last_good")
            return object()

    class Controller:
        def apply_resolution_state(self, **_state):
            pass

        def run_cycle(self, *, eligible_intent_ids):
            trace.append(("ers", eligible_intent_ids))

    async def run_blocking(call, *args):
        return call(*args)

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: None,
        registry_provider=Registry(),
        subjects_for=lambda _registry: ResolutionBatch((), {}),
        resolution_feed=SimpleNamespace(poll=lambda _subjects: ()),
        resolution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
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

    assert trace == ["last_good", ("ers", frozenset())]


@pytest.mark.parametrize(
    ("disposition", "eligible", "terminal", "frozen"),
    [
        (PollDisposition.UNRESOLVED, frozenset({"intent-1"}), (), ()),
        (PollDisposition.UNKNOWN, frozenset(), (), ("condition-1",)),
        (PollDisposition.UNAVAILABLE, frozenset(), (), ()),
        (PollDisposition.ACCEPTED, frozenset(), ("condition-1",), ()),
        (PollDisposition.ALREADY_TERMINAL, frozenset(), ("condition-1",), ()),
    ],
)
def test_every_resolution_disposition_maps_to_exact_ers_authority(
        disposition, eligible, terminal, frozen):
    observed = {}

    class Controller:
        def apply_resolution_state(self, **state):
            observed["state"] = state

        def run_cycle(self, *, eligible_intent_ids):
            observed["eligible"] = eligible_intent_ids

    async def run_blocking(call, *args):
        return call(*args)

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: None,
        registry_provider=SimpleNamespace(
            refresh=lambda: None, require_fresh=lambda: object()
        ),
        subjects_for=lambda _registry: ResolutionBatch(
            ("subject",), {"condition-1": frozenset({"intent-1"})}
        ),
        resolution_feed=SimpleNamespace(poll=lambda _subjects: (
            SimpleNamespace(condition_id="condition-1", disposition=disposition),
        )),
        resolution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
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

    assert observed == {
        "state": {
            "terminal_condition_ids": terminal,
            "frozen_condition_ids": frozen,
        },
        "eligible": eligible,
    }


def test_transient_refresh_cannot_rescue_an_expired_registry_generation():
    class Registry:
        def refresh(self):
            raise RegistryRefreshUnavailable("Gamma unavailable")

        def require_fresh(self):
            raise MarketSnapshotError("Gamma registry is stale")

    async def run_blocking(call, *args):
        return call(*args)

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: None,
        registry_provider=Registry(),
        subjects_for=lambda _registry: ResolutionBatch((), {}),
        resolution_feed=SimpleNamespace(poll=lambda _subjects: ()),
        resolution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
        controller=SimpleNamespace(
            apply_resolution_state=lambda **_state: None,
            run_cycle=lambda **_kwargs: None,
        ),
        execution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
        evidence_update=lambda: None, status_update=lambda: None,
        run_blocking=run_blocking, clock=lambda: 100.0,
        registry_refresh_seconds=300.0, resolution_poll_seconds=60.0,
        outbox_batch_limit=2,
    )

    with pytest.raises(MarketSnapshotError, match="stale"):
        asyncio.run(coordinator.run_cycle())

    assert coordinator.last_registry_error == "Gamma unavailable"


def test_cycle_drains_every_execution_outbox_batch_before_evidence():
    trace = []

    class ExecutionDispatcher:
        def __init__(self):
            self._counts = iter((2, 2, 1))

        def drain(self, limit):
            trace.append(("execution", limit))
            return next(self._counts)

    async def run_blocking(call, *args):
        return call(*args)

    coordinator = ShadowCycleCoordinator(
        heartbeat=lambda: None,
        registry_provider=SimpleNamespace(
            refresh=lambda: None, require_fresh=lambda: object(),
        ),
        subjects_for=lambda _registry: ResolutionBatch((), {}),
        resolution_feed=SimpleNamespace(poll=lambda _subjects: ()),
        resolution_dispatcher=SimpleNamespace(drain=lambda _limit: 0),
        controller=SimpleNamespace(
            apply_resolution_state=lambda **_state: None,
            run_cycle=lambda **_kwargs: trace.append("ers"),
        ),
        execution_dispatcher=ExecutionDispatcher(),
        evidence_update=lambda: trace.append("evidence"),
        status_update=lambda: trace.append("status"),
        run_blocking=run_blocking, clock=lambda: 100.0,
        registry_refresh_seconds=300.0, resolution_poll_seconds=60.0,
        outbox_batch_limit=2,
    )

    asyncio.run(coordinator.run_cycle())

    assert trace == [
        "ers", ("execution", 2), ("execution", 2), ("execution", 2),
        "evidence", "status",
    ]
