"""Strict JSON-RPC boundary for POL-15 Polygon authority reads."""

from dataclasses import dataclass
import httpx
import re

from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.models import (
    CTF_ADDRESS,
    DisputeState,
    LifecyclePhase,
    PUSD_ADDRESS,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
    fold_dispute,
)


_QUANTITY = re.compile(r"0x(?:0|[1-9a-f][0-9a-f]*)\Z")
_UINT256_MAX = 2**256 - 1
CTF_DEPLOYMENT_BLOCK = 4_023_686
CONDITION_PREPARATION_TOPIC = (
    "0xab3760c3bd2bb38b5bcf54dc79802ed67338b4cf29f3054ded67ed24661e4177"
)
CONDITION_RESOLUTION_TOPIC = (
    "0xb44d84d3289691f71497564b85d4233648d9dbae8cbdbb4329f301c3a0185894"
)
QUESTION_RESET_TOPIC = (
    "0x7981b5832932948db4e32a4a16a0f44b2ce7ff088574afb9364b313f70f82e8f"
)
QUESTION_FLAGGED_ADMIN_V1_TOPIC = (
    "0xd96b8927b38f8cc48e678eeb45ee1c3a281d2ba49078ed4a5c00895d251e573b"
)
QUESTION_UPDATED_V1_TOPIC = (
    "0x32da4770ea275a14ae9d822d58709fe7bfb296969d46357149ed02fb4135a17b"
)
QUESTION_RESOLVED_V1_TOPIC = (
    "0x5c3937ed929cd157b73b417381d743daf6e1ef65999e3ccb5dd64bc3247e28d6"
)
QUESTION_FLAGGED_V2_TOPIC = (
    "0x2435a0347185933b12027c6f394a5fd9c03646dba233e956f50658719dfc0b35"
)
QUESTION_RESOLVED_V2_TOPIC = (
    "0x566c3fbdd12dd86bb341787f6d531f79fd7ad4ce7e3ae2d15ac0ca1b601af9df"
)
QUESTION_EMERGENCY_RESOLVED_V2_TOPIC = (
    "0x6edb5841a476c9c29c34a652d1a44f785fe71a6157a3da9a6a6a589a1bd2945a"
)


@dataclass(frozen=True)
class AdapterPolicy:
    policy_id: str
    address: str
    deployment_block: int
    generation: str


ADAPTER_POLICIES = (
    AdapterPolicy(
        "UMA_V1_0_1", "0xb97455fcf78eb37375e8be6f26df895341ca073d",
        29_838_630, "v1",
    ),
    AdapterPolicy(
        "UMA_V2_0_0", "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74",
        34_876_144, "v2_plus",
    ),
    AdapterPolicy(
        "UMA_V3_0_0", "0x71392e133063cc0d16f40e1f9b60227404bc03f7",
        43_375_847, "v2_plus",
    ),
    AdapterPolicy(
        "UMA_V3_1_0", "0x157ce2d672854c848c9b79c49a8cc6cc89176a49",
        46_755_254, "v2_plus",
    ),
)


@dataclass(frozen=True)
class CtfAuthority:
    adapter_address: str
    question_id: str
    policy: AdapterPolicy | None
    audit_event_ids: tuple[str, str]
    resolution_block: int
    resolution_log_index: int
    resolution_transaction_hash: str


_ADAPTER_EVENT_KINDS = frozenset({
    "QUESTION_UPDATED",
    "QUESTION_RESOLVED",
    "QUESTION_RESET",
    "QUESTION_FLAGGED_FOR_ADMIN_RESOLUTION",
    "QUESTION_FLAGGED",
    "QUESTION_UNFLAGGED",
    "QUESTION_EMERGENCY_RESOLVED",
})


@dataclass(frozen=True)
class AdapterEvent:
    kind: str
    question_id: str
    block_number: int
    log_index: int
    transaction_hash: str
    manual: bool = False

    def __post_init__(self):
        if self.kind not in _ADAPTER_EVENT_KINDS:
            raise ValueError("adapter event kind is unsupported")
        decode_fixed_bytes(self.question_id, 32)
        if (isinstance(self.block_number, bool)
                or not isinstance(self.block_number, int)
                or self.block_number < 0
                or isinstance(self.log_index, bool)
                or not isinstance(self.log_index, int)
                or self.log_index < 0):
            raise ValueError("adapter event coordinate is invalid")
        decode_fixed_bytes(self.transaction_hash, 32)
        if not isinstance(self.manual, bool):
            raise TypeError("adapter event manual flag must be bool")
        if self.manual and self.kind != "QUESTION_RESOLVED":
            raise ValueError("manual bool is only valid on v1 resolution")

    @property
    def audit_event_id(self):
        return (
            f"{self.block_number}:{self.log_index}:"
            f"{self.transaction_hash}:{self.kind}"
        )


@dataclass(frozen=True)
class PathProof:
    dispute: DisputeState
    audit_event_ids: tuple[str, ...]
    terminal_event: AdapterEvent | None


def decode_quantity(value):
    if (not isinstance(value, str) or _QUANTITY.fullmatch(value) is None):
        raise ResolutionUnavailable("JSON-RPC quantity is not canonical")
    decoded = int(value[2:], 16)
    if decoded > _UINT256_MAX:
        raise ResolutionUnavailable("JSON-RPC quantity exceeds uint256")
    return decoded


def decode_fixed_bytes(value, width):
    if isinstance(width, bool) or not isinstance(width, int):
        raise TypeError("fixed byte width must be an integer")
    if width <= 0:
        raise ValueError("fixed byte width must be positive")
    if (not isinstance(value, str) or len(value) != 2 + width * 2
            or not value.startswith("0x")
            or re.fullmatch(r"[0-9a-f]+", value[2:]) is None):
        raise ResolutionUnavailable(
            f"JSON-RPC value is not canonical bytes{width}"
        )
    return bytes.fromhex(value[2:])


def _decode_hex_data(value):
    if (not isinstance(value, str) or not value.startswith("0x")
            or len(value[2:]) % 2 != 0
            or (value[2:] and re.fullmatch(r"[0-9a-f]+", value[2:]) is None)):
        raise ResolutionUnavailable("JSON-RPC byte data is not canonical")
    return bytes.fromhex(value[2:])


def _encode_quantity(value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= _UINT256_MAX):
        raise ValueError("JSON-RPC quantity value must be a non-negative uint256")
    return hex(value)


def _bytes32_word(value):
    return decode_fixed_bytes(value, 32).hex()


def _uint256_word(value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= _UINT256_MAX):
        raise ValueError("ABI uint256 value is invalid")
    return f"{value:064x}"


class JsonRpcClient:
    def __init__(self, endpoint: str, client: httpx.Client | None = None):
        if (not isinstance(endpoint, str) or not endpoint
                or endpoint != endpoint.strip()):
            raise ValueError("JSON-RPC endpoint must be a non-empty exact string")
        if client is not None and not callable(getattr(client, "post", None)):
            raise TypeError("JSON-RPC client must provide post")
        self._endpoint = endpoint
        self._client = client if client is not None else httpx.Client()
        self._next_id = 1

    def call(self, method: str, params: list[object]) -> object:
        if (not isinstance(method, str) or not method
                or method != method.strip()):
            raise ValueError("JSON-RPC method must be a non-empty exact string")
        if not isinstance(params, list):
            raise TypeError("JSON-RPC params must be a list")

        request_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            response = self._client.post(self._endpoint, json=request)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ResolutionUnavailable("JSON-RPC request unavailable") from exc

        if not isinstance(payload, dict):
            raise ResolutionUnavailable("JSON-RPC response must be an object")
        if payload.get("jsonrpc") != "2.0":
            raise ResolutionUnavailable("JSON-RPC response version is invalid")
        response_id = payload.get("id")
        if (isinstance(response_id, bool) or not isinstance(response_id, int)
                or response_id != request_id):
            raise ResolutionUnavailable("JSON-RPC response ID does not match request")

        if set(payload) == {"jsonrpc", "id", "result"}:
            return payload["result"]
        if set(payload) != {"jsonrpc", "id", "error"}:
            raise ResolutionUnavailable("JSON-RPC response envelope is malformed")
        error = payload["error"]
        if (not isinstance(error, dict)
                or set(error) not in (
                    {"code", "message"}, {"code", "message", "data"})
                or isinstance(error.get("code"), bool)
                or not isinstance(error.get("code"), int)
                or not isinstance(error.get("message"), str)
                or not error["message"]):
            raise ResolutionUnavailable("JSON-RPC error envelope is malformed")
        raise ResolutionUnavailable(f"JSON-RPC error: {error['message']}")


class JsonRpcResolutionProvider:
    def __init__(self, provider_id: str, rpc: JsonRpcClient):
        if (not isinstance(provider_id, str) or not provider_id
                or provider_id != provider_id.strip()):
            raise ValueError("provider_id must be a non-empty exact string")
        if not callable(getattr(rpc, "call", None)):
            raise TypeError("rpc must provide call")
        self.provider_id = provider_id
        self._rpc = rpc
        self._verified_deployments = set()

    def chain_id(self):
        return decode_quantity(self._rpc.call("eth_chainId", []))

    def latest_block(self):
        return decode_quantity(self._rpc.call("eth_blockNumber", []))

    def block_hash(self, block_number):
        block_tag = _encode_quantity(block_number)
        block = self._rpc.call("eth_getBlockByNumber", [block_tag, False])
        if (not isinstance(block, dict)
                or decode_quantity(block.get("number")) != block_number):
            raise ResolutionUnavailable("JSON-RPC block coordinate is malformed")
        return "0x" + decode_fixed_bytes(block.get("hash"), 32).hex()

    def observe(self, subject, block_number):
        if not isinstance(subject, ResolutionSubject):
            raise TypeError("observation subject must be a ResolutionSubject")
        block_hash = self.block_hash(block_number)
        phase, payout = self._read_payout(subject.condition_id, block_number)
        if phase is LifecyclePhase.UNRESOLVED:
            return ProviderObservation(
                provider_id=self.provider_id,
                block_number=block_number,
                block_hash=block_hash,
                phase=phase,
                payout=None,
                dispute=DisputeState.UNKNOWN,
                collateral_address=None,
                derived_token_ids=None,
                adapter_address=None,
                question_id=None,
                audit_event_ids=(),
            )

        derived_token_ids = self._derive_positions(subject, block_number)
        preparation_block, resolution_block = self._transition_blocks(
            subject.condition_id, block_number
        )
        authority = self._ctf_authority(
            subject.condition_id, preparation_block, resolution_block, payout
        )
        if authority.policy is None:
            proof = PathProof(
                DisputeState.UNKNOWN, (), None
            )
        else:
            self._verify_deployments(authority.adapter_address)
            raw_history = self._read_adapter_history(
                authority.policy, authority.question_id,
                preparation_block, resolution_block,
            )
            events = self._decode_adapter_history(
                authority.policy, raw_history, payout
            )
            proof = (
                self._normalize_v1(authority.question_id, events)
                if authority.policy.generation == "v1"
                else self._normalize_v2_plus(authority.question_id, events)
            )
            if proof.terminal_event is not None:
                terminal = proof.terminal_event
                self._link_adapter_terminal(
                    authority, terminal.question_id, terminal.block_number,
                    terminal.log_index, terminal.transaction_hash,
                )
        audit_event_ids = tuple(sorted(
            authority.audit_event_ids + proof.audit_event_ids,
            key=lambda value: (
                int(value.split(":", 2)[0]),
                int(value.split(":", 2)[1]),
            ),
        ))
        return ProviderObservation(
            provider_id=self.provider_id,
            block_number=block_number,
            block_hash=block_hash,
            phase=phase,
            payout=payout,
            dispute=proof.dispute,
            collateral_address=PUSD_ADDRESS,
            derived_token_ids=derived_token_ids,
            adapter_address=authority.adapter_address,
            question_id=authority.question_id,
            audit_event_ids=audit_event_ids,
        )

    def _verify_deployments(self, adapter_address):
        policy = next(
            (candidate for candidate in ADAPTER_POLICIES
             if candidate.address == adapter_address),
            None,
        )
        if policy is None:
            raise ResolutionUnavailable("adapter is not in the frozen authority registry")
        self._verify_code_transition(CTF_ADDRESS, CTF_DEPLOYMENT_BLOCK)
        self._verify_code_transition(policy.address, policy.deployment_block)

    def _verify_code_transition(self, address, deployment_block):
        if address in self._verified_deployments:
            return
        try:
            before = _decode_hex_data(self._rpc.call(
                "eth_getCode", [address, _encode_quantity(deployment_block - 1)]
            ))
            deployed = _decode_hex_data(self._rpc.call(
                "eth_getCode", [address, _encode_quantity(deployment_block)]
            ))
        except ResolutionUnavailable as exc:
            raise ResolutionUnavailable(
                "frozen contract deployment evidence is unavailable"
            ) from exc
        if before or not deployed:
            raise ResolutionUnavailable(
                "frozen contract deployment transition does not match"
            )
        self._verified_deployments.add(address)

    def _outcome_slot_count(self, condition_id, block_number):
        data = "0xd42dc0c2" + _bytes32_word(condition_id)
        return int.from_bytes(self._ctf_static_word(data, block_number))

    def _read_payout(self, condition_id, block_number):
        slot_count = self._outcome_slot_count(condition_id, block_number)
        if slot_count != 2:
            raise ResolutionUnavailable("CTF payout is not binary")
        denominator = self._payout_denominator(condition_id, block_number)
        if denominator == 0:
            return LifecyclePhase.UNRESOLVED, None
        numerators = tuple(
            self._payout_numerator(condition_id, slot, block_number)
            for slot in (0, 1)
        )
        try:
            payout = PayoutVector(numerators, denominator)
        except (TypeError, ValueError) as exc:
            raise ResolutionUnavailable("CTF payout vector is malformed") from exc
        return LifecyclePhase.FINALIZED, payout

    def _transition_blocks(self, condition_id, acceptance_block):
        if (isinstance(acceptance_block, bool)
                or not isinstance(acceptance_block, int)
                or acceptance_block < CTF_DEPLOYMENT_BLOCK):
            raise ResolutionUnavailable(
                "acceptance block precedes the frozen CTF deployment"
            )
        preparation = self._first_positive_block(
            lambda block: self._outcome_slot_count(condition_id, block),
            acceptance_block,
            "preparation",
        )
        resolution = self._first_positive_block(
            lambda block: self._payout_denominator(condition_id, block),
            acceptance_block,
            "resolution",
        )
        if preparation > resolution:
            raise ResolutionUnavailable(
                "CTF preparation/resolution transition order is invalid"
            )
        return preparation, resolution

    def _ctf_authority(self, condition_id, preparation_block, resolution_block,
                       payout):
        if not isinstance(payout, PayoutVector):
            raise TypeError("CTF authority payout must be a PayoutVector")
        preparation = self._single_ctf_log(
            CONDITION_PREPARATION_TOPIC, condition_id, preparation_block
        )
        resolution = self._single_ctf_log(
            CONDITION_RESOLUTION_TOPIC, condition_id, resolution_block
        )
        prep_adapter, prep_question, prep_index, prep_tx, prep_data = preparation
        res_adapter, res_question, res_index, res_tx, res_data = resolution
        if len(prep_data) != 32 or int.from_bytes(prep_data) != 2:
            raise ResolutionUnavailable("CTF preparation event is not binary")
        if len(res_data) != 160:
            raise ResolutionUnavailable("CTF resolution event ABI is malformed")
        words = tuple(
            int.from_bytes(res_data[offset:offset + 32])
            for offset in range(0, len(res_data), 32)
        )
        if (words[:3] != (2, 64, 2)
                or words[3:] != payout.numerators):
            raise ResolutionUnavailable("CTF resolution event payout disagrees")
        if prep_adapter != res_adapter or prep_question != res_question:
            raise ResolutionUnavailable("CTF event authority linkage disagrees")
        policy = next(
            (candidate for candidate in ADAPTER_POLICIES
             if candidate.address == prep_adapter),
            None,
        )
        return CtfAuthority(
            adapter_address=prep_adapter,
            question_id=prep_question,
            policy=policy,
            audit_event_ids=(
                f"{preparation_block}:{prep_index}:{prep_tx}:CONDITION_PREPARATION",
                f"{resolution_block}:{res_index}:{res_tx}:CONDITION_RESOLUTION",
            ),
            resolution_block=resolution_block,
            resolution_log_index=res_index,
            resolution_transaction_hash=res_tx,
        )

    def _normalize_v1(self, question_id, events):
        decode_fixed_bytes(question_id, 32)
        relevant = self._validate_adapter_events(events, question_id)
        terminal_events = tuple(
            event for event in relevant if event.kind == "QUESTION_RESOLVED"
        )
        if len(terminal_events) > 1:
            raise ResolutionUnavailable("v1 adapter terminal events conflict")
        terminal = terminal_events[0] if terminal_events else None
        states = [DisputeState.UNKNOWN]
        if (terminal is not None
                and any(event.kind == "QUESTION_RESET" for event in relevant)):
            states.append(DisputeState.DISPUTED)
        if terminal is not None and (
                any(event.kind == "QUESTION_FLAGGED_FOR_ADMIN_RESOLUTION"
                    for event in relevant)
                or terminal.manual):
            states.append(DisputeState.MANUAL)
        return PathProof(
            dispute=fold_dispute(tuple(states)),
            audit_event_ids=tuple(event.audit_event_id for event in relevant),
            terminal_event=terminal,
        )

    def _normalize_v2_plus(self, question_id, events):
        decode_fixed_bytes(question_id, 32)
        relevant = self._validate_adapter_events(events, question_id)
        terminal_events = tuple(
            event for event in relevant
            if event.kind in (
                "QUESTION_RESOLVED", "QUESTION_EMERGENCY_RESOLVED"
            )
        )
        if len(terminal_events) > 1:
            raise ResolutionUnavailable("v2+ adapter terminal events conflict")
        terminal = terminal_events[0] if terminal_events else None
        states = [
            DisputeState.CLEAR if terminal is not None else DisputeState.UNKNOWN
        ]
        if (terminal is not None
                and any(event.kind == "QUESTION_RESET" for event in relevant)):
            states.append(DisputeState.DISPUTED)
        if terminal is not None and (
                any(event.kind == "QUESTION_FLAGGED" for event in relevant)
                or terminal.kind == "QUESTION_EMERGENCY_RESOLVED"):
            states.append(DisputeState.MANUAL)
        return PathProof(
            dispute=fold_dispute(tuple(states)),
            audit_event_ids=tuple(event.audit_event_id for event in relevant),
            terminal_event=terminal,
        )

    def _adapter_history_filters(self, policy, question_id, preparation_block,
                                 resolution_block):
        if policy not in ADAPTER_POLICIES:
            raise ResolutionUnavailable("adapter history policy is unsupported")
        decode_fixed_bytes(question_id, 32)
        if (isinstance(preparation_block, bool)
                or not isinstance(preparation_block, int)
                or isinstance(resolution_block, bool)
                or not isinstance(resolution_block, int)
                or preparation_block < 0
                or resolution_block < preparation_block):
            raise ResolutionUnavailable("adapter history interval is invalid")
        filters = []
        start = preparation_block
        while start <= resolution_block:
            end = min(start + 9_999, resolution_block)
            base = {
                "address": policy.address,
                "fromBlock": _encode_quantity(start),
                "toBlock": _encode_quantity(end),
            }
            if policy.generation == "v1":
                filters.append({**base, "topics": [[
                    QUESTION_RESET_TOPIC,
                    QUESTION_UPDATED_V1_TOPIC,
                    QUESTION_RESOLVED_V1_TOPIC,
                ], question_id]})
                filters.append({
                    **base, "topics": [QUESTION_FLAGGED_ADMIN_V1_TOPIC]
                })
            else:
                filters.append({**base, "topics": [[
                    QUESTION_RESET_TOPIC,
                    QUESTION_FLAGGED_V2_TOPIC,
                    QUESTION_RESOLVED_V2_TOPIC,
                    QUESTION_EMERGENCY_RESOLVED_V2_TOPIC,
                ], question_id]})
            start = end + 1
        return tuple(filters)

    def _read_adapter_history(self, policy, question_id, preparation_block,
                              resolution_block):
        logs = []
        for event_filter in self._adapter_history_filters(
                policy, question_id, preparation_block, resolution_block):
            page = self._rpc.call("eth_getLogs", [event_filter])
            if not isinstance(page, list):
                raise ResolutionUnavailable(
                    "filtered adapter history response is malformed"
                )
            logs.extend(page)
        normalized = self._normalize_log_records(tuple(logs))
        for log in normalized:
            block_number = decode_quantity(log["blockNumber"])
            if (log["address"] != policy.address
                    or not preparation_block <= block_number <= resolution_block):
                raise ResolutionUnavailable(
                    "adapter history log is outside requested authority"
                )
        return normalized

    def _decode_adapter_history(self, policy, logs, payout):
        if policy not in ADAPTER_POLICIES:
            raise ResolutionUnavailable("adapter decode policy is unsupported")
        if not isinstance(payout, PayoutVector):
            raise TypeError("adapter payout must be a PayoutVector")
        events = []
        for log in logs:
            topics = log["topics"]
            topic = topics[0]
            data = _decode_hex_data(log["data"])
            manual = False
            if topic == QUESTION_FLAGGED_ADMIN_V1_TOPIC:
                if policy.generation != "v1" or len(topics) != 1 or len(data) != 32:
                    raise ResolutionUnavailable("v1 manual flag ABI is malformed")
                question_id = "0x" + data.hex()
                kind = "QUESTION_FLAGGED_FOR_ADMIN_RESOLUTION"
            else:
                if len(topics) != 2:
                    raise ResolutionUnavailable("adapter indexed event ABI is malformed")
                question_id = "0x" + decode_fixed_bytes(topics[1], 32).hex()
                if topic == QUESTION_RESET_TOPIC:
                    if data:
                        raise ResolutionUnavailable("adapter reset ABI is malformed")
                    kind = "QUESTION_RESET"
                elif topic == QUESTION_UPDATED_V1_TOPIC:
                    if policy.generation != "v1":
                        raise ResolutionUnavailable("adapter update policy is invalid")
                    self._validate_v1_update_data(data)
                    kind = "QUESTION_UPDATED"
                elif topic == QUESTION_RESOLVED_V1_TOPIC:
                    if policy.generation != "v1" or len(data) != 32:
                        raise ResolutionUnavailable("v1 resolution ABI is malformed")
                    manual_value = int.from_bytes(data)
                    if manual_value not in (0, 1):
                        raise ResolutionUnavailable("v1 resolution bool is malformed")
                    manual = bool(manual_value)
                    kind = "QUESTION_RESOLVED"
                elif topic == QUESTION_FLAGGED_V2_TOPIC:
                    if policy.generation != "v2_plus" or data:
                        raise ResolutionUnavailable("v2+ flag ABI is malformed")
                    kind = "QUESTION_FLAGGED"
                elif topic == QUESTION_RESOLVED_V2_TOPIC:
                    if policy.generation != "v2_plus":
                        raise ResolutionUnavailable("v2+ resolution policy is invalid")
                    self._validate_v2_payout_data(data, payout, emergency=False)
                    kind = "QUESTION_RESOLVED"
                elif topic == QUESTION_EMERGENCY_RESOLVED_V2_TOPIC:
                    if policy.generation != "v2_plus":
                        raise ResolutionUnavailable("v2+ emergency policy is invalid")
                    self._validate_v2_payout_data(data, payout, emergency=True)
                    kind = "QUESTION_EMERGENCY_RESOLVED"
                else:
                    raise ResolutionUnavailable("adapter event topic is unsupported")
            events.append(AdapterEvent(
                kind=kind,
                question_id=question_id,
                block_number=decode_quantity(log["blockNumber"]),
                log_index=decode_quantity(log["logIndex"]),
                transaction_hash="0x" + decode_fixed_bytes(
                    log["transactionHash"], 32
                ).hex(),
                manual=manual,
            ))
        return tuple(events)

    @staticmethod
    def _validate_v1_update_data(data):
        if len(data) < 224 or len(data) % 32 != 0:
            raise ResolutionUnavailable("v1 update ABI is malformed")
        words = tuple(
            data[offset:offset + 32] for offset in range(0, 192, 32)
        )
        if (int.from_bytes(words[0]) != 192
                or words[2][:12] != bytes(12)
                or int.from_bytes(words[5]) not in (0, 1)):
            raise ResolutionUnavailable("v1 update ABI is malformed")
        value_length = int.from_bytes(data[192:224])
        padded_length = ((value_length + 31) // 32) * 32
        if (len(data) != 224 + padded_length
                or any(data[224 + value_length:])):
            raise ResolutionUnavailable("v1 update bytes are malformed")

    @staticmethod
    def _validate_v2_payout_data(data, payout, *, emergency):
        expected_length = 128 if emergency else 160
        if len(data) != expected_length:
            raise ResolutionUnavailable("v2+ resolution ABI is malformed")
        words = tuple(
            int.from_bytes(data[offset:offset + 32])
            for offset in range(0, len(data), 32)
        )
        control = words[:2] if emergency else words[1:3]
        numerators = words[2:] if emergency else words[3:]
        expected_control = (32, 2) if emergency else (64, 2)
        if control != expected_control or numerators != payout.numerators:
            raise ResolutionUnavailable("adapter resolution payout disagrees")

    @staticmethod
    def _normalize_log_records(logs):
        if not isinstance(logs, tuple):
            raise TypeError("adapter logs must be a tuple")
        by_coordinate = {}
        ordered = []
        for log in logs:
            try:
                if (not isinstance(log, dict) or log.get("removed") is not False):
                    raise ResolutionUnavailable("invalid adapter log object")
                decode_fixed_bytes(log.get("address"), 20)
                block_number = decode_quantity(log.get("blockNumber"))
                log_index = decode_quantity(log.get("logIndex"))
                transaction_hash = "0x" + decode_fixed_bytes(
                    log.get("transactionHash"), 32
                ).hex()
                topics = log.get("topics")
                if not isinstance(topics, list) or not topics:
                    raise ResolutionUnavailable("invalid adapter log topics")
                for topic in topics:
                    decode_fixed_bytes(topic, 32)
                _decode_hex_data(log.get("data"))
            except ResolutionUnavailable as exc:
                raise ResolutionUnavailable("adapter log is malformed") from exc
            coordinate = (block_number, log_index)
            prior = by_coordinate.get(coordinate)
            if prior is not None:
                if prior != log:
                    raise ResolutionUnavailable(
                        "different adapter logs claim one chain coordinate"
                    )
                continue
            by_coordinate[coordinate] = log
            ordered.append((block_number, log_index, transaction_hash, log))
        ordered.sort(key=lambda item: item[:3])
        return tuple(item[3] for item in ordered)

    @staticmethod
    def _validate_adapter_events(events, question_id):
        if not isinstance(events, tuple):
            raise TypeError("adapter events must be a tuple")
        if any(not isinstance(event, AdapterEvent) for event in events):
            raise TypeError("adapter history must contain AdapterEvent values")
        positions = tuple(
            (event.block_number, event.log_index, event.transaction_hash)
            for event in events
        )
        if positions != tuple(sorted(positions)) or len(set(positions)) != len(positions):
            raise ResolutionUnavailable("adapter events are not in unique chain order")
        return tuple(event for event in events if event.question_id == question_id)

    def _single_ctf_log(self, topic, condition_id, block_number):
        _bytes32_word(condition_id)
        result = self._rpc.call("eth_getLogs", [{
            "address": CTF_ADDRESS,
            "fromBlock": _encode_quantity(block_number),
            "toBlock": _encode_quantity(block_number),
            "topics": [topic, condition_id],
        }])
        if not isinstance(result, list) or len(result) != 1:
            raise ResolutionUnavailable("CTF event authority is not unique")
        log = result[0]
        if (not isinstance(log, dict) or log.get("address") != CTF_ADDRESS
                or log.get("removed") is not False
                or decode_quantity(log.get("blockNumber")) != block_number):
            raise ResolutionUnavailable("CTF event coordinate is malformed")
        transaction_hash = "0x" + decode_fixed_bytes(
            log.get("transactionHash"), 32
        ).hex()
        log_index = decode_quantity(log.get("logIndex"))
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) != 4:
            raise ResolutionUnavailable("CTF event topics are malformed")
        if topics[0] != topic or topics[1] != condition_id:
            raise ResolutionUnavailable("CTF event condition does not match")
        address_word = decode_fixed_bytes(topics[2], 32)
        if address_word[:12] != bytes(12):
            raise ResolutionUnavailable("CTF event adapter topic is malformed")
        adapter_address = "0x" + address_word[12:].hex()
        question_id = "0x" + decode_fixed_bytes(topics[3], 32).hex()
        data = _decode_hex_data(log.get("data"))
        return (
            adapter_address, question_id, log_index, transaction_hash, data
        )

    @staticmethod
    def _link_adapter_terminal(authority, question_id, block_number, log_index,
                               transaction_hash):
        if not isinstance(authority, CtfAuthority):
            raise TypeError("authority must be CtfAuthority")
        try:
            canonical_question = "0x" + decode_fixed_bytes(question_id, 32).hex()
            canonical_tx = "0x" + decode_fixed_bytes(transaction_hash, 32).hex()
        except ResolutionUnavailable as exc:
            raise ResolutionUnavailable("adapter terminal linkage is malformed") from exc
        if (isinstance(block_number, bool) or not isinstance(block_number, int)
                or isinstance(log_index, bool) or not isinstance(log_index, int)
                or canonical_question != authority.question_id
                or block_number != authority.resolution_block
                or canonical_tx != authority.resolution_transaction_hash
                or log_index <= authority.resolution_log_index):
            raise ResolutionUnavailable("adapter terminal linkage does not match")

    @staticmethod
    def _first_positive_block(reader, upper_block, label):
        if reader(upper_block) <= 0:
            raise ResolutionUnavailable(f"CTF {label} transition is unavailable")
        lower = CTF_DEPLOYMENT_BLOCK
        upper = upper_block
        while lower < upper:
            middle = (lower + upper) // 2
            if reader(middle) > 0:
                upper = middle
            else:
                lower = middle + 1
        return lower

    def _payout_denominator(self, condition_id, block_number):
        data = "0xdd34de67" + _bytes32_word(condition_id)
        return int.from_bytes(self._ctf_static_word(data, block_number))

    def _payout_numerator(self, condition_id, slot, block_number):
        data = (
            "0x0504c814" + _bytes32_word(condition_id) + _uint256_word(slot)
        )
        return int.from_bytes(self._ctf_static_word(data, block_number))

    def _collection_id(self, condition_id, index_set, block_number):
        data = (
            "0x856296f7" + "00" * 32 + _bytes32_word(condition_id)
            + _uint256_word(index_set)
        )
        return "0x" + self._ctf_static_word(data, block_number).hex()

    def _derive_positions(self, subject, block_number):
        if not isinstance(subject, ResolutionSubject):
            raise TypeError("position subject must be a ResolutionSubject")
        derived = tuple(
            str(self._position_id(
                self._collection_id(subject.condition_id, index_set, block_number),
                block_number,
            ))
            for index_set in (1, 2)
        )
        if derived != subject.token_ids:
            raise ResolutionUnavailable(
                "chain-derived pUSD token order does not match subject"
            )
        return derived

    def _position_id(self, collection_id, block_number):
        collateral_word = "00" * 12 + decode_fixed_bytes(PUSD_ADDRESS, 20).hex()
        data = (
            "0x39dd7530" + collateral_word + _bytes32_word(collection_id)
        )
        return int.from_bytes(self._ctf_static_word(data, block_number))

    def _ctf_static_word(self, data, block_number):
        result = self._rpc.call(
            "eth_call",
            [{"to": CTF_ADDRESS, "data": data}, _encode_quantity(block_number)],
        )
        return decode_fixed_bytes(result, 32)
