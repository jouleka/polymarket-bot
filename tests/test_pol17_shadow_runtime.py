"""POL-17 supervised startup, readiness, and shutdown."""

import asyncio

from polybot.runtime.shadow_runtime import ShadowRuntime


def test_shadow_runtime_enforces_recovery_readiness_and_reverse_shutdown_order():
    trace = []
    service_started = asyncio.Event()
    service_release = asyncio.Event()

    async def service():
        trace.append("service_start")
        service_started.set()
        await service_release.wait()

    class Lock:
        def acquire(self):
            trace.append("lock")

        def release(self):
            trace.append("unlock")

    class Collector:
        def last_frame_at(self):
            trace.append("book_ready")
            return 123

    class Controller:
        def boot(self):
            trace.append("controller_boot")

    class Readiness:
        def ready(self):
            trace.append("ready")

        def stopping(self):
            trace.append("stopping")

    class Writer:
        def close(self):
            trace.append("writer_close")

    async def recover():
        await service_started.wait()
        trace.append("resolution_recover")

    runtime = None

    async def cycle():
        trace.append("cycle")
        runtime.request_stop()

    runtime = ShadowRuntime(
        services=(service,),
        writer=Writer(),
        lock=Lock(),
        recover_resolution=recover,
        drain_resolution=lambda: trace.append("resolution_drain"),
        drain_execution=lambda: trace.append("execution_drain"),
        collector=Collector(),
        apply_initial_resolution_state=lambda: trace.append("resolution_state"),
        controller=Controller(),
        readiness=Readiness(),
        run_cycle=cycle,
        cycle_interval_seconds=1.0,
        readiness_timeout_seconds=1.0,
        closers=(
            lambda: trace.append("close_first"),
            lambda: trace.append("close_second"),
        ),
    )

    asyncio.run(runtime.run())

    assert trace == [
        "lock",
        "service_start",
        "resolution_recover",
        "resolution_drain",
        "execution_drain",
        "book_ready",
        "resolution_state",
        "controller_boot",
        "ready",
        "cycle",
        "stopping",
        "writer_close",
        "close_second",
        "close_first",
        "unlock",
    ]
