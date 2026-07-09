"""Compact point-in-time midpoint batches for downsampled market memory."""

from dataclasses import dataclass
from decimal import Decimal
import json


MIDPOINT_SOURCE = "clob-midpoint"
MIDPOINT_SCHEMA = 1


@dataclass(frozen=True)
class MidpointQuote:
    bid: Decimal
    ask: Decimal
    midpoint: Decimal


def decode_midpoint_batch(content: str) -> dict[str, MidpointQuote]:
    payload = json.loads(content)
    return {
        token: MidpointQuote(
            Decimal(quote["bid"]),
            Decimal(quote["ask"]),
            Decimal(quote["mid"]),
        )
        for token, quote in sorted(payload["books"].items())
    }
