"""Tests for the Polygon on-chain log watcher (POL-3 / S1, ground truth).

Fixtures are REAL logs captured from a Polymarket trade receipt on Polygon
(block 0x55031b9, tx 0x4e93a7..., 2026-06-25) so the ERC-1155 decoders are
verified against actual chain data, not a hand-built guess.
"""

import asyncio
import json

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.polygon import (
    CONDITIONAL_TOKENS,
    GROUND_TRUTH_ADDRESSES,
    GROUND_TRUTH_TOPICS,
    PolygonLogWatcher,
    TRANSFER_BATCH,
    TRANSFER_SINGLE,
    decode_log,
)
from polybot.storage.market_memory import EventStore
from tests._temp_db import temporary_db_path

# Real ERC-1155 logs from the ConditionalTokens contract.
_TS_LOG = json.loads(
    '{"address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045", "topics": '
    '["0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62", '
    '"0x000000000000000000000000e111180000d2663c0091e4f400237545b87b996b", '
    '"0x000000000000000000000000e111180000d2663c0091e4f400237545b87b996b", '
    '"0x000000000000000000000000adfaada4a21c0b3d5c6459f2374d1e3dfd43324e"], '
    '"data": "0x26a3b535dd93f340eef52e9a07574726e109f35750e01d55e2329f5378f3ec28'
    '0000000000000000000000000000000000000000000000000000000007a035a0", '
    '"blockNumber": "0x55031b9", '
    '"transactionHash": "0x4e93a707e287f9269cc5f0c668a2241823437c75f370d3f0ac2539a40849545b", '
    '"logIndex": "0x3bc"}'
)
_TB_LOG = json.loads(
    '{"address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045", "topics": '
    '["0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb", '
    '"0x000000000000000000000000ada100874d00e3331d00f2007a9c336a65009718", '
    '"0x0000000000000000000000000000000000000000000000000000000000000000", '
    '"0x000000000000000000000000ada100874d00e3331d00f2007a9c336a65009718"], '
    '"data": "0x0000000000000000000000000000000000000000000000000000000000000040'
    '00000000000000000000000000000000000000000000000000000000000000a0'
    '0000000000000000000000000000000000000000000000000000000000000002'
    'a74f7618ec8d425ee7675a005d0df25952c6f52cb81a7c8f10d88e333033c27b'
    '26a3b535dd93f340eef52e9a07574726e109f35750e01d55e2329f5378f3ec28'
    '0000000000000000000000000000000000000000000000000000000000000002'
    '0000000000000000000000000000000000000000000000000000000007a035a0'
    '0000000000000000000000000000000000000000000000000000000007a035a0", '
    '"blockNumber": "0x55031b9", '
    '"transactionHash": "0x4e93a707e287f9269cc5f0c668a2241823437c75f370d3f0ac2539a40849545b", '
    '"logIndex": "0x3b9"}'
)


def test_decode_transfer_single_from_real_log():
    ev = decode_log(_TS_LOG)
    assert ev["kind"] == "transfer_single"
    assert ev["operator"] == "0xe111180000d2663c0091e4f400237545b87b996b"
    assert ev["from"] == "0xe111180000d2663c0091e4f400237545b87b996b"
    assert ev["to"] == "0xadfaada4a21c0b3d5c6459f2374d1e3dfd43324e"
    assert ev["token_id"] == str(int("26a3b535dd93f340eef52e9a07574726e109f35750e01d55e2329f5378f3ec28", 16))
    assert ev["value"] == "127940000"


def test_decode_transfer_batch_from_real_log():
    ev = decode_log(_TB_LOG)
    assert ev["kind"] == "transfer_batch"
    assert ev["operator"] == "0xada100874d00e3331d00f2007a9c336a65009718"
    assert ev["from"] == "0x0000000000000000000000000000000000000000"
    assert ev["to"] == "0xada100874d00e3331d00f2007a9c336a65009718"
    assert ev["token_ids"] == [
        str(int("a74f7618ec8d425ee7675a005d0df25952c6f52cb81a7c8f10d88e333033c27b", 16)),
        str(int("26a3b535dd93f340eef52e9a07574726e109f35750e01d55e2329f5378f3ec28", 16)),
    ]
    assert ev["values"] == ["127940000", "127940000"]


def test_decode_unknown_event_is_archived_raw_not_misdecoded():
    log = {"topics": ["0xdeadbeef00000000000000000000000000000000000000000000000000000000"],
           "data": "0x"}
    ev = decode_log(log)
    assert ev == {"kind": "raw", "topic0": "0xdeadbeef00000000000000000000000000000000000000000000000000000000"}


class _FakeRPC:
    def __init__(self, head=0, logs=None):
        self.head = head
        self.logs = logs or []
        self.calls = []

    async def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getLogs":
            return self.logs
        raise AssertionError(f"unexpected RPC {method}")


def test_poll_once_persists_each_log_as_a_ground_truth_envelope():
    rpc = _FakeRPC(logs=[_TS_LOG, _TB_LOG])
    with EventStore(temporary_db_path()) as store:
        watcher = PolygonLogWatcher(rpc, MonotonicStamper(), store)

        n = asyncio.run(watcher.poll_once(100, 200))

        assert n == 2
        rows = store.all()
        assert [r.source for r in rows] == ["polygon-chain", "polygon-chain"]
        # event_id = txHash:logIndex (decimal); market_links = the CTF token ids
        assert rows[0].event_id.endswith(":956")  # 0x3bc
        assert rows[0].market_links == (str(int("26a3b535dd93f340eef52e9a07574726e109f35750e01d55e2329f5378f3ec28", 16)),)
        assert json.loads(rows[0].content)["event"]["kind"] == "transfer_single"
        assert rows[0].published_at == int("0x55031b9", 16)


def test_poll_once_is_idempotent_on_repolled_overlap():
    rpc = _FakeRPC(logs=[_TS_LOG, _TB_LOG])
    with EventStore(temporary_db_path()) as store:
        watcher = PolygonLogWatcher(rpc, MonotonicStamper(), store)

        asyncio.run(watcher.poll_once(100, 200))
        asyncio.run(watcher.poll_once(150, 250))  # overlapping re-poll

        assert len(store.all()) == 2  # UNIQUE(source, event_id) dedups the repeats


def test_poll_once_filters_by_the_discovered_addresses_and_topics():
    rpc = _FakeRPC(logs=[])
    with EventStore(temporary_db_path()) as store:
        watcher = PolygonLogWatcher(rpc, MonotonicStamper(), store)

        asyncio.run(watcher.poll_once(10, 20))

        method, params = rpc.calls[0]
        assert method == "eth_getLogs"
        f = params[0]
        assert f["fromBlock"] == "0xa" and f["toBlock"] == "0x14"
        assert f["address"] == [a.lower() for a in GROUND_TRUTH_ADDRESSES]
        assert f["topics"] == [[t.lower() for t in GROUND_TRUTH_TOPICS]]


def test_run_advances_the_confirmed_head_in_bounded_chunks():
    rpc = _FakeRPC(head=1000, logs=[])
    with EventStore(temporary_db_path()) as store:
        watcher = PolygonLogWatcher(rpc, MonotonicStamper(), store)

        async def noop(_):
            pass

        asyncio.run(watcher.run(from_block=0, confirmations=5, max_span=400,
                                sleep=noop, max_polls=1))

        ranges = [(int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16))
                  for m, p in rpc.calls if m == "eth_getLogs"]
        # confirmed head = 1000 - 5 = 995; chunked by 400 from block 0
        assert ranges == [(1, 400), (401, 800), (801, 995)]


def _word(n):
    return format(n, "064x")


def test_decode_transfer_batch_length_mismatch_raises():
    # ERC-1155 requires ids.length == values.length; a mismatch is malformed.
    data = "0x" + _word(0x40) + _word(0xa0) + _word(2) + _word(11) + _word(22) + _word(1) + _word(99)
    log = {"topics": [TRANSFER_BATCH,
                      "0x" + "00" * 12 + "11" * 20, "0x" + "00" * 12 + "22" * 20, "0x" + "00" * 12 + "33" * 20],
           "data": data}
    import pytest
    with pytest.raises(ValueError, match="length mismatch"):
        decode_log(log)


def test_poll_once_archives_a_malformed_log_raw_and_keeps_going():
    # A ground-truth layer must NOT wedge on one bad log: archive it raw (with the
    # decode error) and persist the rest, mirroring the Data API poller.
    good = dict(_TS_LOG)
    malformed = {"topics": [TRANSFER_SINGLE], "data": "0x",  # truncated -> decode raises
                 "blockNumber": "0x10", "transactionHash": "0xdead", "logIndex": "0x1"}
    rpc = _FakeRPC(logs=[good, malformed, _TB_LOG])
    with EventStore(temporary_db_path()) as store:
        watcher = PolygonLogWatcher(rpc, MonotonicStamper(), store)

        n = asyncio.run(watcher.poll_once(1, 2))

        assert n == 3  # all three persisted, none dropped
        events = [json.loads(r.content)["event"] for r in store.all()]
        kinds = [e["kind"] for e in events]
        assert kinds == ["transfer_single", "raw", "transfer_batch"]
        assert "decode_error" in events[1]  # the malformed one archived raw with the reason
