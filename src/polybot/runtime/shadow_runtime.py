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
                 closers=(), lock_acquired=False, stop_requested=None):
        self._services = tuple(services)
        self._writer = writer
        self._lock = lock
        self._lock_acquired = lock_acquired
        self._stop_requested = stop_requested
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
        try:
            if self._stop_requested is not None:
                self._stop_requested()
        finally:
            if self._stop is not None:
                self._stop.set()

    async def run(self):
        if not self._lock_acquired:
            self._lock.acquire()
            self._lock_acquired = True
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
            try:
                self._close_resources()
            finally:
                self._release_lock()

    def close_unstarted(self):
        """Release a constructed-but-never-run assembly (tests/failed activation)."""
        try:
            self._close_resources()
        finally:
            self._release_lock()

    def _release_lock(self):
        if self._lock_acquired:
            self._lock_acquired = False
            self._lock.release()

    def _close_resources(self):
        if self._closed:
            return
        self._closed = True
        errors = []
        if self._ready:
            try:
                self._readiness.stopping()
            except Exception as exc:
                errors.append(exc)
        actions = (self._writer.close,) + tuple(reversed(self._closers))
        for close in actions:
            try:
                close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("shadow runtime resource shutdown failed", errors)

    async def _main_loop(self):
        await self._recover_resolution()
        self._drain_resolution()
        self._drain_execution()
        await self._wait_for_live_frame()
        self._controller.boot()
        self._apply_initial_resolution_state()
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
