"""Bounded, non-persisting live diagnostic for repeated CLOB resync storms.

Run only while the production Polymarket units are stopped so there is exactly
one websocket collector. The probe uses production discovery and shard sizing,
keeps books in memory, and writes no market rows or raw frames.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.sharding import ShardedMarketCollector
from polybot.ingestion.transport import WS_RECONNECT_ON, open_market_ws
from polybot.runtime.config import load_config
from polybot.runtime.discovery import discover_universe, make_gamma_fetch


def make_diagnostic_collector(token_ids, *, max_assets_per_shard, connect=open_market_ws):
    return ShardedMarketCollector(
        connect,
        MonotonicStamper(),
        token_ids,
        sink=None,
        max_assets_per_shard=max_assets_per_shard,
        reconnect_on=WS_RECONNECT_ON,
    )


async def run(config_path, *, seconds):
    config = load_config(config_path)
    token_ids = tuple(discover_universe(make_gamma_fetch(config.gamma_url), config))
    collector = make_diagnostic_collector(
        token_ids,
        max_assets_per_shard=config.max_assets_per_shard,
    )
    print(
        f"diagnostic collector: tokens={len(token_ids)} shards={collector.shard_count} "
        f"seconds={seconds:g} persistence=none",
        flush=True,
    )
    try:
        await asyncio.wait_for(
            collector.run(max_connections=None),
            timeout=seconds,
        )
    except TimeoutError:
        print("diagnostic bound reached without a terminal resync storm", flush=True)


def _positive_finite(raw):
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and > 0")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--seconds", type=_positive_finite, default=1800.0)
    args = parser.parse_args()
    asyncio.run(run(args.config, seconds=args.seconds))


if __name__ == "__main__":
    main()

