"""POL-17 supervised startup, readiness, and shutdown."""

import asyncio
from types import SimpleNamespace

import pytest

from polybot.runtime.shadow_runtime import ShadowRuntime


def _leaf_errors(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in _leaf_errors(child)]
    return [error]


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
        "controller_boot",
        "resolution_state",
        "ready",
        "cycle",
        "stopping",
        "writer_close",
        "close_second",
        "close_first",
        "unlock",
    ]


def test_shutdown_attempts_every_close_and_releases_lock_after_close_failures():
    trace = []
    runtime = None

    class Writer:
        def close(self):
            trace.append("writer")
            raise RuntimeError("writer close failed")

    async def service():
        await asyncio.Event().wait()

    async def cycle():
        runtime.request_stop()

    runtime = ShadowRuntime(
        services=(service,),
        writer=Writer(),
        lock=SimpleNamespace(
            acquire=lambda: trace.append("lock"),
            release=lambda: trace.append("unlock"),
        ),
        recover_resolution=lambda: asyncio.sleep(0),
        drain_resolution=lambda: None,
        drain_execution=lambda: None,
        collector=SimpleNamespace(last_frame_at=lambda: 1),
        apply_initial_resolution_state=lambda: None,
        controller=SimpleNamespace(boot=lambda: None),
        readiness=SimpleNamespace(
            ready=lambda: None, stopping=lambda: trace.append("stopping")
        ),
        run_cycle=cycle,
        cycle_interval_seconds=1,
        readiness_timeout_seconds=1,
        closers=(
            lambda: trace.append("close_first"),
            lambda: (_ for _ in ()).throw(RuntimeError("close second failed")),
        ),
    )

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(runtime.run())

    assert {str(error) for error in caught.value.exceptions} == {
        "writer close failed", "close second failed",
    }
    assert trace == [
        "lock", "stopping", "writer", "close_first", "unlock",
    ]


def test_supervisor_treats_a_normally_returning_service_as_fatal():
    trace = []

    async def returned_service():
        trace.append("returned")

    runtime = ShadowRuntime(
        services=(returned_service,),
        writer=SimpleNamespace(close=lambda: trace.append("writer")),
        lock=SimpleNamespace(
            acquire=lambda: trace.append("lock"),
            release=lambda: trace.append("unlock"),
        ),
        recover_resolution=lambda: asyncio.sleep(0),
        drain_resolution=lambda: None,
        drain_execution=lambda: None,
        collector=SimpleNamespace(last_frame_at=lambda: 1),
        apply_initial_resolution_state=lambda: None,
        controller=SimpleNamespace(boot=lambda: None),
        readiness=SimpleNamespace(ready=lambda: None, stopping=lambda: None),
        run_cycle=lambda: asyncio.Event().wait(),
        cycle_interval_seconds=1,
        readiness_timeout_seconds=1,
    )

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(runtime.run())

    leaves = _leaf_errors(caught.value)
    assert len(leaves) == 1
    assert isinstance(leaves[0], RuntimeError)
    assert "returned unexpectedly" in str(leaves[0])
    assert trace == ["lock", "returned", "writer", "unlock"]


def test_live_book_readiness_timeout_is_fatal_and_never_announces_ready():
    trace = []

    async def service():
        await asyncio.Event().wait()

    runtime = ShadowRuntime(
        services=(service,),
        writer=SimpleNamespace(close=lambda: trace.append("writer")),
        lock=SimpleNamespace(
            acquire=lambda: trace.append("lock"),
            release=lambda: trace.append("unlock"),
        ),
        recover_resolution=lambda: asyncio.sleep(0),
        drain_resolution=lambda: None,
        drain_execution=lambda: None,
        collector=SimpleNamespace(last_frame_at=lambda: None),
        apply_initial_resolution_state=lambda: None,
        controller=SimpleNamespace(boot=lambda: None),
        readiness=SimpleNamespace(
            ready=lambda: trace.append("ready"),
            stopping=lambda: trace.append("stopping"),
        ),
        run_cycle=lambda: asyncio.sleep(0),
        cycle_interval_seconds=1,
        readiness_timeout_seconds=0,
    )

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(runtime.run())

    leaves = _leaf_errors(caught.value)
    assert len(leaves) == 1
    assert isinstance(leaves[0], TimeoutError)
    assert str(leaves[0]) == "live-book readiness timed out"
    assert trace == ["lock", "writer", "unlock"]


def test_fatal_shutdown_fences_and_joins_worker_before_draining_writer():
    trace = []

    async def service():
        raise RuntimeError("fatal collector")

    runtime = ShadowRuntime(
        services=(service,),
        writer=SimpleNamespace(close=lambda: trace.append("writer")),
        lock=SimpleNamespace(
            acquire=lambda: trace.append("lock"),
            release=lambda: trace.append("unlock"),
        ),
        recover_resolution=lambda: asyncio.Event().wait(),
        drain_resolution=lambda: None,
        drain_execution=lambda: None,
        collector=SimpleNamespace(last_frame_at=lambda: None),
        apply_initial_resolution_state=lambda: None,
        controller=SimpleNamespace(boot=lambda: None),
        readiness=SimpleNamespace(ready=lambda: None, stopping=lambda: None),
        run_cycle=lambda: asyncio.sleep(0),
        cycle_interval_seconds=1,
        readiness_timeout_seconds=1,
        before_writer_closers=(
            lambda: trace.append("stop_rpc_admission"),
            lambda: trace.append("join_worker"),
        ),
        closers=(lambda: trace.append("components"),),
    )

    with pytest.raises(ExceptionGroup) as caught:
        asyncio.run(runtime.run())

    assert [str(error) for error in _leaf_errors(caught.value)] == [
        "fatal collector",
    ]
    assert trace == [
        "lock", "stop_rpc_admission", "join_worker", "writer", "components",
        "unlock",
    ]
