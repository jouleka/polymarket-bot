# POL-17 websocket-silence resnapshot design

## 1. Incident

The production paper runtime entered sticky `HALTED` with `l5_ws_down` after a websocket
shard had no real bookable market frame for more than the hashed 30-second L5 limit.  The
connection can remain alive and answer the client keepalive while a quiet shard sends no
market update.  The current collector therefore has no way to refresh its book generation
before the controller reaches the halt boundary.

This is an operational false positive only when the transport is responsive.  A transport
that does not answer, a reconnect that fails, or a replacement subscription that does not
produce real book snapshots must retain the existing fail-closed halt.

## 2. Decision

`MarketSocket` will proactively abandon and reconnect a responsive but market-silent
connection when an exact bare `PONG` arrives and the shard has seen no real market frame for
20 seconds.  Reconnect is the venue-proven resnapshot path: the new connection subscribes
again and the venue sends replacement `book` snapshots.

The 20-second deadline is deliberately below the immutable 30-second
`RiskCaps.ws_staleness_halt_seconds` limit.  It is not a new trading cap and does not change
the hashed risk envelope.

## 3. Pinned contract

`MarketSocket.__init__` gains two optional keyword-only seams:

```python
market_silence_resnapshot_seconds=20.0
clock_ns=time.monotonic_ns
```

Both values are validated at construction.  On each exact bare `PONG`, the socket compares
`clock_ns()` with the current connection's last real-frame stamp, or with its connection stamp
if no market frame has arrived.  At age `>= market_silence_resnapshot_seconds`, it:

1. marks the shard's books stale before any await;
2. closes the current transport;
3. applies the existing floor reconnect delay;
4. opens a new connection and sends the normal subscription;
5. requires real venue snapshots to restore book authority.

The silence refresh is not a divergence-resync attempt and cannot accrue or clear the
irreconcilable-divergence counter.  A normal JSON market frame remains the only operation that
advances `MarketStream.last_frame_at()`.

## 4. Safety invariants

- `PONG` never reaches `MarketStream.ingest` and never stamps market-data health.
- `PONG` never marks a book fresh and cannot grant proposal or execution authority.
- books are stale before reconnect teardown yields to sibling tasks;
- the L5 30-second comparison, sticky halt, restart reconciliation, live-book re-fetch, exact
  Decimal handling, PaperSigner boundary, and all proposal/signing surfaces remain unchanged;
- failed or snapshot-less reconnects still reach `l5_ws_down` and halt closed;
- production keeps one collector and persists no raw websocket frames.

## 5. Acceptance

- A responsive quiet socket resubscribes before L5, and the replacement snapshot restores the
  book.
- A pre-threshold `PONG` is ignored without reconnect and without changing `last_frame_at()`.
- The threshold boundary is `>=`, so a scheduler tick exactly at 20 seconds cannot miss the
  safety margin.
- The focused tests, complete canonical suite, and an isolated mutation of every comparison and
  authority boundary pass.

