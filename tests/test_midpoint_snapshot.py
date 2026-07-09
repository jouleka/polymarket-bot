"""D4a / POL-13 downsampled midpoint-batch persistence."""

import json
from decimal import Decimal

import pytest

from polybot.ingestion.midpoint import (
    MIDPOINT_SCHEMA,
    MIDPOINT_SOURCE,
    MidpointQuote,
    MidpointSnapshotter,
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


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_decode_rejects_non_finite_prices(bad):
    content = json.dumps({
        "schema": 1,
        "books": {"A": {"bid": bad, "ask": "0.62", "mid": "0.61"}},
    })
    with pytest.raises(ValueError, match="finite"):
        decode_midpoint_batch(content)


@pytest.mark.parametrize("bid,ask", [
    ("-0.01", "0.50"),
    ("0.50", "1.01"),
    ("0.60", "0.60"),
    ("0.70", "0.60"),
])
def test_decode_rejects_out_of_domain_locked_or_crossed_books(bid, ask):
    midpoint = str((Decimal(bid) + Decimal(ask)) / 2)
    content = json.dumps({
        "schema": 1,
        "books": {"A": {"bid": bid, "ask": ask, "mid": midpoint}},
    })
    with pytest.raises(ValueError, match="domain"):
        decode_midpoint_batch(content)


def test_decode_rejects_midpoint_not_equal_to_exact_bid_ask_average():
    content = json.dumps({
        "schema": 1,
        "books": {"A": {"bid": "0.60", "ask": "0.62", "mid": "0.60"}},
    })
    with pytest.raises(ValueError, match="midpoint"):
        decode_midpoint_batch(content)


class FakeBook:
    def __init__(self, bid, ask, midpoint):
        self._bid = Decimal(bid)
        self._ask = Decimal(ask)
        self._midpoint = Decimal(midpoint)

    def best_bid(self):
        return self._bid

    def best_ask(self):
        return self._ask

    def midpoint(self):
        return self._midpoint


class FakeStamper:
    def __init__(self):
        self.calls = 0

    def stamp(self):
        self.calls += 1
        return 123


class FakeWriter:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


def test_snapshot_two_fresh_books_into_one_deterministic_envelope():
    books = {
        "A": FakeBook("0.60", "0.62", "0.61"),
        "B": FakeBook("0.30", "0.34", "0.32"),
    }
    stamper = FakeStamper()
    writer = FakeWriter()
    snapshotter = MidpointSnapshotter(
        token_ids=("B", "A"),
        book_for=books.get,
        stamper=stamper,
        writer=writer,
    )

    assert snapshotter.snapshot_once() == 2
    assert stamper.calls == 1
    assert len(writer.rows) == 1
    row = writer.rows[0]
    assert row.source == "clob-midpoint"
    assert row.source_tier == "VENUE"
    assert row.event_id == "batch:123"
    assert row.observed_at == 123
    assert row.published_at is None
    assert row.market_links == ("A", "B")
    assert row.content == (
        '{"books":{"A":{"ask":"0.62","bid":"0.60","mid":"0.61"},'
        '"B":{"ask":"0.34","bid":"0.30","mid":"0.32"}},"schema":1}'
    )
    assert decode_midpoint_batch(row.content) == {
        "A": MidpointQuote(Decimal("0.60"), Decimal("0.62"), Decimal("0.61")),
        "B": MidpointQuote(Decimal("0.30"), Decimal("0.34"), Decimal("0.32")),
    }


class NoneMidBook:
    def midpoint(self):
        return None

    def best_bid(self):
        raise AssertionError("unusable book bid must not be read")

    def best_ask(self):
        raise AssertionError("unusable book ask must not be read")


def test_snapshot_omits_missing_and_unusable_books():
    books = {
        "A": FakeBook("0.60", "0.62", "0.61"),
        "B": NoneMidBook(),
    }
    writer = FakeWriter()
    snapshotter = MidpointSnapshotter(
        token_ids=("C", "B", "A"),
        book_for=books.get,
        stamper=FakeStamper(),
        writer=writer,
    )

    assert snapshotter.snapshot_once() == 1
    assert writer.rows[0].market_links == ("A",)
    assert set(decode_midpoint_batch(writer.rows[0].content)) == {"A"}


def test_snapshot_writes_valid_empty_batch_when_all_books_unusable():
    writer = FakeWriter()
    snapshotter = MidpointSnapshotter(
        token_ids=("B", "A"),
        book_for={"A": NoneMidBook()}.get,
        stamper=FakeStamper(),
        writer=writer,
    )

    assert snapshotter.snapshot_once() == 0
    assert len(writer.rows) == 1
    assert writer.rows[0].content == '{"books":{},"schema":1}'
    assert writer.rows[0].market_links == ()
    assert decode_midpoint_batch(writer.rows[0].content) == {}
