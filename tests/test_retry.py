"""Tests for bounded, selective async retry (POL-3 / S1 hardening).

The 24/7 Polygon log watcher must ride out TRANSIENT RPC failures (a dropped
connection, a timeout, a 429/5xx from a free public RPC) without dying, yet still
FAIL LOUD on a non-transient/contract error and after a bounded retry budget. These
pin: success path (no retry), retry-then-succeed with growing+capped backoff, give up
after the budget, never retry a non-retryable error, and arg/result passthrough.
"""

import asyncio

import pytest

from polybot.ingestion.retry import with_retry


class Transient(Exception):
    pass


class Fatal(Exception):
    pass


def _recording_sleep():
    delays = []

    async def sleep(d):
        delays.append(d)

    return sleep, delays


def test_returns_result_without_retrying_on_success():
    sleep, delays = _recording_sleep()
    calls = []

    async def fetch(method, params):
        calls.append((method, params))
        return f"ok:{method}"

    retrying = with_retry(fetch, is_retryable=lambda e: True, sleep=sleep)

    assert asyncio.run(retrying("eth_blockNumber", [])) == "ok:eth_blockNumber"
    assert calls == [("eth_blockNumber", [])]  # called once, args forwarded
    assert delays == []  # no sleep on the happy path


def test_retries_a_retryable_error_then_succeeds():
    sleep, delays = _recording_sleep()
    n = {"c": 0}

    async def fetch():
        n["c"] += 1
        if n["c"] < 3:
            raise Transient("blip")
        return "done"

    retrying = with_retry(fetch, is_retryable=lambda e: isinstance(e, Transient),
                          retries=4, backoff_base=0.5, sleep=sleep)

    assert asyncio.run(retrying()) == "done"
    assert n["c"] == 3            # 2 failures + 1 success
    assert delays == [0.5, 1.0]  # one backoff per retry, growing


def test_reraises_after_exhausting_the_retry_budget():
    sleep, _ = _recording_sleep()
    n = {"c": 0}

    async def fetch():
        n["c"] += 1
        raise Transient(f"fail{n['c']}")

    retrying = with_retry(fetch, is_retryable=lambda e: True, retries=2, sleep=sleep)

    with pytest.raises(Transient):
        asyncio.run(retrying())
    assert n["c"] == 3  # 1 initial + 2 retries, then fail loud


def test_does_not_retry_a_non_retryable_error():
    sleep, delays = _recording_sleep()
    n = {"c": 0}

    async def fetch():
        n["c"] += 1
        raise Fatal("contract / JSON-RPC error")

    retrying = with_retry(fetch, is_retryable=lambda e: isinstance(e, Transient),
                          retries=5, sleep=sleep)

    with pytest.raises(Fatal):
        asyncio.run(retrying())
    assert n["c"] == 1   # not retried -- fail loud immediately
    assert delays == []


def test_backoff_grows_exponentially_and_caps():
    sleep, delays = _recording_sleep()

    async def fetch():
        raise Transient()

    retrying = with_retry(fetch, is_retryable=lambda e: True, retries=5,
                          backoff_base=1.0, backoff_cap=4.0, sleep=sleep)

    with pytest.raises(Transient):
        asyncio.run(retrying())
    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]  # 1,2,4 then capped at the cap


def test_rejects_negative_retries():
    with pytest.raises(ValueError):
        with_retry(lambda: None, is_retryable=lambda e: True, retries=-1)
