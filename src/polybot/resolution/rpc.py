"""Strict JSON-RPC boundary for POL-15 Polygon authority reads."""

import httpx
import re

from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.models import CTF_ADDRESS, PUSD_ADDRESS


_QUANTITY = re.compile(r"0x(?:0|[1-9a-f][0-9a-f]*)\Z")
_UINT256_MAX = 2**256 - 1


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

    def _outcome_slot_count(self, condition_id, block_number):
        data = "0xd42dc0c2" + _bytes32_word(condition_id)
        return int.from_bytes(self._ctf_static_word(data, block_number))

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
