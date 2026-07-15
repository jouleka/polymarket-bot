"""Ordered POL-17 shadow cycle over injected, independently tested authorities."""

from __future__ import annotations

from dataclasses import dataclass

from polybot.ers.market_meta import MarketMetadataUnavailable
from polybot.resolution.feed import PollDisposition
from polybot.resolution.models import ResolutionSubject
from polybot.resolution.errors import SettlementConflict


@dataclass(frozen=True)
class ResolutionBatch:
    subjects: tuple
    intent_ids_by_condition: dict


def make_resolution_batch(forecast_ledger, intent_store, registry):
    """Union canonical unresolved forecasts and current proposals by condition."""
    by_condition = {}
    order = []
    intent_ids = {}

    def add(subject):
        previous = by_condition.get(subject.condition_id)
        if previous is not None and previous != subject:
            raise SettlementConflict("resolution subject identity contradicts fixed registry")
        if previous is None:
            by_condition[subject.condition_id] = subject
            order.append(subject.condition_id)

    for forecast in forecast_ledger.all():
        if forecast.resolution_status is not None:
            continue
        identity = (
            forecast.event_id,
            forecast.condition_id,
            forecast.category,
            forecast.sibling_token_ids,
        )
        if any(value is None for value in identity):
            continue
        add(ResolutionSubject(
            event_id=forecast.event_id,
            condition_id=forecast.condition_id,
            token_ids=forecast.sibling_token_ids,
            category=forecast.category,
        ))

    for intent in intent_store.pending():
        try:
            metadata = registry.resolution_subject_for(intent)
            subject = ResolutionSubject(
                event_id=metadata.event_id,
                condition_id=metadata.condition_id,
                token_ids=metadata.sibling_token_ids,
                category=metadata.category,
            )
        except MarketMetadataUnavailable:
            continue
        add(subject)
        intent_ids.setdefault(subject.condition_id, set()).add(intent.intent_id)

    return ResolutionBatch(
        subjects=tuple(by_condition[condition_id] for condition_id in order),
        intent_ids_by_condition={
            condition_id: frozenset(ids)
            for condition_id, ids in intent_ids.items()
        },
    )


class ShadowCycleCoordinator:
    def __init__(self, *, heartbeat, registry_provider, subjects_for,
                 resolution_feed, resolution_dispatcher, controller,
                 execution_dispatcher, evidence_update, status_update,
                 run_blocking, clock, registry_refresh_seconds,
                 resolution_poll_seconds, outbox_batch_limit):
        self._heartbeat = heartbeat
        self._registry_provider = registry_provider
        self._subjects_for = subjects_for
        self._resolution_feed = resolution_feed
        self._resolution_dispatcher = resolution_dispatcher
        self._controller = controller
        self._execution_dispatcher = execution_dispatcher
        self._evidence_update = evidence_update
        self._status_update = status_update
        self._run_blocking = run_blocking
        self._clock = clock
        self._registry_refresh_seconds = registry_refresh_seconds
        self._resolution_poll_seconds = resolution_poll_seconds
        self._outbox_batch_limit = outbox_batch_limit
        self._last_registry_refresh = None
        self._last_resolution_poll = None

    async def run_cycle(self):
        self._heartbeat()
        now = self._clock()

        if self._due(self._last_registry_refresh, self._registry_refresh_seconds, now):
            await self._run_blocking(self._registry_provider.refresh)
            self._last_registry_refresh = now
        registry = self._registry_provider.require_fresh()

        eligible = frozenset()
        terminal = ()
        frozen = ()
        if self._due(self._last_resolution_poll, self._resolution_poll_seconds, now):
            batch = self._subjects_for(registry)
            results = await self._run_blocking(
                self._resolution_feed.poll, batch.subjects
            )
            eligible_ids = set()
            terminal_ids = []
            frozen_ids = []
            for result in results:
                if result.disposition is PollDisposition.UNRESOLVED:
                    eligible_ids.update(
                        batch.intent_ids_by_condition.get(result.condition_id, ())
                    )
                elif result.disposition is PollDisposition.UNKNOWN:
                    frozen_ids.append(result.condition_id)
                elif result.disposition in (
                        PollDisposition.ACCEPTED,
                        PollDisposition.ALREADY_TERMINAL):
                    terminal_ids.append(result.condition_id)
            eligible = frozenset(eligible_ids)
            terminal = tuple(terminal_ids)
            frozen = tuple(frozen_ids)
            self._last_resolution_poll = now

        self._resolution_dispatcher.drain(self._outbox_batch_limit)
        self._controller.apply_resolution_state(
            terminal_condition_ids=terminal,
            frozen_condition_ids=frozen,
        )
        self._controller.run_cycle(eligible_intent_ids=eligible)
        self._execution_dispatcher.drain(self._outbox_batch_limit)
        self._evidence_update()
        self._status_update()

    @staticmethod
    def _due(last, interval, now):
        return last is None or now - last >= interval
