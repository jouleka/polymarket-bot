"""Live replay-fidelity / no-look-ahead acceptance check (POL-3 / S1 gate).

Drives the tested ingestion core against the REAL public Polymarket venue
(read-only, no auth, no orders), persisting every WS frame to a Market-Memory
EventStore. While capturing, it records the live book's top-of-book at periodic
checkpoints (each tagged with the observed_at reached). Afterwards it asserts:

  1. FIDELITY + NO LOOK-AHEAD: for each checkpoint, a book reconstructed from the
     store using ONLY rows with observed_at <= that checkpoint equals the live
     top-of-book captured at that moment (so a later frame cannot leak in).
  2. FULL-REPLAY fidelity: replaying the whole store reproduces the final live book.
  3. MONOTONIC observed_at: every stored record has a strictly-increasing stamp.
  4. RESTART persistence: reopening the DB file and replaying yields the same books.

Not a unit test (network-dependent). Run manually:
    ./.venv/bin/python scripts/replay_fidelity_check.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.gamma import normalize_market
from polybot.ingestion.market_socket import MarketSocket
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.replay import reconstruct_from_store
from polybot.ingestion.transport import GAMMA_URL, WS_RECONNECT_ON, open_market_ws
from polybot.storage.market_memory import EventStore

WS_SECONDS = 35
CHECKPOINT_EVERY = 40  # observations between point-in-time snapshots


def pick_markets(n=3):
    resp = httpx.get(f"{GAMMA_URL}/markets",
                     params={"limit": 150, "closed": "false", "active": "true",
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


def state(book):
    """Point-in-time state for the fidelity comparison: top-of-book AND midpoint.
    midpoint() is the quantity the ERS sizes real money off (None while stale), so
    the gate asserts IT as a first-class value, not just the raw bid/ask levels."""
    if book is None:
        return None
    bid, ask, mid = book.best_bid(), book.best_ask(), book.midpoint()
    return (str(bid) if bid is not None else None,
            str(ask) if ask is not None else None,
            str(mid) if mid is not None else None)


class CapturingSink:
    """Persist each Observation, and every CHECKPOINT_EVERY observations record the
    live top-of-book for all tracked assets tagged with the observed_at reached."""

    def __init__(self, persist, stream, asset_ids, every=CHECKPOINT_EVERY):
        self._persist = persist
        self._stream = stream
        self._asset_ids = asset_ids
        self._every = every
        self._n = 0
        self.checkpoints = []  # list[(observed_at, {asset_id: (bid, ask)})]

    def __call__(self, observation):
        self._persist(observation)
        self._n += 1
        if self._n % self._every == 0:
            snapshot = {a: state(self._stream.book_for(a)) for a in self._asset_ids
                        if self._stream.book_for(a) is not None}
            self.checkpoints.append((observation.observed_at, snapshot))


async def capture(token_ids, db_path):
    stamper = MonotonicStamper()
    store = EventStore(db_path)
    stream = MarketStream(stamper, asset_ids=token_ids)
    sink = CapturingSink(PersistingSink(store), stream, token_ids)
    stream._sink = sink  # wire the capturing sink (stream built first so the sink can read its books)
    socket = MarketSocket(open_market_ws, stream, asset_ids=token_ids, reconnect_on=WS_RECONNECT_ON)
    try:
        await asyncio.wait_for(socket.run(max_connections=None), timeout=WS_SECONDS)
    except asyncio.TimeoutError:
        pass
    final_live = {a: state(stream.book_for(a)) for a in token_ids if stream.book_for(a) is not None}
    store.close()
    return sink.checkpoints, final_live


def main():
    markets = pick_markets(3)
    token_ids = [o.token_id for m in markets for o in m.outcomes]
    print(f"capturing {len(token_ids)} assets ({len(markets)} markets) for {WS_SECONDS}s ...")
    db_path = tempfile.mktemp(suffix=".db")
    checkpoints, final_live = asyncio.run(capture(token_ids, db_path))

    failures = []

    # 1 + 2: point-in-time fidelity / no look-ahead at each checkpoint, then full replay.
    store = EventStore(db_path)
    rows = store.all()
    print(f"captured {len(rows)} store rows, {len(checkpoints)} checkpoints")

    for observed_at, live_snapshot in checkpoints:
        replayed = reconstruct_from_store(store, until=observed_at)
        for asset_id, live_top in live_snapshot.items():
            got = state(replayed.book_for(asset_id))
            if got != live_top:
                failures.append(f"checkpoint oa={observed_at} asset={asset_id[:12]}.. "
                                f"live={live_top} replay={got}")

    full = reconstruct_from_store(store)
    for asset_id, live_top in final_live.items():
        got = state(full.book_for(asset_id))
        if got != live_top:
            failures.append(f"FULL asset={asset_id[:12]}.. live={live_top} replay={got}")

    # 3: monotonic observed_at across every stored record.
    stamps = [e.observed_at for e in rows]
    monotonic = all(b > a for a, b in zip(stamps, stamps[1:]))
    if not monotonic:
        failures.append("observed_at not strictly increasing across store rows")

    # 4: restart persistence -- reopen the same DB file and replay.
    store.close()
    reopened = EventStore(db_path)
    restart_ok = True
    full_after_restart = reconstruct_from_store(reopened)
    for asset_id, live_top in final_live.items():
        if state(full_after_restart.book_for(asset_id)) != live_top:
            restart_ok = False
            failures.append(f"RESTART mismatch asset={asset_id[:12]}..")
    reopened.close()

    checked = sum(len(s) for _, s in checkpoints) + len(final_live)
    print(f"\nfidelity points checked: {checked} (checkpoints + full)")
    print(f"monotonic observed_at: {monotonic}   restart persistence: {restart_ok}")
    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} mismatches)")
        for f in failures[:20]:
            print("  -", f)
        sys.exit(1)
    print("\nRESULT: PASS - every reconstruction matched the live point-in-time state "
          "(fidelity + no look-ahead), observed_at strictly monotonic, DB survives restart.")


if __name__ == "__main__":
    main()
