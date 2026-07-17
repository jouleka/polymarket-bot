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
must include the bounded detail and the shard's configured asset count. Missing detail is itself a
fail-loud internal invariant error. The retry/backoff/HALT behavior is otherwise unchanged in
Stage A.

## 4. Stopped-service live diagnostic

After Stage A tests pass, stop Hermes and then POL-17 cleanly. Run a bounded, non-persisting
collector from the synchronized development checkout against the production discovery/shard
configuration. It must use the single collector identity while production is stopped and exit on
the first terminal resync storm or its time bound. Preserve databases and raw-firehose evidence.

The captured `BookDivergence` decides Stage B:

- repeated identical asset after fresh snapshots: an asset-local reconstruction/race correction
  may be designed;
- changing assets, malformed values, or cross-shard evidence: preserve whole-process HALT and fix
  the protocol parser/ordering contract;
- no reproduction within the bound: deploy Stage A only, restart in order, and wait for one
  production recurrence before any behavior change.

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

