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


def book_fidelity_state(book):
    """Point-in-time observable state of a book for the replay-fidelity comparison.

    Returns None when there is no book. ``stale`` is carried alongside the levels +
    midpoint because staleness is SOCKET-driven (a disconnect / mid-stream-gap resync
    marks the live book stale) and is NOT derivable from the persisted data alone — so
    the comparator below treats a stale LIVE book specially.
    """
    if book is None:
        return None
    return {
        "bid": book.best_bid(),
        "ask": book.best_ask(),
        "mid": book.midpoint(),
        "stale": book.is_stale(),
    }


def fidelity_matches(live, replay):
    """Whether a data-only REPLAY book state is consistent with the LIVE state at a
    point in time. Argument order matters: ``live`` is the reference.

    Top-of-book (bid/ask) must ALWAYS match — it is fully data-derivable, so a
    look-ahead bug or dropped/leaked frame changes it and is caught. The midpoint is
    compared ONLY when the live book is not stale: a stale live book has midpoint=None
    for a reason (a socket disconnect/resync) that is not a persisted row, so the
    data-only replay cannot and should not reproduce it (the known live-reconnect
    asymmetry). When the live book IS fresh, the midpoint is fully checked — including
    catching a replay that went stale from a real data gap the live stream did not have
    — so the gate keeps its teeth.
    """
    if live is None or replay is None:
        return live is None and replay is None
    if live["bid"] != replay["bid"] or live["ask"] != replay["ask"]:
        return False
    if live["stale"]:
        return True  # midpoint divergence is socket-driven; bid/ask already matched
    return live["mid"] == replay["mid"]


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
