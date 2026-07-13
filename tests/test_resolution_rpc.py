"""POL-15 strict Polygon JSON-RPC provider boundary."""

import pytest

from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.models import CTF_ADDRESS, PUSD_ADDRESS
from polybot.resolution.rpc import (
    JsonRpcClient,
    JsonRpcResolutionProvider,
    decode_fixed_bytes,
    decode_quantity,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.calls = []

    def post(self, endpoint, *, json):
        self.calls.append((endpoint, json))
        return _Response(self._payloads.pop(0))


class _Rpc:
    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return self._results.pop(0)


def test_rpc_correlates_monotonic_request_id():
    http = _HttpClient(
        {"jsonrpc": "2.0", "id": 1, "result": "0x89"},
        {"jsonrpc": "2.0", "id": 2, "result": "0x10"},
    )
    rpc = JsonRpcClient("https://polygon.example", http)

    assert rpc.call("eth_chainId", []) == "0x89"
    assert rpc.call("eth_blockNumber", []) == "0x10"
    assert http.calls == [
        ("https://polygon.example", {
            "jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": [],
        }),
        ("https://polygon.example", {
            "jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber", "params": [],
        }),
    ]


@pytest.mark.parametrize("payload", [
    [],
    {"id": 1, "result": "0x1"},
    {"jsonrpc": "1.0", "id": 1, "result": "0x1"},
    {"jsonrpc": "2.0", "result": "0x1"},
    {"jsonrpc": "2.0", "id": True, "result": "0x1"},
    {"jsonrpc": "2.0", "id": 2, "result": "0x1"},
    {"jsonrpc": "2.0", "id": 1},
    {"jsonrpc": "2.0", "id": 1, "result": "0x1", "error": {}},
    {"jsonrpc": "2.0", "id": 1, "result": "0x1", "extra": None},
    {"jsonrpc": "2.0", "id": 1, "error": "bad"},
])
def test_rpc_rejects_malformed_response_envelope(payload):
    rpc = JsonRpcClient("https://polygon.example", _HttpClient(payload))
    with pytest.raises(ResolutionUnavailable, match="JSON-RPC"):
        rpc.call("eth_chainId", [])


def test_rpc_surfaces_valid_error_envelope_as_unavailable():
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32000, "message": "archive unavailable"},
    }
    rpc = JsonRpcClient("https://polygon.example", _HttpClient(payload))
    with pytest.raises(ResolutionUnavailable, match="archive unavailable"):
        rpc.call("eth_chainId", [])


def test_rpc_quantity_and_fixed_bytes_decoders_are_canonical():
    assert decode_quantity("0x0") == 0
    assert decode_quantity("0x1") == 1
    assert decode_quantity("0xff") == 255
    for value in (
        True, 1, None, "", "0x", "0x00", "0x01", "0X1", "0xA",
        "0xg", " 0x1", "0x1 ",
    ):
        with pytest.raises(ResolutionUnavailable):
            decode_quantity(value)

    raw = bytes(range(32))
    assert decode_fixed_bytes("0x" + raw.hex(), 32) == raw
    for value in (
        True, raw, None, "", "0x", "0x" + "00" * 31,
        "0x" + "00" * 33, "0x" + "AA" * 32,
        "0x" + "gg" * 32,
    ):
        with pytest.raises(ResolutionUnavailable):
            decode_fixed_bytes(value, 32)
    with pytest.raises(TypeError):
        decode_fixed_bytes("0x00", True)
    with pytest.raises(ValueError):
        decode_fixed_bytes("0x00", 0)


def test_ctf_static_calls_decode_exact_32_byte_words():
    condition_id = "0x" + "11" * 32
    collection_id = "0x" + "22" * 32
    rpc = _Rpc(
        "0x" + f"{2:064x}",
        "0x" + f"{4:064x}",
        "0x" + f"{3:064x}",
        "0x" + "33" * 32,
        "0x" + f"{123:064x}",
    )
    provider = JsonRpcResolutionProvider("archive-a", rpc)

    assert provider._outcome_slot_count(condition_id, 15) == 2
    assert provider._payout_denominator(condition_id, 15) == 4
    assert provider._payout_numerator(condition_id, 1, 15) == 3
    assert provider._collection_id(condition_id, 2, 15) == "0x" + "33" * 32
    assert provider._position_id(collection_id, 15) == 123

    condition_word = condition_id[2:]
    uint_one = f"{1:064x}"
    uint_two = f"{2:064x}"
    zero_word = "00" * 32
    collateral_word = "00" * 12 + PUSD_ADDRESS[2:]
    expected_data = [
        "0xd42dc0c2" + condition_word,
        "0xdd34de67" + condition_word,
        "0x0504c814" + condition_word + uint_one,
        "0x856296f7" + zero_word + condition_word + uint_two,
        "0x39dd7530" + collateral_word + collection_id[2:],
    ]
    assert rpc.calls == [
        ("eth_call", [{"to": CTF_ADDRESS, "data": data}, "0xf"])
        for data in expected_data
    ]

    for malformed in (
        "0x", "0x" + "00" * 31, "0x" + "00" * 33,
        "0x" + "AA" * 32, "0x" + "gg" * 32,
    ):
        malformed_provider = JsonRpcResolutionProvider(
            "archive-a", _Rpc(malformed)
        )
        with pytest.raises(ResolutionUnavailable):
            malformed_provider._outcome_slot_count(condition_id, 15)
