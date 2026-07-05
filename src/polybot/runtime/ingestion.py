"""Continuous ingestion runtime: supervise the S1 collectors in one event loop with durable shutdown.
IngestionRuntime is the PURE supervision core (fake services -> hermetic tests); build_ingestion_runtime + main
(Task 4) do the live wiring + entry point. Additive; imports only ingestion/storage/core/ers.heartbeat."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("polybot.ingestion")


class _StopRequested(Exception):
    """Internal sentinel raised by the stopper task to unwind the TaskGroup on an operator-requested stop
    (distinct from a collector HALT, which must propagate loudly)."""


class IngestionRuntime:
    def __init__(self, *, services, writer, heartbeat=None,
                 heartbeat_interval_seconds: float = 5.0, sleep=asyncio.sleep):
        self._services = list(services)        # zero-arg callables -> awaitable
        self._writer = writer
        self._heartbeat = heartbeat
        self._heartbeat_interval = heartbeat_interval_seconds
        self._sleep = sleep
        self._stop: asyncio.Event | None = None

    def request_stop(self) -> None:
        # Loop-safe (Event.set) so a signal handler can call it. Idempotent; no-op before run().
        if self._stop is not None:
            self._stop.set()

    async def run(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with asyncio.TaskGroup() as tg:
                for factory in self._services:
                    tg.create_task(factory())
                if self._heartbeat is not None:
                    tg.create_task(self._heartbeat_loop())
                tg.create_task(self._stopper())
        except* _StopRequested:
            pass  # clean, operator-requested stop; a real service error is NOT _StopRequested -> propagates
        finally:
            self._writer.close()  # idempotent drain+join; durability invariant (runs on EVERY path)

    async def _stopper(self) -> None:
        assert self._stop is not None
        await self._stop.wait()
        raise _StopRequested()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._heartbeat.beat()
            except Exception:  # best-effort liveness: a beat hiccup must never kill ingestion
                log.exception("heartbeat beat failed")
            await self._sleep(self._heartbeat_interval)
