"""Ordered POL-17 shadow cycle over injected, independently tested authorities."""

from __future__ import annotations

from dataclasses import dataclass

from polybot.resolution.feed import PollDisposition


@dataclass(frozen=True)
class ResolutionBatch:
    subjects: tuple
    intent_ids_by_condition: dict


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
