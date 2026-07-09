"""D4a / POL-13 downsampled midpoint-batch persistence."""

import json
from decimal import Decimal

from polybot.ingestion.midpoint import (
    MIDPOINT_SCHEMA,
    MIDPOINT_SOURCE,
    MidpointQuote,
    decode_midpoint_batch,
)


def test_decode_midpoint_batch_returns_exact_quotes():
    content = json.dumps({
        "schema": 1,
        "books": {
            "B": {"bid": "0.30", "ask": "0.34", "mid": "0.32"},
            "A": {"bid": "0.60", "ask": "0.62", "mid": "0.61"},
        },
    })

    assert MIDPOINT_SOURCE == "clob-midpoint"
    assert MIDPOINT_SCHEMA == 1
    assert decode_midpoint_batch(content) == {
        "A": MidpointQuote(Decimal("0.60"), Decimal("0.62"), Decimal("0.61")),
        "B": MidpointQuote(Decimal("0.30"), Decimal("0.34"), Decimal("0.32")),
    }
