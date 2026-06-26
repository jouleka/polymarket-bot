"""Bounded, selective retry for an injected async fetch (POL-3 / S1 hardening).

A 24/7 ingestion loop (esp. the Polygon log watcher) must ride out TRANSIENT RPC
failures -- a dropped connection, a timeout, a 429/5xx from a free public RPC -- without
dying, but must still FAIL LOUD on a non-transient/contract error (a JSON-RPC error, a
4xx, a format change) and once a bounded retry budget is spent. ``with_retry`` wraps a
fetch with bounded exponential backoff that retries ONLY when an injected
``is_retryable(exc)`` predicate says so, and re-raises otherwise -- never an unbounded
silent retry. ``BaseException`` (incl. ``CancelledError``) is never caught.
"""

import asyncio


def with_retry(fetch, *, is_retryable, retries=4, backoff_base=0.5, backoff_cap=30.0,
               sleep=asyncio.sleep):
    if retries < 0:
        raise ValueError("retries must be >= 0")

    async def retrying(*args):
        attempt = 0
        while True:
            try:
                return await fetch(*args)
            except Exception as exc:
                # Fail loud when the budget is spent OR the error is not transient.
                if attempt >= retries or not is_retryable(exc):
                    raise
                await sleep(min(backoff_cap, backoff_base * (2 ** attempt)))
                attempt += 1

    return retrying
