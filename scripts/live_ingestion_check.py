"""Live, read-only end-to-end ingestion smoke check (POL-3 / S1).

Drives the tested ingestion core against the REAL public Polymarket venue:
  1. Gamma   -> normalize a live market (gamma normalizer)
  2. Data API -> poll real /trades through DataApiPoller + httpx fetch
  3. CLOB WS  -> build a live order book through MarketSocket -> MarketStream

Read-only, no auth, no orders. Not a unit test (network-dependent) — run manually:
    ./.venv/bin/python scripts/live_ingestion_check.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.data_api import DataApiPoller
from polybot.ingestion.gamma import normalize_market
from polybot.ingestion.market_socket import MarketSocket
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.transport import (
    DATA_API_URL,
    GAMMA_URL,
    WS_RECONNECT_ON,
    make_httpx_fetch,
    open_market_ws,
)
from polybot.storage.market_memory import EventStore

WS_SECONDS = 8


def pick_market():
    resp = httpx.get(
        f"{GAMMA_URL}/markets",
        params={"limit": 40, "closed": "false", "active": "true",
                "order": "volume24hr", "ascending": "false"},
        timeout=15,
    )
    resp.raise_for_status()
    for raw in resp.json():
        if not all(k in raw for k in ("clobTokenIds", "outcomes", "outcomePrices")):
            continue
        market = normalize_market(raw)
        if len(market.outcomes) == 2 and raw.get("acceptingOrders"):
            return raw, market
    raise SystemExit("no acceptable live binary market found")


async def main():
    raw, market = pick_market()
    print("1) GAMMA normalize")
    print(f"   {market.question[:64]!r}")
    print(f"   conditionId={market.condition_id[:18]}...  outcomes="
          f"{[(o.name, str(o.price)) for o in market.outcomes]}")
    token_ids = [o.token_id for o in market.outcomes]

    stamper = MonotonicStamper()
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        print("\n2) DATA API poll /trades")
        poller = DataApiPoller(make_httpx_fetch(DATA_API_URL), stamper, store)
        n = await poller.poll_once("/trades", params={"market": market.condition_id, "limit": 25})
        print(f"   persisted {n} trade observations")

        print(f"\n3) CLOB WS live book ({WS_SECONDS}s)")
        stream = MarketStream(stamper, sink=PersistingSink(store))
        socket = MarketSocket(open_market_ws, stream, asset_ids=token_ids,
                              reconnect_on=WS_RECONNECT_ON)
        try:
            await asyncio.wait_for(socket.run(max_connections=1), timeout=WS_SECONDS)
        except asyncio.TimeoutError:
            pass
        for tid in token_ids:
            book = stream.book_for(tid)
            if book is None:
                print(f"   token {tid[:14]}...  (no frames)")
                continue
            print(f"   token {tid[:14]}...  bid={book.best_bid()} ask={book.best_ask()} "
                  f"mid={book.midpoint()}")

        rows = store.all()
        print(f"\n   Market-Memory rows persisted: {len(rows)} "
              f"(sources: {sorted({e.source for e in rows})})")


if __name__ == "__main__":
    asyncio.run(main())
