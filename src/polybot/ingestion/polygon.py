"""Polygon on-chain log watcher (POL-3 / S1): tamper-proof ground truth.

Watches the Polymarket ConditionalTokens (CTF, ERC-1155) and CTF Exchange
contracts for outcome-token movements + fills via ``eth_getLogs``, normalizes each
log into a canonical Envelope, and persists it. The contract addresses + event
topic0 hashes below were discovered EMPIRICALLY from a real Polymarket trade
receipt on Polygon (block 0x55031b9, tx 0x4e93a7..., 2026-06-25), not guessed.

Only the well-known, STATIC-layout ERC-1155 events (TransferSingle / TransferBatch)
are decoded into structured fields; any other watched event (the exchange's
OrderFilled / OrdersMatched, whose exact ABI is not independently verified here) is
archived RAW -- its topics + data preserved losslessly -- rather than mis-decoded.

Read-only. The JSON-RPC ``fetch`` is injected so the core is network-free and
testable; the production fetch is a thin httpx JSON-RPC POST (transport.py).
"""

import asyncio
import json

from polybot.core.models import Envelope

# --- discovered on-chain constants (Polygon mainnet) -------------------------
CONDITIONAL_TOKENS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"  # CTF ERC-1155 (outcome tokens)
CTF_EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"        # active CTF Exchange

# ERC-1155 (well-known, static data layout -> decoded):
TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TRANSFER_BATCH = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
# Exchange (archived raw, ABI not independently verified here):
ORDER_FILLED = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
ORDERS_MATCHED = "0x174b3811690657c217184f89418266767c87e4805d09680c39fc9c031c0cab7c"

GROUND_TRUTH_ADDRESSES = (CONDITIONAL_TOKENS, CTF_EXCHANGE)
GROUND_TRUTH_TOPICS = (TRANSFER_SINGLE, TRANSFER_BATCH, ORDER_FILLED, ORDERS_MATCHED)

# NOTE: the CTF is a firehose (~350 transfers/block across ALL Polymarket markets).
# The `addresses`/`topics` filter is configurable so production NARROWS it -- once a
# deposit wallet exists (POL-4), filter to our wallet's transfers (from/to are the
# indexed topics[2]/topics[3]) or to the specific token_ids we trade, for relevant,
# low-volume ground truth (3-way reconciliation in S4). The defaults watch everything.


def _addr(topic):
    """A 32-byte indexed-address topic -> 0x-prefixed 20-byte address."""
    return "0x" + topic[-40:]


def _words(data):
    d = data[2:] if data.startswith("0x") else data
    return [d[i:i + 64] for i in range(0, len(d), 64)]


def decode_log(log):
    """Decode a watched log: structured fields for the static ERC-1155 transfers,
    else a raw marker (topics + data are preserved by the caller regardless)."""
    topic0 = log["topics"][0].lower()
    if topic0 == TRANSFER_SINGLE:
        operator, frm, to = (_addr(t) for t in log["topics"][1:4])
        w = _words(log["data"])
        return {"kind": "transfer_single", "operator": operator, "from": frm, "to": to,
                "token_id": str(int(w[0], 16)), "value": str(int(w[1], 16))}
    if topic0 == TRANSFER_BATCH:
        return _decode_transfer_batch(log)
    return {"kind": "raw", "topic0": topic0}


def _decode_transfer_batch(log):
    operator, frm, to = (_addr(t) for t in log["topics"][1:4])
    w = _words(log["data"])
    ids_at = int(w[0], 16) // 32   # ABI head: byte offset of the ids[] array
    vals_at = int(w[1], 16) // 32  # ... and of the values[] array
    ids = [str(int(w[ids_at + 1 + i], 16)) for i in range(int(w[ids_at], 16))]
    vals = [str(int(w[vals_at + 1 + i], 16)) for i in range(int(w[vals_at], 16))]
    if len(ids) != len(vals):  # ERC-1155 invariant; a mismatch is malformed -> raw
        raise ValueError(f"TransferBatch ids/values length mismatch: {len(ids)} != {len(vals)}")
    return {"kind": "transfer_batch", "operator": operator, "from": frm, "to": to,
            "token_ids": ids, "values": vals}


def _market_links(event):
    if event.get("kind") == "transfer_single":
        return (event["token_id"],)
    if event.get("kind") == "transfer_batch":
        return tuple(event["token_ids"])
    return ()


class PolygonLogWatcher:
    def __init__(self, fetch, stamper, store, *, addresses=GROUND_TRUTH_ADDRESSES,
                 topics=GROUND_TRUTH_TOPICS, source="polygon-chain", source_tier="CHAIN"):
        self._fetch = fetch  # async (method, params) -> result
        self._stamper = stamper
        self._store = store
        self._addresses = [a.lower() for a in addresses]
        self._topics = [t.lower() for t in topics]
        self._source = source
        self._source_tier = source_tier

    async def latest_block(self):
        return int(await self._fetch("eth_blockNumber", []), 16)

    async def poll_once(self, from_block, to_block):
        """Fetch + persist all watched logs in [from_block, to_block]. Idempotent on
        re-poll via the store's UNIQUE(source, event_id=txHash:logIndex)."""
        logs = await self._fetch("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": self._addresses,
            "topics": [self._topics],  # topic0 in the watched set
        }])
        persisted = 0
        for log in logs:
            try:
                event = decode_log(log)
            except Exception as exc:
                # A ground-truth layer must NOT wedge on one bad log (run() would
                # re-poll the same range and re-crash forever). Archive it RAW with the
                # reason and keep going -- the raw topics+data are preserved in content
                # regardless -- mirroring data_api.py's skip-bad-row resilience.
                topics = log.get("topics") or [None]
                event = {"kind": "raw", "topic0": topics[0], "decode_error": str(exc)}
            self._store.append(Envelope(
                source=self._source,
                source_tier=self._source_tier,
                event_id=f"{log['transactionHash']}:{int(log['logIndex'], 16)}",
                observed_at=self._stamper.stamp(),
                content=json.dumps({"log": log, "event": event}, sort_keys=True, default=str),
                published_at=int(log["blockNumber"], 16),  # block HEIGHT, not a unix ts (deliberate;
                market_links=_market_links(event),          # cross-source compares must account for this)
            ))
            persisted += 1
        return persisted

    async def run(self, *, from_block=None, confirmations=5, max_span=500,
                  interval=2.0, sleep=asyncio.sleep, max_polls=None):
        """Continuously ingest forward from the confirmed head (latest - confirmations,
        to ride out reorgs), catching up in <=max_span-block chunks. from_block set =
        backfill from there; None = start at the current confirmed head (no history)."""
        last = from_block
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            head = await self.latest_block() - confirmations
            if last is None:
                last = head  # no historical backfill by default
            while last < head:
                to = min(last + max_span, head)
                await self.poll_once(last + 1, to)
                last = to
            if max_polls is None or polls < max_polls:
                await sleep(interval)
        return last
