import asyncio
import json
from pathlib import Path

import pytest

from polybot.core.models import Envelope
from polybot.ingestion.midpoint import MIDPOINT_SCHEMA, MIDPOINT_SOURCE
from polybot.storage.market_memory import EventStore
from scripts.downsample_endurance_check import (
    footprint,
    inspect_capture,
    parse_args,
    projected_gib_per_day,
    run_runtime_for,
)


GIB = 1024 ** 3
SECONDS_PER_DAY = 86400


def test_projected_gib_per_day_exact_known_rate():
    assert projected_gib_per_day(GIB, SECONDS_PER_DAY) == 1.0
    assert projected_gib_per_day(GIB // 2, SECONDS_PER_DAY * 2) == 0.25


@pytest.mark.parametrize("elapsed_seconds", [0, -1.0])
def test_projected_gib_per_day_rejects_nonpositive_elapsed(elapsed_seconds):
    with pytest.raises(ValueError, match="elapsed_seconds"):
        projected_gib_per_day(1, elapsed_seconds)


@pytest.mark.parametrize("elapsed_seconds", [float("nan"), float("inf"), float("-inf")])
def test_projected_gib_per_day_rejects_nonfinite_elapsed(elapsed_seconds):
    with pytest.raises(ValueError, match="elapsed_seconds"):
        projected_gib_per_day(1, elapsed_seconds)


@pytest.mark.parametrize("total_bytes", [-1, float("nan"), float("inf"), float("-inf")])
def test_projected_gib_per_day_rejects_invalid_total_bytes(total_bytes):
    with pytest.raises(ValueError, match="total_bytes"):
        projected_gib_per_day(total_bytes, 1)


def test_footprint_includes_db_and_wal_and_ignores_missing_shm(tmp_path):
    db = tmp_path / "capture.db"
    wal = Path(f"{db}-wal")
    shm = Path(f"{db}-shm")
    db.write_bytes(b"db!")
    wal.write_bytes(b"wal!!")

    assert footprint([db, wal, shm]) == 8


def _env(source, event_id, observed_at, content, market_links=()):
    return Envelope(
        source=source,
        source_tier="VENUE",
        event_id=event_id,
        observed_at=observed_at,
        content=content,
        market_links=market_links,
    )


def _midpoint_content(books=None):
    if books is None:
        books = {"A": {"bid": "0.50", "ask": "0.52", "mid": "0.51"}}
    return json.dumps(
        {"schema": MIDPOINT_SCHEMA, "books": books},
        sort_keys=True,
        separators=(",", ":"),
    )


def test_inspect_capture_accepts_midpoints_trades_no_raw_and_bounded_rate(tmp_path):
    with EventStore(str(tmp_path / "capture.db")) as store:
        store.append(_env(MIDPOINT_SOURCE, "mid:1", 1, _midpoint_content(), ("A",)))
        store.append(_env("data-api", "/trades:t1", 2, '{"id":"t1"}'))

        result = inspect_capture(store, projected_rate=0.25, max_gib_per_day=0.5)

    assert result.passed is True
    assert result.failures == ()
    assert result.source_counts == {MIDPOINT_SOURCE: 1, "data-api": 1}
    assert result.midpoint_batches == 1
    assert result.usable_quotes == 1
    assert result.projected_gib_per_day == 0.25


def test_inspect_capture_rejects_any_raw_clob_row(tmp_path):
    with EventStore(str(tmp_path / "capture.db")) as store:
        store.append(_env(MIDPOINT_SOURCE, "mid:1", 1, _midpoint_content(), ("A",)))
        store.append(_env("data-api", "/trades:t1", 2, '{"id":"t1"}'))
        store.append(_env("clob-ws", "raw:1", 3, '{"event_type":"book"}', ("A",)))

        result = inspect_capture(store, projected_rate=0.25, max_gib_per_day=0.5)

    assert result.passed is False
    assert any("raw clob-ws" in failure for failure in result.failures)


def test_inspect_capture_rejects_malformed_midpoint_batch(tmp_path):
    with EventStore(str(tmp_path / "capture.db")) as store:
        store.append(_env(MIDPOINT_SOURCE, "mid:bad", 1, "not-json", ("A",)))
        store.append(_env("data-api", "/trades:t1", 2, '{"id":"t1"}'))

        result = inspect_capture(store, projected_rate=0.25, max_gib_per_day=0.5)

    assert result.passed is False
    assert any("malformed midpoint" in failure for failure in result.failures)


def test_inspect_capture_rejects_rate_above_ceiling(tmp_path):
    with EventStore(str(tmp_path / "capture.db")) as store:
        store.append(_env(MIDPOINT_SOURCE, "mid:1", 1, _midpoint_content(), ("A",)))
        store.append(_env("data-api", "/trades:t1", 2, '{"id":"t1"}'))

        result = inspect_capture(store, projected_rate=0.5001, max_gib_per_day=0.5)

    assert result.passed is False
    assert any("projected rate" in failure for failure in result.failures)


@pytest.mark.parametrize(("projected_rate", "ceiling", "reason"), [
    (float("nan"), 0.5, "invalid projected rate"),
    (float("inf"), 0.5, "invalid projected rate"),
    (float("-inf"), 0.5, "invalid projected rate"),
    (-0.01, 0.5, "invalid projected rate"),
    (0.25, float("nan"), "invalid rate ceiling"),
    (0.25, float("inf"), "invalid rate ceiling"),
    (0.25, float("-inf"), "invalid rate ceiling"),
    (0.25, 0.0, "invalid rate ceiling"),
])
def test_inspect_capture_fails_closed_on_invalid_rate_inputs(
        tmp_path, projected_rate, ceiling, reason):
    with EventStore(str(tmp_path / "capture.db")) as store:
        store.append(_env(MIDPOINT_SOURCE, "mid:1", 1, _midpoint_content(), ("A",)))
        store.append(_env("data-api", "/trades:t1", 2, '{"id":"t1"}'))

        result = inspect_capture(
            store,
            projected_rate=projected_rate,
            max_gib_per_day=ceiling,
        )

    assert result.passed is False
    assert any(reason in failure for failure in result.failures)


def test_inspect_capture_requires_midpoint_trade_and_usable_quote(tmp_path):
    with EventStore(str(tmp_path / "capture.db")) as store:
        empty = inspect_capture(store, projected_rate=0.0, max_gib_per_day=0.5)
        store.append(_env(MIDPOINT_SOURCE, "mid:empty", 1, _midpoint_content({})))
        no_trade_or_quote = inspect_capture(store, projected_rate=0.0, max_gib_per_day=0.5)

    assert any("no midpoint" in failure for failure in empty.failures)
    assert any("no data-api" in failure for failure in empty.failures)
    assert any("no data-api" in failure for failure in no_trade_or_quote.failures)
    assert any("no usable" in failure for failure in no_trade_or_quote.failures)


def test_parse_args_has_release_gate_defaults():
    args = parse_args([])
    assert args.seconds == 1800.0
    assert args.max_gib_per_day == 0.5
    assert args.universe_max_markets == 200
    assert args.keep_db is False


def test_parse_args_accepts_smoke_overrides():
    args = parse_args([
        "--seconds", "70",
        "--max-gib-per-day", "0.25",
        "--universe-max-markets", "5",
        "--keep-db",
    ])
    assert args.seconds == 70.0
    assert args.max_gib_per_day == 0.25
    assert args.universe_max_markets == 5
    assert args.keep_db is True


@pytest.mark.parametrize("argv", [
    ["--seconds", "nan"],
    ["--seconds", "inf"],
    ["--max-gib-per-day", "nan"],
    ["--max-gib-per-day", "inf"],
])
def test_parse_args_rejects_nonfinite_safety_bounds(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_run_runtime_for_requests_graceful_stop_after_window():
    class FakeRuntime:
        def __init__(self):
            self.stop = asyncio.Event()
            self.stop_calls = 0

        async def run(self):
            await self.stop.wait()

        def request_stop(self):
            self.stop_calls += 1
            self.stop.set()

    runtime = FakeRuntime()
    clock_values = iter([10.0, 80.0])

    async def complete_window(_seconds):
        await asyncio.sleep(0)

    elapsed = asyncio.run(run_runtime_for(
        runtime,
        70.0,
        sleep=complete_window,
        clock=lambda: next(clock_values),
    ))

    assert elapsed == 70.0
    assert runtime.stop_calls == 1


def test_run_runtime_for_propagates_early_halt():
    class HaltingRuntime:
        async def run(self):
            raise RuntimeError("collector HALT")

        def request_stop(self):
            raise AssertionError("already-halted runtime should not need a stop request")

    async def never_complete(_seconds):
        await asyncio.Event().wait()

    with pytest.raises(RuntimeError, match="collector HALT"):
        asyncio.run(run_runtime_for(
            HaltingRuntime(),
            70.0,
            sleep=never_complete,
        ))
