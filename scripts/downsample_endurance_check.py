#!/usr/bin/env python3
"""Bounded public-data storage-rate gate for downsampled ingestion.

The live runner is added after its result contract is pinned. These helpers are
pure so projection and SQLite DB/WAL/SHM accounting stay unit-testable.
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polybot.ingestion.midpoint import MIDPOINT_SOURCE, decode_midpoint_batch
from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import build_ingestion_runtime
from polybot.storage.market_memory import EventStore

GIB = 1024 ** 3
SECONDS_PER_DAY = 86400


def projected_gib_per_day(total_bytes, elapsed_seconds):
    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be > 0")
    return total_bytes / elapsed_seconds * SECONDS_PER_DAY / GIB


def footprint(paths):
    return sum(path.stat().st_size for path in paths if path.exists())


@dataclass(frozen=True)
class CaptureResult:
    source_counts: dict[str, int]
    midpoint_batches: int
    usable_quotes: int
    projected_gib_per_day: float
    failures: tuple[str, ...]

    @property
    def passed(self):
        return not self.failures


def inspect_capture(store, *, projected_rate, max_gib_per_day):
    rows = store.all()
    counts = Counter(row.source for row in rows)
    failures = []
    usable_quotes = 0

    for row in rows:
        if row.source != MIDPOINT_SOURCE:
            continue
        try:
            usable_quotes += len(decode_midpoint_batch(row.content))
        except Exception as exc:
            failures.append(f"malformed midpoint batch {row.event_id}: {exc}")

    midpoint_batches = counts.get(MIDPOINT_SOURCE, 0)
    data_api_rows = counts.get("data-api", 0)
    raw_rows = counts.get("clob-ws", 0)
    if midpoint_batches == 0:
        failures.append("no midpoint batches persisted")
    if data_api_rows == 0:
        failures.append("no data-api trade rows persisted")
    if raw_rows:
        failures.append(f"raw clob-ws rows persisted: {raw_rows}")
    if usable_quotes == 0:
        failures.append("no usable midpoint quotes persisted")
    if projected_rate > max_gib_per_day:
        failures.append(
            f"projected rate {projected_rate:.6f} GiB/day exceeds "
            f"ceiling {max_gib_per_day:.6f} GiB/day"
        )

    return CaptureResult(
        source_counts=dict(sorted(counts.items())),
        midpoint_batches=midpoint_batches,
        usable_quotes=usable_quotes,
        projected_gib_per_day=projected_rate,
        failures=tuple(failures),
    )


def _positive_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and > 0")
    return parsed


def _positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a bounded public-data downsample storage-rate gate.",
    )
    parser.add_argument("--seconds", type=_positive_float, default=1800.0)
    parser.add_argument("--max-gib-per-day", type=_positive_float, default=0.5)
    parser.add_argument("--universe-max-markets", type=_positive_int, default=200)
    parser.add_argument("--keep-db", action="store_true")
    return parser.parse_args(argv)


async def run_runtime_for(runtime, seconds, *, sleep=asyncio.sleep, clock=time.monotonic):
    start = clock()
    runtime_task = asyncio.create_task(runtime.run())
    timer_task = asyncio.create_task(sleep(seconds))
    try:
        done, _ = await asyncio.wait(
            {runtime_task, timer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if runtime_task in done:
            await runtime_task
            raise RuntimeError("ingestion runtime returned before endurance window elapsed")
        runtime.request_stop()
        await runtime_task
        return clock() - start
    finally:
        if not timer_task.done():
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass
        if not runtime_task.done():
            runtime.request_stop()
            runtime_task.cancel()
            try:
                await runtime_task
            except asyncio.CancelledError:
                pass


def _database_paths(db_path):
    return [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]


def _print_result(*, elapsed, total_bytes, result):
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"footprint_bytes={total_bytes}")
    print(f"source_counts={json.dumps(result.source_counts, sort_keys=True)}")
    print(f"midpoint_batches={result.midpoint_batches}")
    print(f"usable_quotes={result.usable_quotes}")
    print(f"projected_gib_per_day={result.projected_gib_per_day:.6f}")
    if result.passed:
        print("RESULT: PASS")
    else:
        print(f"RESULT: FAIL ({len(result.failures)})")
        for failure in result.failures:
            print(f"  - {failure}")


def main(argv=None):
    args = parse_args(argv)
    temp_dir = Path(tempfile.mkdtemp(prefix="polybot-downsample-endurance-"))
    db_path = temp_dir / "market_memory.db"
    try:
        config = IngestionConfig(
            db_path=str(db_path),
            universe_max_markets=args.universe_max_markets,
            data_api_enabled=True,
            snapshot_interval_seconds=60.0,
        )
        try:
            runtime = build_ingestion_runtime(config)
            elapsed = asyncio.run(run_runtime_for(runtime, args.seconds))
        except Exception as exc:
            print(f"RESULT: FAIL (runtime HALT/exception: {exc!r})")
            return 1

        total_bytes = footprint(_database_paths(db_path))
        projected_rate = projected_gib_per_day(total_bytes, elapsed)
        with EventStore(str(db_path)) as store:
            result = inspect_capture(
                store,
                projected_rate=projected_rate,
                max_gib_per_day=args.max_gib_per_day,
            )
        _print_result(elapsed=elapsed, total_bytes=total_bytes, result=result)
        return 0 if result.passed else 1
    finally:
        if args.keep_db:
            print(f"kept_database={db_path}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
