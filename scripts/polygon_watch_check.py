"""Live read-only check: the Polygon log watcher ingests real on-chain ground truth.

Scans the most recent blocks for ConditionalTokens (ERC-1155) transfers + CTF
Exchange fills, decodes them, and persists them to a temp Market-Memory store.
Read-only, no keys. Run manually:
    ./.venv/bin/python scripts/polygon_watch_check.py
"""

import asyncio
import collections
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.polygon import PolygonLogWatcher
from polybot.ingestion.retry import with_retry
from polybot.ingestion.transport import is_transient_rpc_error, make_rpc_fetch
from polybot.storage.market_memory import EventStore

SPAN = 3  # recent blocks (the CTF is a firehose ~350 transfers/block; keep the sample small)


async def main():
    # Production 24/7 wiring: ONE shared httpx client (connection pooling) + bounded,
    # selective retry so a transient RPC blip (timeout / 429 / 5xx) doesn't kill the
    # watch loop, while a JSON-RPC / contract error still fails loud.
    async with httpx.AsyncClient(timeout=30.0) as client:
        fetch = with_retry(make_rpc_fetch(client=client), is_retryable=is_transient_rpc_error)
        with EventStore(tempfile.mktemp(suffix=".db")) as store:
            watcher = PolygonLogWatcher(fetch, MonotonicStamper(), store)
            head = await watcher.latest_block()
            n = await watcher.poll_once(head - SPAN, head)
            print(f"latest confirmed block {head}; scanned {SPAN} blocks; ingested {n} ground-truth logs")

            rows = store.all()
            kinds = collections.Counter()
            example = {}
            bad_token = 0
            for r in rows:
                ev = json.loads(r.content)["event"]
                kinds[ev["kind"]] += 1
                example.setdefault(ev["kind"], (ev, r.market_links, r.published_at))
                # sanity: decoded transfers carry big ERC-1155 token ids matching market links
                if ev["kind"] == "transfer_single" and (len(ev["token_id"]) < 20 or r.market_links != (ev["token_id"],)):
                    bad_token += 1
                if ev["kind"] == "transfer_batch" and tuple(ev["token_ids"]) != r.market_links:
                    bad_token += 1

            print("by kind:", dict(kinds))
            for k, (ev, links, blk) in example.items():
                print(f"  [{k}] block={blk} links={[l[:14] + '..' for l in links]}")
                print(f"       {json.dumps(ev)[:200]}")
            monotonic = all(b > a for a, b in zip([r.observed_at for r in rows], [r.observed_at for r in rows][1:]))
            ok = n > 0 and kinds.get("transfer_single", 0) + kinds.get("transfer_batch", 0) > 0 and bad_token == 0 and monotonic
            print(f"\nRESULT: {'PASS' if ok else 'CHECK'}  "
                  f"(decoded transfers present={kinds.get('transfer_single',0)+kinds.get('transfer_batch',0)>0}, "
                  f"token/link mismatches={bad_token}, observed_at monotonic={monotonic})")


if __name__ == "__main__":
    asyncio.run(main())
