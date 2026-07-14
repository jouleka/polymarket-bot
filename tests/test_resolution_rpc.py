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
    AdapterEvent,
    CONDITION_PREPARATION_TOPIC,
    CONDITION_RESOLUTION_TOPIC,
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


def _word(value):
    return f"{value:064x}"


def _ctf_log(topic, condition_id, adapter, question_id, block_number,
             log_index, transaction_hash, data):
    return {
        "address": CTF_ADDRESS,
        "blockNumber": hex(block_number),
        "transactionHash": transaction_hash,
        "logIndex": hex(log_index),
        "removed": False,
        "topics": [
            topic, condition_id, "0x" + "00" * 12 + adapter[2:], question_id,
        ],
        "data": "0x" + data,
    }


def test_ctf_events_tie_condition_adapter_question_and_payout():
    condition_id = "0x" + "15" * 32
    question_id = "0x" + "16" * 32
    transaction_hash = "0x" + "17" * 32
    adapter = ADAPTER_POLICIES[-1].address
    payout = PayoutVector((3, 1), 4)
    preparation = _ctf_log(
        CONDITION_PREPARATION_TOPIC, condition_id, adapter, question_id,
        100, 1, "0x" + "18" * 32, _word(2),
    )
    resolution = _ctf_log(
        CONDITION_RESOLUTION_TOPIC, condition_id, adapter, question_id,
        200, 2, transaction_hash,
        _word(2) + _word(64) + _word(2) + _word(3) + _word(1),
    )
    rpc = _Rpc([preparation], [resolution])
    provider = JsonRpcResolutionProvider("archive-a", rpc)

    authority = provider._ctf_authority(condition_id, 100, 200, payout)
    assert authority.adapter_address == adapter
    assert authority.question_id == question_id
    assert authority.policy == ADAPTER_POLICIES[-1]
    assert authority.audit_event_ids == (
        "100:1:" + "0x" + "18" * 32 + ":CONDITION_PREPARATION",
        "200:2:" + transaction_hash + ":CONDITION_RESOLUTION",
    )
    assert provider._link_adapter_terminal(
        authority, question_id, 200, 3, transaction_hash
    ) is None
    assert rpc.calls == [
        ("eth_getLogs", [{
            "address": CTF_ADDRESS, "fromBlock": "0x64", "toBlock": "0x64",
            "topics": [CONDITION_PREPARATION_TOPIC, condition_id],
        }]),
        ("eth_getLogs", [{
            "address": CTF_ADDRESS, "fromBlock": "0xc8", "toBlock": "0xc8",
            "topics": [CONDITION_RESOLUTION_TOPIC, condition_id],
        }]),
    ]

    for changed in ("condition", "adapter", "question", "payout", "duplicate"):
        bad_preparation = dict(preparation)
        bad_resolution = dict(resolution)
        if changed == "condition":
            bad_resolution["topics"] = list(resolution["topics"])
            bad_resolution["topics"][1] = "0x" + "19" * 32
        elif changed == "adapter":
            bad_resolution["topics"] = list(resolution["topics"])
            bad_resolution["topics"][2] = (
                "0x" + "00" * 12 + ADAPTER_POLICIES[-2].address[2:]
            )
        elif changed == "question":
            bad_resolution["topics"] = list(resolution["topics"])
            bad_resolution["topics"][3] = "0x" + "20" * 32
        elif changed == "payout":
            bad_resolution["data"] = (
                "0x" + _word(2) + _word(64) + _word(2) + _word(2) + _word(2)
            )
        invalid_rpc = _Rpc(
            [bad_preparation],
            [bad_resolution, bad_resolution] if changed == "duplicate"
            else [bad_resolution],
        )
        invalid = JsonRpcResolutionProvider("archive-a", invalid_rpc)
        with pytest.raises(ResolutionUnavailable):
            invalid._ctf_authority(condition_id, 100, 200, payout)

    unsupported_log = _ctf_log(
        CONDITION_PREPARATION_TOPIC, condition_id, "0x" + "99" * 20,
        question_id, 100, 1, "0x" + "18" * 32, _word(2),
    )
    unsupported_resolution = _ctf_log(
        CONDITION_RESOLUTION_TOPIC, condition_id, "0x" + "99" * 20,
        question_id, 200, 2, transaction_hash,
        _word(2) + _word(64) + _word(2) + _word(3) + _word(1),
    )
    unsupported = JsonRpcResolutionProvider(
        "archive-a", _Rpc([unsupported_log], [unsupported_resolution])
    )._ctf_authority(condition_id, 100, 200, payout)
    assert unsupported.policy is None

    for link in (
        ("0x" + "21" * 32, 200, 3, transaction_hash),
        (question_id, 201, 3, transaction_hash),
        (question_id, 200, 1, transaction_hash),
        (question_id, 200, 3, "0x" + "22" * 32),
    ):
        with pytest.raises(ResolutionUnavailable, match="terminal"):
            provider._link_adapter_terminal(authority, *link)


def _adapter_event(kind, question_id, block_number, log_index, *, manual=False):
    return AdapterEvent(
        kind, question_id, block_number, log_index,
        "0x" + f"{block_number % 256:02x}" * 32,
        manual,
    )


def test_v1_path_never_claims_normal_resolution_is_clear():
    question_id = "0x" + "23" * 32
    normal = _adapter_event("QUESTION_RESOLVED", question_id, 200, 3)
    updated = _adapter_event("QUESTION_UPDATED", question_id, 150, 1)
    reset = _adapter_event("QUESTION_RESET", question_id, 175, 2)
    flagged = _adapter_event(
        "QUESTION_FLAGGED_FOR_ADMIN_RESOLUTION", question_id, 180, 4
    )
    emergency = _adapter_event(
        "QUESTION_RESOLVED", question_id, 200, 3, manual=True
    )
    provider = JsonRpcResolutionProvider("archive-a", _Rpc())

    assert provider._normalize_v1(question_id, (normal,)).dispute.name == "UNKNOWN"
    update_proof = provider._normalize_v1(question_id, (updated, normal))
    assert update_proof.dispute.name == "UNKNOWN"
    assert update_proof.audit_event_ids == (
        updated.audit_event_id, normal.audit_event_id
    )

    disputed = provider._normalize_v1(question_id, (reset, normal))
    assert disputed.dispute.name == "DISPUTED"
    manual = provider._normalize_v1(question_id, (reset, flagged, normal))
    assert manual.dispute.name == "MANUAL"
    assert manual.audit_event_ids == (
        reset.audit_event_id, flagged.audit_event_id, normal.audit_event_id
    )
    emergency_proof = provider._normalize_v1(question_id, (emergency,))
    assert emergency_proof.dispute.name == "MANUAL"

    combined = provider._normalize_v1(
        question_id, (updated, reset, flagged, normal)
    )
    assert combined.dispute.name == "MANUAL"
    assert combined.audit_event_ids == tuple(
        event.audit_event_id for event in (updated, reset, flagged, normal)
    )


def test_v2_plus_path_normalizes_normal_reset_flag_and_emergency():
    question_id = "0x" + "24" * 32
    normal = _adapter_event("QUESTION_RESOLVED", question_id, 200, 4)
    reset = _adapter_event("QUESTION_RESET", question_id, 150, 1)
    flagged = _adapter_event("QUESTION_FLAGGED", question_id, 160, 2)
    unflagged = _adapter_event("QUESTION_UNFLAGGED", question_id, 170, 3)
    emergency = _adapter_event(
        "QUESTION_EMERGENCY_RESOLVED", question_id, 200, 4
    )
    provider = JsonRpcResolutionProvider("archive-a", _Rpc())

    clear = provider._normalize_v2_plus(question_id, (normal,))
    assert clear.dispute.name == "CLEAR"
    assert clear.terminal_event == normal

    disputed = provider._normalize_v2_plus(question_id, (reset, normal))
    assert disputed.dispute.name == "DISPUTED"
    assert disputed.audit_event_ids == (
        reset.audit_event_id, normal.audit_event_id
    )

    flagged_proof = provider._normalize_v2_plus(
        question_id, (flagged, unflagged, normal)
    )
    assert flagged_proof.dispute.name == "MANUAL"
    assert flagged_proof.audit_event_ids == (
        flagged.audit_event_id, unflagged.audit_event_id, normal.audit_event_id
    )

    emergency_proof = provider._normalize_v2_plus(question_id, (emergency,))
    assert emergency_proof.dispute.name == "MANUAL"
    assert emergency_proof.terminal_event == emergency

    combined = provider._normalize_v2_plus(
        question_id, (reset, flagged, unflagged, normal)
    )
    assert combined.dispute.name == "MANUAL"
    assert combined.audit_event_ids == tuple(
        event.audit_event_id for event in (reset, flagged, unflagged, normal)
    )
