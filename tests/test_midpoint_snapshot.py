"""D4a / POL-13 downsampled midpoint-batch persistence."""

import json
from decimal import Decimal

import pytest

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


def test_decode_rejects_unknown_schema():
    with pytest.raises(ValueError, match="schema"):
        decode_midpoint_batch('{"schema":2,"books":{}}')


def test_decode_rejects_missing_or_extra_top_level_keys():
    for content in ('{"books":{}}', '{"schema":1,"books":{},"extra":1}'):
        with pytest.raises(ValueError, match="schema|keys"):
            decode_midpoint_batch(content)


def test_decode_rejects_non_object_books():
    with pytest.raises(ValueError, match="books"):
        decode_midpoint_batch('{"schema":1,"books":[]}')


def test_decode_rejects_empty_token_id():
    content = json.dumps({
        "schema": 1,
        "books": {"": {"bid": "0.60", "ask": "0.62", "mid": "0.61"}},
    })
    with pytest.raises(ValueError, match="token"):
        decode_midpoint_batch(content)


def test_decode_rejects_missing_or_extra_quote_keys():
    for quote in (
        {"bid": "0.60", "ask": "0.62"},
        {"bid": "0.60", "ask": "0.62", "mid": "0.61", "size": "100"},
    ):
        content = json.dumps({"schema": 1, "books": {"A": quote}})
        with pytest.raises(ValueError, match="quote|keys"):
            decode_midpoint_batch(content)


def test_decode_rejects_numeric_json_prices_instead_of_strings():
    content = json.dumps({
        "schema": 1,
        "books": {"A": {"bid": 0.60, "ask": "0.62", "mid": "0.61"}},
    })
    with pytest.raises(ValueError, match="string"):
        decode_midpoint_batch(content)
