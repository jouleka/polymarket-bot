"""POL-15 strict Polygon JSON-RPC provider boundary."""

from dataclasses import replace

import pytest

from polybot.resolution.errors import ResolutionUnavailable, SettlementConflict
from polybot.resolution.models import (
    CTF_ADDRESS,
    DisputeState,
    LifecyclePhase,
    PUSD_ADDRESS,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.rpc import (
    ADAPTER_POLICIES,
    AdapterEvent,
    CONDITION_PREPARATION_TOPIC,
    CONDITION_RESOLUTION_TOPIC,
    CTF_DEPLOYMENT_BLOCK,
    JsonRpcClient,
    JsonRpcResolutionProvider,
    QUESTION_EMERGENCY_RESOLVED_V2_TOPIC,
    QUESTION_FLAGGED_ADMIN_V1_TOPIC,
    QUESTION_FLAGGED_V2_TOPIC,
    QUESTION_RESET_TOPIC,
    QUESTION_RESOLVED_V1_TOPIC,
    QUESTION_RESOLVED_V2_TOPIC,
    QUESTION_UPDATED_V1_TOPIC,
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
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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


class _ObservationRpc:
    def __init__(self, subject, policy, preparation_block, resolution_block,
                 acceptance_block, block_hash, question_id, transaction_hash):
        self.subject = subject
        self.policy = policy
        self.preparation_block = preparation_block
        self.resolution_block = resolution_block
        self.acceptance_block = acceptance_block
        self.block_hash = block_hash
        self.question_id = question_id
        self.transaction_hash = transaction_hash
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getBlockByNumber":
            return {"number": params[0], "hash": self.block_hash}
        if method == "eth_getCode":
            address, block_tag = params
            deployment = (
                CTF_DEPLOYMENT_BLOCK if address == CTF_ADDRESS
                else self.policy.deployment_block
            )
            return "0x60" if int(block_tag, 16) >= deployment else "0x"
        if method == "eth_call":
            data = params[0]["data"]
            selector = data[:10]
            block_number = int(params[1], 16)
            if selector == "0xd42dc0c2":
                value = 2 if block_number >= self.preparation_block else 0
            elif selector == "0xdd34de67":
                value = 4 if block_number >= self.resolution_block else 0
            elif selector == "0x0504c814":
                value = (3, 1)[int(data[-64:], 16)]
            elif selector == "0x856296f7":
                return "0x" + ("31" if int(data[-64:], 16) == 1 else "32") * 32
            elif selector == "0x39dd7530":
                value = 101 if data[-64:] == "31" * 32 else 202
            else:
                raise AssertionError(f"unexpected call selector {selector}")
            return "0x" + f"{value:064x}"
        if method == "eth_getLogs":
            event_filter = params[0]
            topic = event_filter["topics"][0]
            if topic == CONDITION_PREPARATION_TOPIC:
                return [_ctf_log(
                    topic, self.subject.condition_id, self.policy.address,
                    self.question_id, self.preparation_block, 1,
                    "0x" + "41" * 32, _word(2),
                )]
            if topic == CONDITION_RESOLUTION_TOPIC:
                return [_ctf_log(
                    topic, self.subject.condition_id, self.policy.address,
                    self.question_id, self.resolution_block, 2,
                    self.transaction_hash,
                    _word(2) + _word(64) + _word(2) + _word(3) + _word(1),
                )]
            allowed_topics = topic if isinstance(topic, list) else [topic]
            if (QUESTION_RESOLVED_V2_TOPIC in allowed_topics
                    and int(event_filter["fromBlock"], 16) <= self.resolution_block
                    <= int(event_filter["toBlock"], 16)):
                return [{
                    "address": self.policy.address,
                    "blockNumber": hex(self.resolution_block),
                    "transactionHash": self.transaction_hash,
                    "logIndex": "0x3",
                    "removed": False,
                    "topics": [QUESTION_RESOLVED_V2_TOPIC, self.question_id],
                    "data": "0x" + _word(1) + _word(64) + _word(2)
                    + _word(3) + _word(1),
                }]
            return []
        raise AssertionError(f"unexpected RPC method {method}")


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


def test_empty_unrelated_or_missing_positive_resolution_is_unknown():
    question_id = "0x" + "25" * 32
    unrelated_question = "0x" + "26" * 32
    reset = _adapter_event("QUESTION_RESET", question_id, 150, 1)
    flagged = _adapter_event("QUESTION_FLAGGED", question_id, 160, 2)
    unrelated_resolution = _adapter_event(
        "QUESTION_RESOLVED", unrelated_question, 200, 3
    )
    provider = JsonRpcResolutionProvider("archive-a", _Rpc())

    for proof in (
        provider._normalize_v1(question_id, ()),
        provider._normalize_v2_plus(question_id, ()),
        provider._normalize_v1(question_id, (reset,)),
        provider._normalize_v2_plus(question_id, (flagged,)),
        provider._normalize_v2_plus(question_id, (unrelated_resolution,)),
    ):
        assert proof.dispute.name == "UNKNOWN"
        assert proof.terminal_event is None

    assert provider._normalize_v1(
        question_id, (reset,)
    ).audit_event_ids == (reset.audit_event_id,)
    assert provider._normalize_v2_plus(
        question_id, (flagged,)
    ).audit_event_ids == (flagged.audit_event_id,)
    assert provider._normalize_v2_plus(
        question_id, (unrelated_resolution,)
    ).audit_event_ids == ()


def test_adapter_history_pages_only_derived_interval_in_exact_10000_block_ranges():
    question_id = "0x" + "27" * 32
    provider = JsonRpcResolutionProvider("archive-a", _Rpc())
    v1 = ADAPTER_POLICIES[0]
    v2_plus = ADAPTER_POLICIES[-1]
    expected_ranges = (
        (100, 10_099), (10_100, 20_099), (20_100, 20_105)
    )

    v2_filters = provider._adapter_history_filters(
        v2_plus, question_id, 100, 20_105
    )
    assert tuple(
        (int(item["fromBlock"], 16), int(item["toBlock"], 16))
        for item in v2_filters
    ) == expected_ranges
    assert all(item["address"] == v2_plus.address for item in v2_filters)
    assert all(item["topics"] == [[
        QUESTION_RESET_TOPIC,
        QUESTION_FLAGGED_V2_TOPIC,
        QUESTION_RESOLVED_V2_TOPIC,
        QUESTION_EMERGENCY_RESOLVED_V2_TOPIC,
    ], question_id] for item in v2_filters)

    v1_filters = provider._adapter_history_filters(
        v1, question_id, 100, 20_105
    )
    assert len(v1_filters) == 6
    assert tuple(
        (int(v1_filters[index]["fromBlock"], 16),
         int(v1_filters[index]["toBlock"], 16))
        for index in range(0, len(v1_filters), 2)
    ) == expected_ranges
    for index in range(0, len(v1_filters), 2):
        assert v1_filters[index]["topics"] == [[
            QUESTION_RESET_TOPIC,
            QUESTION_UPDATED_V1_TOPIC,
            QUESTION_RESOLVED_V1_TOPIC,
        ], question_id]
        assert v1_filters[index + 1]["topics"] == [
            QUESTION_FLAGGED_ADMIN_V1_TOPIC
        ]

    single = provider._adapter_history_filters(
        v2_plus, question_id, 500, 500
    )
    assert [(item["fromBlock"], item["toBlock"]) for item in single] == [
        ("0x1f4", "0x1f4")
    ]
    for bounds in ((99, 98), (-1, 10), (True, 10)):
        with pytest.raises(ResolutionUnavailable, match="interval"):
            provider._adapter_history_filters(
                v2_plus, question_id, bounds[0], bounds[1]
            )


def _raw_adapter_log(block_number, log_index, transaction_byte, *, data="0x"):
    return {
        "address": ADAPTER_POLICIES[-1].address,
        "blockNumber": hex(block_number),
        "transactionHash": "0x" + transaction_byte * 32,
        "logIndex": hex(log_index),
        "removed": False,
        "topics": [QUESTION_RESET_TOPIC, "0x" + "27" * 32],
        "data": data,
    }


def test_log_normalization_orders_exact_duplicates_and_rejects_coordinate_conflict():
    provider = JsonRpcResolutionProvider("archive-a", _Rpc())
    first = _raw_adapter_log(100, 1, "31")
    second = _raw_adapter_log(100, 2, "32")
    third = _raw_adapter_log(101, 0, "33")

    normalized = provider._normalize_log_records((
        third, second, dict(first), first,
    ))
    assert normalized == (first, second, third)

    conflict = dict(first)
    conflict["data"] = "0x00"
    with pytest.raises(ResolutionUnavailable, match="coordinate"):
        provider._normalize_log_records((first, conflict))

    malformed = dict(first)
    malformed["logIndex"] = "0x00"
    with pytest.raises(ResolutionUnavailable, match="log"):
        provider._normalize_log_records((malformed,))


def test_failed_filtered_history_is_unavailable_never_unknown_or_clear():
    policy = ADAPTER_POLICIES[-1]
    question_id = "0x" + "28" * 32
    failed_rpc = _Rpc([], ResolutionUnavailable("range failed"))
    provider = JsonRpcResolutionProvider("archive-a", failed_rpc)
    with pytest.raises(ResolutionUnavailable, match="range failed"):
        provider._read_adapter_history(policy, question_id, 100, 10_100)
    assert len(failed_rpc.calls) == 2

    malformed = JsonRpcResolutionProvider("archive-a", _Rpc(None))
    with pytest.raises(ResolutionUnavailable, match="history"):
        malformed._read_adapter_history(policy, question_id, 100, 100)

    complete = JsonRpcResolutionProvider("archive-a", _Rpc([]))
    assert complete._read_adapter_history(
        policy, question_id, 100, 100
    ) == ()


def test_json_rpc_provider_returns_fully_bound_observation():
    subject = ResolutionSubject(
        "event-1", "0x" + "29" * 32, ("101", "202"), "politics"
    )
    policy = ADAPTER_POLICIES[-1]
    preparation_block = 49_990_000
    resolution_block = 50_000_000
    acceptance_block = 50_000_005
    block_hash = "0x" + "42" * 32
    question_id = "0x" + "43" * 32
    transaction_hash = "0x" + "44" * 32
    rpc = _ObservationRpc(
        subject, policy, preparation_block, resolution_block,
        acceptance_block, block_hash, question_id, transaction_hash,
    )
    provider = JsonRpcResolutionProvider("archive-a", rpc)

    observation = provider.observe(subject, acceptance_block)
    assert observation == ProviderObservation(
        provider_id="archive-a",
        block_number=acceptance_block,
        block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED,
        payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR,
        collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids,
        adapter_address=policy.address,
        question_id=question_id,
        audit_event_ids=(
            f"{preparation_block}:1:" + "0x" + "41" * 32
            + ":CONDITION_PREPARATION",
            f"{resolution_block}:2:{transaction_hash}:CONDITION_RESOLUTION",
            f"{resolution_block}:3:{transaction_hash}:QUESTION_RESOLVED",
        ),
    )
    assert all(method != "eth_blockNumber" for method, _ in rpc.calls)


def test_provider_terminal_verification_uses_stored_block_without_log_rescan(
        monkeypatch):
    subject = ResolutionSubject(
        "event-1", "0x" + "2a" * 32, ("101", "202"), "politics"
    )
    policy = ADAPTER_POLICIES[-1]
    acceptance_block = 50_000_005
    block_hash = "0x" + "45" * 32
    question_id = "0x" + "46" * 32
    transaction_hash = "0x" + "47" * 32
    terminal = TerminalResolution(
        subject=subject,
        payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR,
        block_number=acceptance_block,
        block_hash=block_hash,
        adapter_address=policy.address,
        question_id=question_id,
        audit_event_ids=(
            "49990000:1:" + "0x" + "41" * 32
            + ":CONDITION_PREPARATION",
            f"50000000:2:{transaction_hash}:CONDITION_RESOLUTION",
            f"50000000:3:{transaction_hash}:QUESTION_RESOLVED",
        ),
        provider_ids=("archive-a", "archive-b"),
    )

    def make_rpc(hash_value=block_hash):
        return _ObservationRpc(
            subject, policy, 49_990_000, 50_000_000,
            acceptance_block, hash_value, question_id, transaction_hash,
        )

    rpc = make_rpc()
    provider = JsonRpcResolutionProvider("archive-a", rpc)
    assert provider.verify_terminal(terminal) is None
    assert all(method not in ("eth_getLogs", "eth_blockNumber")
               for method, _ in rpc.calls)
    assert all(
        params[1] == hex(acceptance_block)
        for method, params in rpc.calls if method == "eth_call"
    )

    wrong_hash = JsonRpcResolutionProvider(
        "archive-a", make_rpc("0x" + "48" * 32)
    )
    with pytest.raises(SettlementConflict, match="hash"):
        wrong_hash.verify_terminal(terminal)

    wrong_payout = JsonRpcResolutionProvider("archive-a", make_rpc())
    with pytest.raises(SettlementConflict, match="payout"):
        wrong_payout.verify_terminal(
            replace(terminal, payout=PayoutVector((1, 3), 4))
        )

    wrong_tokens = JsonRpcResolutionProvider("archive-a", make_rpc())
    with pytest.raises(SettlementConflict, match="token"):
        wrong_tokens.verify_terminal(replace(
            terminal,
            subject=replace(subject, token_ids=("303", "404")),
        ))

    bad_code_rpc = make_rpc()
    original_call = bad_code_rpc.call

    def changed_code(method, params):
        if (method == "eth_getCode" and params == [
                policy.address, hex(policy.deployment_block - 1)]):
            return "0x60"
        return original_call(method, params)

    monkeypatch.setattr(bad_code_rpc, "call", changed_code)
    changed_code_provider = JsonRpcResolutionProvider("archive-a", bad_code_rpc)
    with pytest.raises(SettlementConflict, match="deployment"):
        changed_code_provider.verify_terminal(terminal)
