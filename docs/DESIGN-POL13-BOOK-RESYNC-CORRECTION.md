# DESIGN — POL-13 repeated order-book resync correction

## 1. Incident

The enabled paper/shadow runtime restarted six times between 2026-07-16 19:34 UTC and
2026-07-17 02:46 UTC. Every exit was the existing eight-attempt order-book resync HALT. The
registry remained fresh, all economic/outbox tables remained empty, and the service stayed within
its memory caps. The current exception does not identify the shard, asset, venue top, reconstructed
top, market, or venue timestamp, so another identical restart cannot distinguish an asset-local
race from a protocol-wide format change.

## 2. Scope and authority boundary

This correction is part of POL-13. It may change only the in-memory public-market collector,
focused tests, operational evidence, and deployment documentation.

- Keep exactly one websocket collector. A bounded live diagnostic runs only while the production
  units are stopped.
- Persist no raw websocket frames and add no database table.
- Do not alter signer, wallet, proposal, ERS validation, pricing, sizing, controller, resolution,
  or execution authority.
- Do not increase `max_resyncs`, accept an unverified venue top, or make a stale/diverged book
  eligible.
- Unknown event types, malformed fields, lossy numeric fields, and cross-asset/global ambiguity
  still HALT the supervised runtime.
- Existing optional seams retain their backward-compatible defaults.

## 3. Stage A contract — bounded divergence evidence

Add an immutable `BookDivergence` value in `ingestion.market_stream` with exact string/`None`
evidence only:

```python
BookDivergence(
    asset_id: str,
    market: str | None,
    timestamp: str | None,
    reconstructed_bid: str | None,
    reconstructed_ask: str | None,
    venue_bid: str,
    venue_ask: str,
)
```

`MarketStream.consume_resync_detail() -> BookDivergence | None` is paired with the existing
read-and-clear resync request. A divergence records the last offending tracked asset without raw
levels, sizes, sibling entries, or full frames. A clean event cannot erase pending divergence
evidence. `consume_resync_request()` remains exactly boolean for compatibility.

`MarketSocket` consumes the paired detail when it consumes the request. Its terminal resync error
must include the shard's configured asset count and an ordered, escaped history of no more than
eight bounded divergence details. This distinguishes repeated-same from changing-asset storms
without logging raw frames. Clean progress, transport disconnect, or normal-close reconnect clears
the history together with the consecutive counter. Missing detail is itself a fail-loud internal
invariant error. The retry/backoff/HALT threshold is otherwise unchanged.

## 4. Stopped-service live diagnostic

After Stage A tests pass, stop Hermes and then POL-17 cleanly. Run a bounded, non-persisting
collector from the synchronized development checkout against the production discovery/shard
configuration. It acquires the production runtime's nonblocking singleton lock at
`/opt/polymarket-bot/data/shadow-runtime.lock` before discovery and holds it until completion, so a
service/operator race cannot create a duplicate collector. The stopped installer precreates that
regular file as `0640 polybot:polybot`; unlike systemd's `/run/polybot`, its parent persists while
the unit is stopped. The probe must exit on the first terminal resync storm or its time bound.
Preserve databases and raw-firehose evidence.

The captured `BookDivergence` decides Stage B:

- repeated identical asset after fresh snapshots: an asset-local reconstruction/race correction
  may be designed;
- changing assets, malformed values, or cross-shard evidence: preserve whole-process HALT and fix
  the protocol parser/ordering contract;
- no reproduction within the bound and no deterministic invariant defect: deploy Stage A only,
  restart in order, and wait for one production recurrence before any behavior change.

No asset quarantine or parser relaxation may be inferred without this evidence.

## 5. Stage B safety criteria

The Stage B design amendment must name the observed exact failure class and prove:

1. every divergent book is stale before recovery;
2. a fresh snapshot is required before that asset can regain read/ERS authority;
3. a sibling asset cannot clear or inherit another asset's divergence state;
4. global/multi-asset format changes still terminate supervision;
5. the collector subscription identity cannot expand or silently duplicate;
6. no raw-frame persistence is introduced;
7. current restart, registry, resolution, execution-outbox, and no-signing boundaries are unchanged.

## 6. Acceptance

- Intended RED is observed before each implementation.
- Focused collector tests and the canonical suite pass.
- A whole-slice restart/sibling-isolation test covers the observed failure.
- Independent specification/security review passes.
- An isolated mutation battery kills diagnostic loss, wrong-asset attribution, stale-book revival,
  retry-limit weakening, global-HALT weakening, and raw-persistence/authority expansion.
- Deployment preserves config/data, retains existing memory caps, and restarts in dependency order
  only after the reviewed correction lands.

## 7. Stage B amendment — reconnect snapshot-readiness race

Two further production resync HALTs occurred at 2026-07-17 12:51:18 UTC and
13:20:33 UTC. The latter followed a restart by only 29 minutes. The process peaked at
285.7 MB RSS with zero swap, excluding the VPS memory cap as the trigger. The installed
pre-diagnostic build still discarded the offending asset/top details. A state-machine audit found
and reproduced a recovery race that deterministically creates the same consecutive resync storm.
It is a confirmed defect consistent with the incidents, not proof that it caused every historical
restart; the bounded live diagnostic remains the causal observation gate:

1. a gap or exceptional transport disconnect calls `MarketStream.mark_all_stale()` but
   intentionally retains the prior levels for diagnostics; normal close codes previously bypassed
   this stale transition entirely;
2. reconnect subscribes the whole shard and waits for replacement snapshots;
3. a tracked asset's live `price_change` can arrive before that asset's new `book` snapshot;
4. the dispatcher distinguished only `book is None`, so it applied that delta to the retained
   stale levels and compared the mixed-generation book with the venue's current top;
5. the manufactured mismatch forced another reconnect before the replacement snapshot could be
   consumed, repeating until the existing eight-attempt HALT.

This differs from an actual post-snapshot divergence. A stale book explicitly has no read/ERS
authority, so deltas received before its replacement snapshot cannot safely mutate it. The exact
recovery rule is therefore:

- `book is None` and `book.is_stale()` use the same pre-snapshot path;
- all entries first pass atomic semantic validation (finite exact decimals and known sides), so the
  archive-only window cannot suppress a protocol-format HALT;
- a tracked delta is still stamped and archived by the configured sink, preserving the
  no-backfill evidence contract;
- it is not applied, cannot clear staleness, and cannot request another resync;
- an untracked sibling remains skipped as before;
- only a full `book` snapshot replaces levels and clears staleness;
- subsequent deltas are applied and verified exactly as before, and a real post-snapshot mismatch
  still follows the existing backoff and eight-attempt HALT;
- any normally exhausted websocket is marked stale and backed off before a production reconnect;
  every abandoned generation, including format HALT and cancellation, is marked stale synchronously
  before keepalive/socket teardown can yield; bounded one-shot runs retain their existing terminal
  semantics.

The correction changes no collector identity, websocket schema, retry limit, raw persistence,
database, execution/controller seam, signer boundary, or proposal authority. A socket-level
regression covers gap -> reconnect -> delta-before-snapshot -> snapshot -> clean delta and fails
under the pre-fix stale-baseline behavior. A combined two-asset regression additionally proves
sibling preservation and atomic HALT on a malformed multi-asset frame during recovery.

## 8. Stage C amendment — side-aware empty-book boundaries

The landed diagnostics then attributed the next live halt exactly. At 2026-07-17 14:17:04 UTC,
all eight attempts named asset
`87799961432065897081457579217720144183820894061679127498797994042052915780390`
in market `0xc2367f6c81c524809d55f9b6b1e681b7c6ee6e782ccd197ed426a20d20b365a5`.
Every attempt reconstructed `0.999`/no ask while the venue reported `0.999`/`1`. A direct REST
book read confirmed 41 bids with best bid `0.999` and zero asks. This proves that the websocket's
`best_ask="1"` is an empty-ask boundary sentinel, not a missing executable level.

Polymarket order prices remain strictly inside `(0, 1)`. Top verification therefore applies this
minimal, side-aware rule:

- bid `0` denotes an empty bid; bid `1` is not accepted as empty;
- ask `1` denotes an empty ask; ask `0` remains accepted for compatibility with prior frames;
- any in-domain price is parsed as an exact `Decimal` and must match the reconstructed top;
- a boundary sentinel matches only an actually empty reconstructed side. It cannot hide a real
  level, and every mismatch still marks the book stale and enters the existing resync path.

A one-sided book may now remain structurally fresh, but it is not execution authority:
`midpoint()` remains `None`, so readiness, ERS validation, shadow filling, Hermes book views, and
midpoint persistence continue to reject or skip it. The amendment changes no collector identity,
retry or HALT threshold, controller seam, proposal authority, persistence contract, or signing
boundary.
