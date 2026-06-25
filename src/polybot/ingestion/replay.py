"""Deterministic replay reconstruction (POL-3 / S1 acceptance gate).

Reconstruct per-asset ``LocalBook``s from an ordered sequence of stored/captured
market frames (a ``book`` snapshot + ``price_change`` deltas) by feeding them
through a fresh ``MarketStream`` in observed_at order. This is the basis of the
replay-fidelity / no-look-ahead acceptance gate: a book reconstructed from data
with ``observed_at <= T`` must equal the book that was live at T, and the
reconstruction must depend ONLY on data <= T. Pure and deterministic -- no
network, no sink, and it never mutates the source store.
"""

import json

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_stream import MarketStream


def reconstruct(messages):
    """Replay an ordered iterable of decoded WS frame dicts through a fresh stream
    and return the ``MarketStream`` (query it via ``book_for``). Frames must be in
    observed_at order; only ``book`` / ``price_change`` events shape the books.
    Determinism comes from the frame sequence alone -- the fresh stamper's values
    are not used in reconstruction.
    """
    stream = MarketStream(MonotonicStamper())
    for message in messages:
        stream.ingest(message)
    return stream


def reconstruct_from_store(store, until=None, source="clob-ws"):
    """Reconstruct books from the Market-Memory store as of an observed_at cutoff.

    ``until=None`` replays the whole store; otherwise only rows with
    ``observed_at <= until`` are replayed -- point-in-time with no look-ahead, the
    cutoff enforced by ``EventStore.replay_until``. Rows from other sources (e.g.
    the Data API ``/trades`` poller) carry a different shape and are skipped, so a
    mixed store reconstructs cleanly.
    """
    envelopes = store.all() if until is None else store.replay_until(until)
    # A corrupt/truncated stored row raises here rather than being silently skipped
    # -- fail-loud matches the project's HALT-on-format-change stance for a store
    # that cannot be backfilled (better to abort the gate than reconstruct partial).
    messages = (json.loads(e.content) for e in envelopes if e.source == source)
    return reconstruct(messages)
