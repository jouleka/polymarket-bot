"""POL-15 strict Polygon JSON-RPC provider boundary."""

import pytest

from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.rpc import (
    JsonRpcClient,
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
