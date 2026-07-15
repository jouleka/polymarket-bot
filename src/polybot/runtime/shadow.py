"""Executable production composition for the paper-only POL-17 runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.transport import make_text_fetch
from polybot.runtime.history_clock import make_history_stamper
from polybot.runtime.shadow_adapters import (
    SingletonLock,
    SystemdReadiness,
    make_gamma_snapshot_fetch,
    make_resolution_providers,
)
from polybot.runtime.shadow_config import load_shadow_config
from polybot.runtime.shadow_root import build_shadow_runtime


log = logging.getLogger("polybot.shadow")
LOCK_PATH = "/run/polybot/shadow-runtime.lock"


def build_production_runtime(
        config, *, gamma_factory=None, provider_factory=make_resolution_providers,
        history_stamper_factory=make_history_stamper,
        health_stamper_factory=MonotonicStamper,
        news_fetch_factory=make_text_fetch,
        lock_factory=SingletonLock, readiness_factory=SystemdReadiness,
        root_builder=build_shadow_runtime):
    """Own all live read-only adapters and transfer them to one paper runtime."""
    if gamma_factory is None:
        gamma_factory = lambda runtime_config: make_gamma_snapshot_fetch(
            runtime_config.ingestion,
            timeout=runtime_config.rpc_timeout_seconds,
        )
    lock = lock_factory(LOCK_PATH)
    lock.acquire()
    lock_owned = True
    gamma = None
    providers = None
    provider_close = None
    try:
        gamma = gamma_factory(config)
        providers, provider_close = provider_factory(config)
        runtime = root_builder(
            config,
            gamma_snapshot_fetch=gamma,
            resolution_providers=providers,
            history_stamper=history_stamper_factory(config.database_paths),
            health_stamper=health_stamper_factory(),
            news_fetch=news_fetch_factory(timeout=config.rpc_timeout_seconds),
            lock=lock,
            readiness=readiness_factory(),
            extra_closers=(gamma.close, provider_close),
            lock_acquired=True,
        )
        lock_owned = False
        return runtime
    except Exception:
        if provider_close is not None:
            provider_close()
        if gamma is not None:
            gamma.close()
        if lock_owned:
            lock.release()
        raise


async def _amain(runtime):
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass
    await runtime.run()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="polybot-shadow")
    parser.add_argument("--config", required=True, help="path to composite TOML config")
    args = parser.parse_args(argv)
    try:
        config = load_shadow_config(args.config)
        logging.basicConfig(
            level=config.ingestion.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        runtime = build_production_runtime(config)
        asyncio.run(_amain(runtime))
        return 0
    except Exception:
        log.exception("shadow runtime halted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
