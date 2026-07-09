"""D4a / POL-13 downsampled midpoint-batch persistence."""

import asyncio
import json
import math
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


@pytest.mark.parametrize("schema", [2, True, 1.0])
def test_decode_rejects_unknown_or_non_integer_schema(schema):
    content = json.dumps({"schema": schema, "books": {}})
    with pytest.raises(ValueError, match="schema"):
        decode_midpoint_batch(content)


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


@pytest.mark.parametrize("field", ["bid", "ask", "mid"])
def test_decode_rejects_numeric_json_prices_instead_of_strings(field):
    quote: dict[str, object] = {"bid": "0.60", "ask": "0.62", "mid": "0.61"}
    quote[field] = 0.60
    content = json.dumps({"schema": 1, "books": {"A": quote}})
    with pytest.raises(ValueError, match="string"):
        decode_midpoint_batch(content)


@pytest.mark.parametrize("field", ["bid", "ask", "mid"])
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_decode_rejects_non_finite_prices_in_every_field(field, bad):
    quote = {"bid": "0.60", "ask": "0.62", "mid": "0.61"}
    quote[field] = bad
    content = json.dumps({"schema": 1, "books": {"A": quote}})
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


@pytest.mark.parametrize("token_ids", [
    (), ("A", "A"), ("",), (1,), ("A", ""), ("A", 1),
])
def test_snapshotter_rejects_invalid_token_universe(token_ids):
    with pytest.raises(ValueError, match="token"):
        MidpointSnapshotter(
            token_ids=token_ids,
            book_for=lambda _token: None,
            stamper=FakeStamper(),
            writer=FakeWriter(),
        )


@pytest.mark.parametrize("interval", [0, -1, math.inf, math.nan, True])
def test_snapshotter_rejects_invalid_interval(interval):
    with pytest.raises(ValueError, match="interval"):
        MidpointSnapshotter(
            token_ids=("A",),
            book_for=lambda _token: None,
            stamper=FakeStamper(),
            writer=FakeWriter(),
            interval_seconds=interval,
        )


def test_snapshotter_propagates_book_lookup_failure():
    def raising_book_for(_token):
        raise LookupError("book map corrupt")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for=raising_book_for,
        stamper=FakeStamper(),
        writer=FakeWriter(),
    )
    with pytest.raises(LookupError, match="book map corrupt"):
        snapshotter.snapshot_once()


def test_snapshotter_propagates_later_token_lookup_failure_without_partial_batch():
    writer = FakeWriter()

    def book_for(token):
        if token == "B":
            raise LookupError("later book lookup failed")
        return FakeBook("0.60", "0.62", "0.61")

    snapshotter = MidpointSnapshotter(
        token_ids=("B", "A"),
        book_for=book_for,
        stamper=FakeStamper(),
        writer=writer,
    )
    with pytest.raises(LookupError, match="later book lookup failed"):
        snapshotter.snapshot_once()
    assert writer.rows == []


def test_snapshotter_propagates_writer_failure():
    class RaisingWriter:
        def append(self, _row):
            raise OSError("disk full")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": FakeBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=RaisingWriter(),
    )
    with pytest.raises(OSError, match="disk full"):
        snapshotter.snapshot_once()


def test_snapshotter_stamps_before_reading_any_book():
    events = []

    class OrderingStamper:
        def stamp(self):
            events.append("stamp")
            return 123

    def book_for(_token):
        events.append("book")
        return FakeBook("0.60", "0.62", "0.61")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for=book_for,
        stamper=OrderingStamper(),
        writer=FakeWriter(),
    )
    snapshotter.snapshot_once()

    assert events == ["stamp", "book"]


@pytest.mark.parametrize("accessor", ["midpoint", "best_bid", "best_ask"])
def test_snapshotter_propagates_book_accessor_failure(accessor):
    class RaisingBook(FakeBook):
        def midpoint(self):
            if accessor == "midpoint":
                raise ArithmeticError("midpoint failed")
            return super().midpoint()

        def best_bid(self):
            if accessor == "best_bid":
                raise ArithmeticError("best_bid failed")
            return super().best_bid()

        def best_ask(self):
            if accessor == "best_ask":
                raise ArithmeticError("best_ask failed")
            return super().best_ask()

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": RaisingBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=FakeWriter(),
    )
    with pytest.raises(ArithmeticError, match=f"{accessor} failed"):
        snapshotter.snapshot_once()


def test_snapshotter_propagates_price_encoding_failure():
    class Unencodable:
        def __str__(self):
            raise UnicodeError("cannot encode price")

    class BadBook:
        def midpoint(self):
            return Decimal("0.61")

        def best_bid(self):
            return Unencodable()

        def best_ask(self):
            return Decimal("0.62")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": BadBook()}.get,
        stamper=FakeStamper(),
        writer=FakeWriter(),
    )
    with pytest.raises(UnicodeError, match="cannot encode price"):
        snapshotter.snapshot_once()


def test_snapshotter_propagates_stamper_failure_before_book_lookup():
    looked_up = []

    class RaisingStamper:
        def stamp(self):
            raise OSError("clock unavailable")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for=lambda token: looked_up.append(token),
        stamper=RaisingStamper(),
        writer=FakeWriter(),
    )
    with pytest.raises(OSError, match="clock unavailable"):
        snapshotter.snapshot_once()
    assert looked_up == []


def test_snapshotter_default_interval_is_60_seconds():
    sleep_calls = []

    class StopAfterFirstSleep(Exception):
        pass

    async def recording_sleep(interval):
        sleep_calls.append(interval)
        raise StopAfterFirstSleep

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for=lambda _token: None,
        stamper=FakeStamper(),
        writer=FakeWriter(),
        sleep=recording_sleep,
    )

    with pytest.raises(StopAfterFirstSleep):
        asyncio.run(snapshotter.run())
    assert sleep_calls == [60.0]


def test_run_sleeps_before_each_snapshot_and_cancels_cleanly():
    writer = FakeWriter()
    first_interval = asyncio.Event()
    second_interval = asyncio.Event()
    second_sleep_started = asyncio.Event()
    third_sleep_started = asyncio.Event()
    never = asyncio.Event()
    sleep_calls = []

    async def controlled_sleep(interval):
        sleep_calls.append(interval)
        if len(sleep_calls) == 1:
            await first_interval.wait()
        elif len(sleep_calls) == 2:
            second_sleep_started.set()
            await second_interval.wait()
        else:
            third_sleep_started.set()
            await never.wait()

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": FakeBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=writer,
        interval_seconds=15.0,
        sleep=controlled_sleep,
    )

    async def scenario():
        task = asyncio.create_task(snapshotter.run())
        await asyncio.sleep(0)
        assert sleep_calls == [15.0]
        assert writer.rows == []

        first_interval.set()
        await asyncio.wait_for(second_sleep_started.wait(), timeout=1)
        assert sleep_calls == [15.0, 15.0]
        assert len(writer.rows) == 1

        second_interval.set()
        await asyncio.wait_for(third_sleep_started.wait(), timeout=1)
        assert sleep_calls == [15.0, 15.0, 15.0]
        assert len(writer.rows) == 2

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(writer.rows) == 2

    asyncio.run(scenario())


def test_run_propagates_snapshot_failure_after_sleep():
    sleep_calls = []

    async def immediate_sleep(interval):
        sleep_calls.append(interval)

    class RaisingWriter:
        def append(self, _row):
            raise OSError("snapshot write failed")

    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": FakeBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=RaisingWriter(),
        interval_seconds=15.0,
        sleep=immediate_sleep,
    )

    with pytest.raises(OSError, match="snapshot write failed"):
        asyncio.run(snapshotter.run())
    assert sleep_calls == [15.0]


def test_run_propagates_writer_failure_after_a_successful_cycle():
    class ContinuedAfterWriterFailure(BaseException):
        pass

    class SecondAppendFails:
        def __init__(self):
            self.rows = []

        def append(self, row):
            if self.rows:
                raise OSError("second snapshot write failed")
            self.rows.append(row)

    sleep_calls = []

    async def bounded_sleep(interval):
        sleep_calls.append(interval)
        await asyncio.sleep(0)
        if len(sleep_calls) == 3:
            raise ContinuedAfterWriterFailure("run continued after writer failure")

    writer = SecondAppendFails()
    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": FakeBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=writer,
        sleep=bounded_sleep,
    )

    with pytest.raises(OSError, match="second snapshot write failed"):
        asyncio.run(snapshotter.run())
    assert len(writer.rows) == 1
    assert sleep_calls == [60.0, 60.0]


def test_run_propagates_sleep_failure_after_a_successful_cycle():
    class ContinuedAfterSleepFailure(BaseException):
        pass

    sleep_calls = []

    async def second_sleep_fails(interval):
        sleep_calls.append(interval)
        await asyncio.sleep(0)
        if len(sleep_calls) == 2:
            raise RuntimeError("second cadence sleep failed")
        if len(sleep_calls) == 3:
            raise ContinuedAfterSleepFailure("run continued after sleep failure")

    writer = FakeWriter()
    snapshotter = MidpointSnapshotter(
        token_ids=("A",),
        book_for={"A": FakeBook("0.60", "0.62", "0.61")}.get,
        stamper=FakeStamper(),
        writer=writer,
        interval_seconds=15.0,
        sleep=second_sleep_fails,
    )

    with pytest.raises(RuntimeError, match="second cadence sleep failed"):
        asyncio.run(snapshotter.run())
    assert sleep_calls == [15.0, 15.0]
    assert len(writer.rows) == 1
