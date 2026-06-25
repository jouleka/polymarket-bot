"""Live replay-fidelity / no-look-ahead acceptance check (POL-3 / S1 gate).

Drives the tested ingestion core against the REAL public Polymarket venue
(read-only, no auth, no orders), persisting every WS frame to a Market-Memory
EventStore. While capturing, it records the live book's top-of-book at periodic
checkpoints (each tagged with the observed_at reached). Afterwards it asserts:

  1. FIDELITY + NO LOOK-AHEAD: for each checkpoint, a book reconstructed from the
     store using ONLY rows with observed_at <= that checkpoint equals the live
     point-in-time state captured at that moment (so a later frame cannot leak in).
  2. FULL-REPLAY fidelity: replaying the whole store reproduces the final live book.
  3. MONOTONIC observed_at: every stored record has a strictly-increasing stamp.
  4. RESTART persistence: reopening the DB file and replaying yields the same books.

The comparison is STALENESS-AWARE (replay.fidelity_matches): top-of-book (bid/ask) is
always asserted, but the midpoint is compared only when the LIVE book is not stale. A
live book goes stale via a socket disconnect / mid-stream-gap resync — events that are
not persisted rows — so the data-only replay cannot reproduce that staleness; comparing
its (fresh) midpoint against the live (None) midpoint was a false mismatch on
reconnect-prone (e.g. extreme-price) markets. bid/ask are still checked in that case, so
a look-ahead / data-loss bug is still caught.

Not a unit test (network-dependent). Run manually:
    ./.venv/bin/python scripts/replay_fidelity_check.py
    ./.venv/bin/python scripts/replay_fidelity_check.py --forced-resync   # inject one
        mid-capture disconnect to exercise the reconnect+resync path live and assert
        fidelity (incl. no-look-ahead) holds across it.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.gamma import normalize_market
from polybot.ingestion.market_socket import MarketSocket
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.replay import book_fidelity_state, fidelity_matches, reconstruct_from_store
from polybot.ingestion.transport import GAMMA_URL, WS_RECONNECT_ON, open_market_ws
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore

WS_SECONDS = 35
CHECKPOINT_EVERY = 40  # observations between point-in-time snapshots
FORCED_DISCONNECT_AFTER = 50  # --forced-resync: frames before the injected mid-capture drop


class _DisconnectAfter:
    """Wrap a live transport so it raises ``exc`` after yielding ``after`` frames —
    forcing exactly one mid-capture disconnect so the socket reconnects + resubscribes
    (== resync). Used by --forced-resync to exercise the resync-during-capture path
    live (otherwise it only happens when a market happens to drop)."""

    def __init__(self, transport, after, exc):
        self._t = transport
        self._after = after
        self._exc = exc

    async def send(self, message):
        await self._t.send(message)

    async def __aiter__(self):
        n = 0
        async for frame in self._t:
            yield frame
            n += 1
            if n >= self._after:
                raise self._exc

    def close(self):
        close = getattr(self._t, "close", None)
        return close() if close is not None else None


def forced_resync_connect(base_connect, after_frames, state):
    """A connect() whose FIRST connection drops after ``after_frames`` frames (forcing
    one reconnect+resync); later connections pass through untouched. ``state`` records
    the connection count so the caller can confirm a resync actually happened."""

    async def connect():
        transport = await base_connect()
        state["connections"] += 1
        if state["connections"] == 1:
            return _DisconnectAfter(transport, after_frames, OSError("forced resync (injected)"))
        return transport

    return connect


def pick_markets(n=3):
    resp = httpx.get(f"{GAMMA_URL}/markets",
                     params={"limit": 150, "closed": "false", "active": "true",
                             "order": "volume24hr", "ascending": "false"}, timeout=20)
    resp.raise_for_status()
    out = []
    for raw in resp.json():
        if not all(k in raw for k in ("clobTokenIds", "outcomes", "outcomePrices")):
            continue
        try:
            m = normalize_market(raw)
        except Exception:
            continue
        if len(m.outcomes) == 2 and raw.get("acceptingOrders"):
            out.append(m)
        if len(out) >= n:
            break
    return out


def _fmt(s):
    """Render a fidelity state dict (book_fidelity_state) compactly for failure output."""
    if s is None:
        return "none"
    mid = s["mid"] if s["mid"] is not None else "-"
    return f"bid={s['bid']} ask={s['ask']} mid={mid}{' STALE' if s['stale'] else ''}"


class CapturingSink:
    """Persist each Observation, and every CHECKPOINT_EVERY observations record the
    live point-in-time state (book_fidelity_state, incl. the staleness flag) for all
    tracked assets, tagged with the observed_at reached."""

    def __init__(self, persist, stream, asset_ids, every=CHECKPOINT_EVERY):
        self._persist = persist
        self._stream = stream
        self._asset_ids = asset_ids
        self._every = every
        self._n = 0
        self.checkpoints = []  # list[(observed_at, {asset_id: fidelity_state})]

    def __call__(self, observation):
        self._persist(observation)
        self._n += 1
        if self._n % self._every == 0:
            snapshot = {a: book_fidelity_state(self._stream.book_for(a)) for a in self._asset_ids
                        if self._stream.book_for(a) is not None}
            self.checkpoints.append((observation.observed_at, snapshot))


async def capture(token_ids, db_path, connect=open_market_ws):
    stamper = MonotonicStamper()
    # Persist through the off-loop single-writer (POL-12): the acceptance gate now
    # exercises the production write path, proving replay fidelity + no-look-ahead
    # still hold with the per-frame commit moved off the event loop. The store is
    # opened check_same_thread=False so the writer thread can drive its connection.
    writer = QueuedEventWriter(EventStore(db_path, check_same_thread=False))
    stream = MarketStream(stamper, asset_ids=token_ids)
    sink = CapturingSink(PersistingSink(writer), stream, token_ids)
    stream._sink = sink  # wire the capturing sink (stream built first so the sink can read its books)
    socket = MarketSocket(connect, stream, asset_ids=token_ids, reconnect_on=WS_RECONNECT_ON)
    try:
        await asyncio.wait_for(socket.run(max_connections=None), timeout=WS_SECONDS)
    except asyncio.TimeoutError:
        pass
    final_live = {a: book_fidelity_state(stream.book_for(a))
                  for a in token_ids if stream.book_for(a) is not None}
    writer.close()  # drain the off-loop queue + close the store before any reconstruction reads
    return sink.checkpoints, final_live


def main():
    forced = "--forced-resync" in sys.argv
    markets = pick_markets(3)
    token_ids = [o.token_id for m in markets for o in m.outcomes]
    print(f"capturing {len(token_ids)} assets ({len(markets)} markets) for {WS_SECONDS}s ...")
    db_path = tempfile.mktemp(suffix=".db")

    conn_state = {"connections": 0}
    if forced:
        connect = forced_resync_connect(open_market_ws, FORCED_DISCONNECT_AFTER, conn_state)
        print(f"FORCED-RESYNC mode: injecting one disconnect after {FORCED_DISCONNECT_AFTER} "
              "frames to exercise the resync-during-capture path")
    else:
        connect = open_market_ws
    checkpoints, final_live = asyncio.run(capture(token_ids, db_path, connect))

    failures = []
    relaxed = 0  # comparisons where the LIVE book was stale -> midpoint relaxed (bid/ask still checked)

    # 1 + 2: point-in-time fidelity / no look-ahead at each checkpoint, then full replay.
    # Comparison is staleness-aware (fidelity_matches): a stale LIVE book has midpoint
    # None for a socket reason (a disconnect/resync) that is NOT a persisted row, so the
    # data-only replay cannot reproduce it -- bid/ask are still asserted, the midpoint
    # is relaxed only in that case (keeps the gate's teeth; see replay.fidelity_matches).
    store = EventStore(db_path)
    rows = store.all()
    print(f"captured {len(rows)} store rows, {len(checkpoints)} checkpoints")

    for observed_at, live_snapshot in checkpoints:
        replayed = reconstruct_from_store(store, until=observed_at)
        for asset_id, live in live_snapshot.items():
            got = book_fidelity_state(replayed.book_for(asset_id))
            if not fidelity_matches(live, got):
                failures.append(f"checkpoint oa={observed_at} asset={asset_id[:12]}.. "
                                f"live=[{_fmt(live)}] replay=[{_fmt(got)}]")
            elif live is not None and live["stale"]:
                relaxed += 1

    full = reconstruct_from_store(store)
    for asset_id, live in final_live.items():
        got = book_fidelity_state(full.book_for(asset_id))
        if not fidelity_matches(live, got):
            failures.append(f"FULL asset={asset_id[:12]}.. live=[{_fmt(live)}] replay=[{_fmt(got)}]")
        elif live is not None and live["stale"]:
            relaxed += 1

    # 3: monotonic observed_at across every stored record.
    stamps = [e.observed_at for e in rows]
    monotonic = all(b > a for a, b in zip(stamps, stamps[1:]))
    if not monotonic:
        failures.append("observed_at not strictly increasing across store rows")

    # 4: restart persistence -- reopen the same DB file and replay.
    store.close()
    reopened = EventStore(db_path)
    restart_ok = True
    full_after_restart = reconstruct_from_store(reopened)
    for asset_id, live in final_live.items():
        if not fidelity_matches(live, book_fidelity_state(full_after_restart.book_for(asset_id))):
            restart_ok = False
            failures.append(f"RESTART mismatch asset={asset_id[:12]}..")
    reopened.close()

    # forced-resync: confirm a reconnect actually happened, else the path wasn't exercised.
    if forced:
        if conn_state["connections"] >= 2:
            print(f"forced-resync: {conn_state['connections']} connections -> a reconnect+resync was "
                  "exercised mid-capture (fidelity asserted across it above)")
        else:
            failures.append(f"forced-resync requested but only {conn_state['connections']} connection(s) "
                            "-- no resync was exercised (inconclusive)")

    checked = sum(len(s) for _, s in checkpoints) + len(final_live)
    print(f"\nfidelity points checked: {checked} (checkpoints + full); "
          f"{relaxed} relaxed (live book stale via reconnect -> midpoint not compared, bid/ask verified)")
    if checked and relaxed / checked > 0.5:
        # bid/ask are still verified everywhere, so this is not a failure -- but an
        # unusually high relaxed fraction can mean a book is PERSISTENTLY stale (not just
        # transient reconnects), which is worth eyeballing rather than passing silently.
        print(f"WARNING: {relaxed}/{checked} comparisons were midpoint-relaxed -- unusually high; "
              "check for a persistently-stale book, not just transient reconnects")
    print(f"monotonic observed_at: {monotonic}   restart persistence: {restart_ok}")
    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} mismatches)")
        for f in failures[:20]:
            print("  -", f)
        sys.exit(1)
    print("\nRESULT: PASS - every reconstruction matched the live point-in-time state "
          "(fidelity + no look-ahead), observed_at strictly monotonic, DB survives restart.")


if __name__ == "__main__":
    main()
