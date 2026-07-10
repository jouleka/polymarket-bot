import asyncio
import pytest
from polybot.runtime.ingestion import IngestionRuntime


class FakeWriter:
    def __init__(self): self.close_calls = 0
    def close(self): self.close_calls += 1


def test_runs_all_services_then_stops_cleanly():
    started = []
    async def svc_a():
        started.append("a")
        await asyncio.sleep(3600)   # runs "forever" until cancelled
    async def svc_b():
        started.append("b")
        await asyncio.sleep(3600)
    w = FakeWriter()

    async def scenario():
        rt = IngestionRuntime(services=[svc_a, svc_b], writer=w)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)                # let both services start
        assert set(started) == {"a", "b"}
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)  # clean return, no exception

    asyncio.run(scenario())
    assert w.close_calls == 1                    # durability: closed exactly once


def test_production_shaped_three_services_cancel_and_close_writer_once():
    started = set()
    cancelled = set()
    all_started = asyncio.Event()
    never = asyncio.Event()

    def service(name):
        async def run():
            started.add(name)
            if len(started) == 3:
                all_started.set()
            try:
                await never.wait()
            finally:
                cancelled.add(name)
        return run

    writer = FakeWriter()

    async def scenario():
        runtime = IngestionRuntime(
            services=[service("clob-ws"), service("clob-midpoint"), service("data-api")],
            writer=writer,
        )
        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(all_started.wait(), timeout=1)
        runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())
    assert started == {"clob-ws", "clob-midpoint", "data-api"}
    assert cancelled == started
    assert writer.close_calls == 1


def test_service_halt_propagates_and_still_closes_writer():
    async def good():
        await asyncio.sleep(3600)
    async def halting():
        raise RuntimeError("unknown event_type: format change")   # a venue HALT
    w = FakeWriter()

    async def scenario():
        rt = IngestionRuntime(services=[good, halting], writer=w)
        await asyncio.wait_for(rt.run(), timeout=1)

    with pytest.raises(BaseExceptionGroup) as ei:
        asyncio.run(scenario())
    # the RuntimeError HALT is inside the group (not swallowed as a clean stop)
    assert any(isinstance(e, RuntimeError) for e in ei.value.exceptions)
    assert w.close_calls == 1        # durability on crash


class FakeHeartbeat:
    def __init__(self, *, fail=False): self.beats = 0; self._fail = fail
    def beat(self):
        self.beats += 1
        if self._fail:
            raise OSError("disk full")


def test_heartbeat_beats_and_survives_beat_errors():
    async def svc():
        await asyncio.sleep(3600)
    w = FakeWriter()
    hb = FakeHeartbeat(fail=True)     # every beat raises
    async def fast_sleep(_):          # instant sleep so the heartbeat loop spins fast
        await asyncio.sleep(0)

    async def scenario():
        rt = IngestionRuntime(services=[svc], writer=w, heartbeat=hb,
                              heartbeat_interval_seconds=0.001, sleep=fast_sleep)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)   # survived the beat errors -> clean stop

    asyncio.run(scenario())
    assert hb.beats > 0
    assert w.close_calls == 1
