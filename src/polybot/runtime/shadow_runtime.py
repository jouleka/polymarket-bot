"""Supervised lifecycle for the single-process POL-17 paper runtime."""

from __future__ import annotations

import asyncio


class _StopRequested(Exception):
    """Internal clean-stop sentinel for TaskGroup cancellation."""


def _supervised(name, service):
    async def run():
        await service()
        raise RuntimeError(
            f"shadow runtime service {name!r} returned unexpectedly — HALT"
        )
    return run


class ShadowRuntime:
    def __init__(self, *, services, writer, lock, recover_resolution,
                 drain_resolution, drain_execution, collector,
                 apply_initial_resolution_state, controller, readiness,
                 run_cycle, cycle_interval_seconds, readiness_timeout_seconds,
                 closers=()):
        self._services = tuple(services)
        self._writer = writer
        self._lock = lock
        self._recover_resolution = recover_resolution
        self._drain_resolution = drain_resolution
        self._drain_execution = drain_execution
        self._collector = collector
        self._apply_initial_resolution_state = apply_initial_resolution_state
        self._controller = controller
        self._readiness = readiness
        self._run_cycle = run_cycle
        self._cycle_interval_seconds = cycle_interval_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._closers = tuple(closers)
        self._stop = None
        self._ready = False
        self._closed = False

    def request_stop(self):
        if self._stop is not None:
            self._stop.set()

    async def run(self):
        acquired = False
        self._lock.acquire()
        acquired = True
        self._stop = asyncio.Event()
        try:
            try:
                async with asyncio.TaskGroup() as group:
                    for index, service in enumerate(self._services):
                        group.create_task(_supervised(f"service-{index}", service)())
                    group.create_task(self._main_loop())
                    group.create_task(self._stopper())
            except* _StopRequested:
                pass
        finally:
            self._close_resources()
            if acquired:
                self._lock.release()

    def close_unstarted(self):
        """Release a constructed-but-never-run assembly (tests/failed activation)."""
        self._close_resources()

    def _close_resources(self):
        if self._closed:
            return
        self._closed = True
        if self._ready:
            self._readiness.stopping()
        self._writer.close()
        for close in reversed(self._closers):
            close()

    async def _main_loop(self):
        await self._recover_resolution()
        self._drain_resolution()
        self._drain_execution()
        await self._wait_for_live_frame()
        self._apply_initial_resolution_state()
        self._controller.boot()
        self._readiness.ready()
        self._ready = True
        while True:
            await self._run_cycle()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._cycle_interval_seconds
                )
            except TimeoutError:
                continue
            return

    async def _wait_for_live_frame(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._readiness_timeout_seconds
        while self._collector.last_frame_at() is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("live-book readiness timed out")
            await asyncio.sleep(min(0.05, remaining))

    async def _stopper(self):
        await self._stop.wait()
        raise _StopRequested()
