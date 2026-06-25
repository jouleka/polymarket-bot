"""Live many-shard endurance check (POL-12 follow-up).

Drives the SHARDED CLOB collector with several concurrent WS connections, persisting
through the off-loop ``QueuedEventWriter``, to confirm that with the per-frame commit
moved off the event loop the single writer thread SUSTAINS the aggregate frame rate of
many shards. This is the empirical gate for raising the production shard count past the
live-verified 2 (POL-12 unblocked it in principle; this measures it under real load).
Read-only, no auth, no orders.

Asserts: no HALT/exception; observed_at strictly monotonic + UNIQUE across ALL shards
(global ordering from the one shared stamper); books built; and the writer's high-water
backlog stayed far under its ceiling (it kept up — no stall). Reports throughput
(rows/s), peak backlog, shard count.

Not a unit test (network-dependent). Run manually:
    ./.venv/bin/python scripts/shard_endurance_check.py
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.gamma import normalize_market
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.sharding import ShardedMarketCollector
from polybot.ingestion.transport import GAMMA_URL, WS_RECONNECT_ON, open_market_ws
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore

WS_SECONDS = 40
MAX_ASSETS_PER_SHARD = 8  # small, to force MANY shards (many concurrent connections)


def pick_markets(n=24):
    resp = httpx.get(f"{GAMMA_URL}/markets",
                     params={"limit": 200, "closed": "false", "active": "true",
                             "order": "volume24hr", "ascending": "false"}, timeout=20)
    resp.raise_for_status()
    out = []
    for raw in resp.json():
        if not all(k in raw for k in ("clobTokenIds", "outcomes", "outcomePrices")):
            continue
        try:
            m = normalize_market(raw)
        except Exception:
            continue
        if len(m.outcomes) == 2 and raw.get("acceptingOrders"):
            out.append(m)
        if len(out) >= n:
            break
    return out


async def run_capture(token_ids, db_path):
    stamper = MonotonicStamper()
    writer = QueuedEventWriter(EventStore(db_path, check_same_thread=False))
    collector = ShardedMarketCollector(
        open_market_ws, stamper, token_ids,
        sink=PersistingSink(writer), max_assets_per_shard=MAX_ASSETS_PER_SHARD,
        reconnect_on=WS_RECONNECT_ON,
    )
    error = None
    start = time.monotonic()
    try:
        await asyncio.wait_for(collector.run(max_connections=None), timeout=WS_SECONDS)
    except asyncio.TimeoutError:
        pass  # expected: the endurance window elapsed
    except Exception as exc:  # a shard HALT surfaces as an ExceptionGroup from the TaskGroup
        error = exc
    elapsed = time.monotonic() - start

    books = sum(1 for a in token_ids
                if (b := collector.book_for(a)) is not None and b.midpoint() is not None)
    peak = writer.peak_pending()
    try:
        writer.close()  # drains; re-raises if the writer thread failed (e.g. backlog HALT)
    except Exception as exc:
        error = error or exc
    return collector.shard_count, elapsed, books, peak, error


def main():
    markets = pick_markets()
    token_ids = [o.token_id for m in markets for o in m.outcomes]
    db_path = tempfile.mktemp(suffix=".db")
    print(f"endurance: {len(token_ids)} assets across shards (<= {MAX_ASSETS_PER_SHARD}/shard) "
          f"for {WS_SECONDS}s ...")
    shard_count, elapsed, books, peak, error = asyncio.run(run_capture(token_ids, db_path))

    failures = []
    if error is not None:
        failures.append(f"run raised (HALT/exception): {error!r}")

    store = EventStore(db_path)
    rows = store.all()
    store.close()
    stamps = [e.observed_at for e in rows]
    monotonic = all(b > a for a, b in zip(stamps, stamps[1:]))
    unique = len(set(stamps)) == len(stamps)
    if not monotonic:
        failures.append("observed_at not strictly increasing across shards")
    if not unique:
        failures.append("observed_at collision across shards")
    if not rows:
        failures.append("no rows persisted")
    if books == 0:
        failures.append("no books built")

    rate = len(rows) / elapsed if elapsed > 0 else 0
    print(f"\nshards: {shard_count}   rows: {len(rows)}   elapsed: {elapsed:.1f}s   "
          f"throughput: {rate:.0f} rows/s")
    print(f"books with midpoint: {books}/{len(token_ids)}   "
          f"writer peak backlog: {peak} (ceiling 100000)")
    print(f"observed_at monotonic: {monotonic}   unique: {unique}")
    if failures:
        print(f"\nRESULT: FAIL ({len(failures)})")
        for f in failures[:10]:
            print("  -", f)
        sys.exit(1)
    print(f"\nRESULT: PASS - {shard_count} shards sustained; the single off-loop writer kept up "
          f"(peak backlog {peak} << ceiling), observed_at globally ordered + unique, no stall/HALT.")


if __name__ == "__main__":
    main()
