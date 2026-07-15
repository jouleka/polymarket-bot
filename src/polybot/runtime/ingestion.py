"""Continuous ingestion runtime: supervise the S1 collectors in one event loop with durable shutdown.
IngestionRuntime is the PURE supervision core (fake services -> hermetic tests); build_ingestion_runtime + main
(Task 4) do the live wiring + entry point. Additive; imports only ingestion/storage/core/ers.heartbeat."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass

from polybot.core.clock import MonotonicStamper
from polybot.ers.heartbeat import Heartbeat
from polybot.ingestion.data_api import DataApiPoller
from polybot.ingestion.midpoint import MidpointSnapshotter
from polybot.ingestion.sharding import ShardedMarketCollector
from polybot.ingestion.transport import DATA_API_URL, WS_RECONNECT_ON, make_httpx_fetch, open_market_ws
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore
from polybot.runtime.config import IngestionConfig, load_config
from polybot.runtime.discovery import discover_universe, make_gamma_fetch

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


def _supervised(name, factory):
    """Wrap a service factory so a NORMAL return (a collector that stops looping) becomes a LOUD HALT rather than a
    silent dead stream under the still-running stopper. Collectors are contractually infinite; a normal completion is
    a bug on an un-backfillable store -> fail loud -> systemd restart. Cancellation on stop propagates normally
    (the raise is after the await, unreachable when the awaited coro is cancelled)."""
    async def run():
        await factory()
        raise RuntimeError(f"ingestion service {name!r} returned unexpectedly (must run forever) — HALT")
    return run


@dataclass(frozen=True)
class IngestionAssembly:
    services: tuple
    writer: object
    collector: object
    token_ids: tuple[str, ...]
    stamper: object
    health_stamper: object
    heartbeat: object | None

    def book_for(self, token_id):
        return self.collector.book_for(token_id)


def build_ingestion_assembly(config: IngestionConfig, *, gamma_fetch=None, ws_connect=None,
                             data_fetch=None, stamper=None,
                             health_stamper=None) -> IngestionAssembly:
    """Construct D4a once while exposing its live collector to the POL-17 root."""
    stamper = stamper or MonotonicStamper()
    if health_stamper is None:
        health_stamper = stamper
    gamma_fetch = gamma_fetch or make_gamma_fetch(config.gamma_url)
    ws_connect = ws_connect or open_market_ws
    data_fetch = data_fetch or make_httpx_fetch(DATA_API_URL)

    token_ids = discover_universe(gamma_fetch, config)
    writer = QueuedEventWriter(EventStore(config.db_path, check_same_thread=False))

    ws = ShardedMarketCollector(ws_connect, health_stamper, token_ids, sink=None,
                                max_assets_per_shard=config.max_assets_per_shard,
                                reconnect_on=WS_RECONNECT_ON)
    snapshotter = MidpointSnapshotter(
        token_ids=token_ids,
        book_for=ws.book_for,
        stamper=stamper,
        writer=writer,
        interval_seconds=config.snapshot_interval_seconds,
    )
    services = [
        _supervised("clob-ws", lambda: ws.run(max_connections=None)),
        _supervised("clob-midpoint", snapshotter.run),
    ]

    if config.data_api_enabled:
        poller = DataApiPoller(data_fetch, stamper, writer)
        services.append(_supervised("data-api", lambda: poller.run(
            "/trades", params={"limit": config.data_api_limit}, interval=config.data_api_interval_seconds)))

    heartbeat = Heartbeat(config.heartbeat_path) if config.heartbeat_path else None
    return IngestionAssembly(
        services=tuple(services), writer=writer, collector=ws,
        token_ids=tuple(token_ids), stamper=stamper,
        health_stamper=health_stamper, heartbeat=heartbeat,
    )


def build_ingestion_runtime(config: IngestionConfig, *, gamma_fetch=None, ws_connect=None,
                            data_fetch=None, stamper=None) -> IngestionRuntime:
    """Backward-compatible D4a runtime built from the shared live assembly."""
    assembly = build_ingestion_assembly(
        config, gamma_fetch=gamma_fetch, ws_connect=ws_connect,
        data_fetch=data_fetch, stamper=stamper,
    )
    return IngestionRuntime(services=assembly.services, writer=assembly.writer,
                            heartbeat=assembly.heartbeat,
                            heartbeat_interval_seconds=config.heartbeat_interval_seconds)


async def _amain(runtime: IngestionRuntime) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass  # add_signal_handler is POSIX-only; the VPS is Linux, dev-on-Windows just Ctrl-C's
    await runtime.run()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="polybot-ingestion")
    parser.add_argument("--config", default=None, help="path to an ingestion TOML config")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(level=config.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runtime = build_ingestion_runtime(config)
    try:
        asyncio.run(_amain(runtime))
        return 0
    except Exception:  # a collector HALT surfaces as an ExceptionGroup -> non-zero for systemd Restart=on-failure
        log.exception("ingestion runtime halted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
