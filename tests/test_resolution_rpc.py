"""POL-15 strict Polygon JSON-RPC provider boundary."""

import pytest

from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.models import (
    CTF_ADDRESS,
    LifecyclePhase,
    PUSD_ADDRESS,
    PayoutVector,
    ResolutionSubject,
)
from polybot.resolution.rpc import (
    ADAPTER_POLICIES,
    CTF_DEPLOYMENT_BLOCK,
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


class _TransitionRpc:
    def __init__(self, preparation_block, resolution_block):
        self.preparation_block = preparation_block
        self.resolution_block = resolution_block
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        selector = params[0]["data"][:10]
        block_number = int(params[1], 16)
        if selector == "0xd42dc0c2":
            value = 2 if block_number >= self.preparation_block else 0
        elif selector == "0xdd34de67":
            value = 4 if block_number >= self.resolution_block else 0
        else:
            raise AssertionError(f"unexpected transition selector {selector}")
        return "0x" + f"{value:064x}"


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


def test_provider_verifies_code_transition_for_ctf_and_selected_adapter():
    selected = ADAPTER_POLICIES[-1]
    rpc = _Rpc("0x", "0x6001", "0x", "0x6002")
    provider = JsonRpcResolutionProvider("archive-a", rpc)

    assert provider._verify_deployments(selected.address) is None
    assert rpc.calls == [
        ("eth_getCode", [CTF_ADDRESS, hex(CTF_DEPLOYMENT_BLOCK - 1)]),
        ("eth_getCode", [CTF_ADDRESS, hex(CTF_DEPLOYMENT_BLOCK)]),
        ("eth_getCode", [selected.address, hex(selected.deployment_block - 1)]),
        ("eth_getCode", [selected.address, hex(selected.deployment_block)]),
    ]
    assert provider._verify_deployments(selected.address) is None
    assert len(rpc.calls) == 4

    for results in (
        ("0x60", "0x6001", "0x", "0x6002"),
        ("0x", "0x", "0x", "0x6002"),
        ("0x", "0x6001", "0x60", "0x6002"),
        ("0x", "0x6001", "0x", "0x"),
        ("0x", "0x6001", "0x0", "0x6002"),
    ):
        invalid = JsonRpcResolutionProvider("archive-a", _Rpc(*results))
        with pytest.raises(ResolutionUnavailable, match="deployment"):
            invalid._verify_deployments(selected.address)

    with pytest.raises(ResolutionUnavailable, match="adapter"):
        provider._verify_deployments("0x" + "99" * 20)


def test_provider_reads_binary_ctf_payout_at_requested_block():
    condition_id = "0x" + "12" * 32
    rpc = _Rpc(*(
        "0x" + f"{value:064x}" for value in (2, 4, 3, 1)
    ))
    provider = JsonRpcResolutionProvider("archive-a", rpc)
    assert provider._read_payout(condition_id, 99) == (
        LifecyclePhase.FINALIZED, PayoutVector((3, 1), 4)
    )
    assert all(
        method == "eth_call" and params[1] == "0x63"
        for method, params in rpc.calls
    )
    assert [call[1][0]["data"][:10] for call in rpc.calls] == [
        "0xd42dc0c2", "0xdd34de67", "0x0504c814", "0x0504c814",
    ]

    unresolved_rpc = _Rpc(
        "0x" + f"{2:064x}", "0x" + f"{0:064x}"
    )
    unresolved = JsonRpcResolutionProvider("archive-a", unresolved_rpc)
    assert unresolved._read_payout(condition_id, 100) == (
        LifecyclePhase.UNRESOLVED, None
    )
    assert len(unresolved_rpc.calls) == 2
    assert all(call[1][1] == "0x64" for call in unresolved_rpc.calls)

    for values in ((1,), (3,), (2, 4, 3, 0)):
        invalid = JsonRpcResolutionProvider(
            "archive-a", _Rpc(*(
                "0x" + f"{value:064x}" for value in values
            ))
        )
        with pytest.raises(ResolutionUnavailable, match="payout"):
            invalid._read_payout(condition_id, 99)


def test_provider_derives_pusd_positions_in_slot_order():
    subject = ResolutionSubject(
        "event-1", "0x" + "13" * 32, ("101", "202"), "politics"
    )
    rpc = _Rpc(
        "0x" + "31" * 32, "0x" + f"{101:064x}",
        "0x" + "32" * 32, "0x" + f"{202:064x}",
    )
    provider = JsonRpcResolutionProvider("archive-a", rpc)
    assert provider._derive_positions(subject, 99) == ("101", "202")
    assert [params[0]["data"][:10] for _, params in rpc.calls] == [
        "0x856296f7", "0x39dd7530", "0x856296f7", "0x39dd7530",
    ]
    assert all(params[1] == "0x63" for _, params in rpc.calls)
    collateral_word = "00" * 12 + PUSD_ADDRESS[2:]
    assert all(
        params[0]["data"][10:74] == collateral_word
        for _, params in (rpc.calls[1], rpc.calls[3])
    )

    for derived_ids in ((202, 101), (303, 404)):
        mismatched = JsonRpcResolutionProvider("archive-a", _Rpc(
            "0x" + "31" * 32, "0x" + f"{derived_ids[0]:064x}",
            "0x" + "32" * 32, "0x" + f"{derived_ids[1]:064x}",
        ))
        with pytest.raises(ResolutionUnavailable, match="token"):
            mismatched._derive_positions(subject, 99)


def test_provider_binary_searches_exact_preparation_and_resolution_transitions():
    condition_id = "0x" + "14" * 32
    preparation_block = 30_123_456
    resolution_block = 49_876_543
    acceptance_block = 50_000_000
    rpc = _TransitionRpc(preparation_block, resolution_block)
    provider = JsonRpcResolutionProvider("archive-a", rpc)

    assert provider._transition_blocks(condition_id, acceptance_block) == (
        preparation_block, resolution_block
    )
    slot_blocks = [
        int(params[1], 16) for _, params in rpc.calls
        if params[0]["data"].startswith("0xd42dc0c2")
    ]
    denominator_blocks = [
        int(params[1], 16) for _, params in rpc.calls
        if params[0]["data"].startswith("0xdd34de67")
    ]
    assert min(slot_blocks + denominator_blocks) >= CTF_DEPLOYMENT_BLOCK
    assert len(slot_blocks) <= 28
    assert len(denominator_blocks) <= 28

    too_early_rpc = _TransitionRpc(preparation_block, resolution_block)
    too_early = JsonRpcResolutionProvider("archive-a", too_early_rpc)
    with pytest.raises(ResolutionUnavailable, match="deployment"):
        too_early._transition_blocks(condition_id, CTF_DEPLOYMENT_BLOCK - 1)
    assert too_early_rpc.calls == []

    reversed_rpc = _TransitionRpc(49_900_000, 49_800_000)
    reversed_provider = JsonRpcResolutionProvider("archive-a", reversed_rpc)
    with pytest.raises(ResolutionUnavailable, match="transition"):
        reversed_provider._transition_blocks(condition_id, acceptance_block)
