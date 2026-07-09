# DESIGN — D4a downsampled market-memory persistence

**Date:** 2026-07-09 · **Ticket:** POL-13 · **Slice:** D4a-downsample ·
**Status:** SPEC LOCKED — owner selected and approved the recommended contract on 2026-07-09; the exact-spec prompt timed out with authorization to proceed using best judgment. Production code remains gated on this contract and its TDD plan.
**Depends on:** the landed D4a ingestion runtime, S1 `LocalBook`/CLOB ingestion, POL-12 `QueuedEventWriter`, and S3 `build_bar_series`.

This slice remains **public-data, read-only ingestion**. It cannot propose, size, sign, submit, cancel, or move funds.

---

## 0. Problem statement and measured evidence

The deployed D4a runtime correctly maintained live books but persisted every CLOB `book` and `price_change` observation. A 493.128-second capture produced:

- 102,918 rows and a 169,467,904-byte SQLite database;
- 101,418 `clob-ws` rows (205.663 rows/second);
- a projected **27.653 GiB/day** database growth rate;
- a projected 17,769,243 raw CLOB rows/day;
- 1,500 deduplicated `data-api` trade rows, projecting to 262,812 rows/day and 199.7 MiB/day of JSON content.

That rate would fill the shared VPS disk in roughly three days. The service is therefore correctly stopped and disabled.

The shadow evaluator does not need historical depth-delta replay for decisions: the ERS re-fetches and validates the live book at decision time. Historical co-move estimation needs aligned midpoint bars. The production persistence contract must therefore change while leaving live in-memory book reconstruction untouched.

A read-only replay of the captured books measured one compact batch containing 188 fresh token quotes at 23,160 bytes. At a 60-second cadence this is 31.81 MiB/day of snapshot JSON; extrapolated to 400 fresh tokens it is approximately 68 MiB/day. This supports a realistic total-store gate of **no more than 0.5 GiB/day** at the deployed top-200-market configuration while retaining the complete deduplicated trade rows.

A replay through the current default `SyntheticDetector` projected approximately 827,682 derived rows/day (mostly `large_print`), and no current evaluator consumes those rows. Synthetic persistence is therefore explicitly out of this slice rather than replacing one firehose with another.

---

## 1. Owner-resolved forks

The owner approved the recommended 60-second batch design on 2026-07-09.

| Fork | Decision |
|---|---|
| Production WS persistence | **No raw WS rows.** Every frame still updates `LocalBook`; the production `ShardedMarketCollector` receives `sink=None`. There is no runtime switch that can accidentally re-enable the firehose. |
| Snapshot shape | **One versioned batch envelope per cadence**, not one envelope per token. The batch contains exact-string bid, ask, and midpoint entries keyed by token ID. |
| Cadence | **60 seconds**, configurable by `snapshot_interval_seconds` and fail-loud validated. This preserves minute-scale correlation bars while keeping row overhead tiny. |
| Stale/invalid books | **Omit the token from that batch.** Never forward-fill and never publish a midpoint from a stale, empty, locked, or crossed book. An all-stale cycle still writes a valid empty batch, proving sampler cadence without inventing prices. |
| Trade tape | Keep the existing full, deduplicated global Data API `/trades` rows unchanged. Do not compact fields before D1/D2/D3 consumers pin their field contract. |
| Historical compatibility | `build_bar_series` auto-prefers midpoint batches when present and falls back to the existing raw-WS replay adapter for legacy stores and bounded diagnostic captures. It never combines both representations for the same query. |
| Raw diagnostics | Keep `PersistingSink` and replay-fidelity tooling for bounded manual diagnostics/tests only. The 24/7 production factory does not import or construct it. |
| Synthetic events | Do not wire persistence now. Current defaults project another high-volume stream and no evaluator consumes it. D4a.2 must treat synthetic events as live-only unless their thresholds and consumer contract are separately approved. |
| Clock domain | Keep the existing process/boot monotonic `observed_at` domain in this surgical slice. The cross-OS-reboot ordering limitation is documented in §8 and must be resolved before D4b; changing the pinned safety/liveness clock semantics is not mixed into a storage-volume fix. |
| Operational gate | A 30-minute live, read-only endurance run at the deployed top-200-market configuration must project total SQLite growth at **≤0.5 GiB/day**, contain midpoint and trade rows, and contain zero raw `clob-ws` rows. |

---

## 2. Goals and non-goals

### Goals

1. Preserve every live CLOB frame's effect on the in-memory `LocalBook`, including gap detection, staleness, resync, and health tracking.
2. Remove raw CLOB persistence from the production D4a composition root.
3. Persist one compact, versioned, point-in-time batch of all usable token top-of-books every 60 seconds.
4. Make the co-move bar adapter consume those batches directly with no look-ahead and no forward-fill.
5. Preserve legacy raw-store replay for existing tests and bounded manual diagnostics.
6. Retain the current full deduplicated `/trades` tape.
7. Prove the corrected live storage rate on the real venue before the service is re-enabled.

### Non-goals

- Compacting or changing Data API trade fields or deduplication.
- Persisting raw WS frames in production, even behind a config flag.
- Wiring/tuning `SyntheticDetector`, Polygon, news, or dynamic-universe refresh (D4a.2).
- Changing `MonotonicStamper`, anomaly/liveness clocks, or the cross-reboot timestamp model.
- Changing `EventStore` schema, batching commits, adding compression, retention, VACUUM, or deletion.
- Reconstructing historical full-depth books from production data after this slice; production deliberately retains only the derived signal the evaluator consumes.
- Any Hermes, ERS, harness, signer, wallet, or real-money behavior.
- Automatically starting/enabling the stopped service as part of code implementation.

---

## 3. Architecture and data flow

### Before

```text
CLOB WS frame
  -> MarketStream mutates LocalBook
  -> PersistingSink serializes the raw frame
  -> QueuedEventWriter
  -> EventStore source="clob-ws"       (~27.65 GiB/day total DB)

Data API /trades
  -> DataApiPoller
  -> QueuedEventWriter
  -> EventStore source="data-api"
```

### After

```text
CLOB WS frame
  -> MarketStream mutates LocalBook, verifies top-of-book, marks stale/resyncs as today
  -> no raw persistence sink

Every snapshot_interval_seconds (default 60):
  MidpointSnapshotter
    -> synchronously reads collector.book_for(token) for the fixed discovered universe
    -> includes only book.midpoint() != None
    -> writes ONE versioned clob-midpoint batch Envelope
    -> QueuedEventWriter -> EventStore

Data API /trades
  -> unchanged DataApiPoller
  -> unchanged full deduplicated data-api Envelope rows
  -> same QueuedEventWriter -> same EventStore

Co-move history
  -> build_bar_series
  -> clob-midpoint batches when present
  -> legacy clob-ws replay only when no batches exist or explicitly requested
```

`MidpointSnapshotter.snapshot_once()` is synchronous and contains no `await`. It stamps, reads all books, serializes, and enqueues one envelope without yielding the event loop; no WS frame can interleave halfway through a batch. All records continue to use the one process-wide `MonotonicStamper` and the one off-loop writer.

---

## 4. Pinned contract: exact units and signatures

### 4.1 `src/polybot/ingestion/midpoint.py` — new

```python
from dataclasses import dataclass
from decimal import Decimal

MIDPOINT_SOURCE = "clob-midpoint"
MIDPOINT_SCHEMA = 1

@dataclass(frozen=True)
class MidpointQuote:
    bid: Decimal
    ask: Decimal
    midpoint: Decimal


def decode_midpoint_batch(content: str) -> dict[str, MidpointQuote]:
    """Parse and fail-loud validate one version-1 midpoint batch."""


class MidpointSnapshotter:
    def __init__(
        self,
        *,
        token_ids,
        book_for,
        stamper,
        writer,
        interval_seconds: float = 60.0,
        sleep=asyncio.sleep,
    ) -> None: ...

    def snapshot_once(self) -> int:
        """Append one batch Envelope and return the number of included fresh books."""

    async def run(self) -> None:
        """Sleep one interval, snapshot once, and repeat forever."""
```

Construction requirements:

- `token_ids` is materialized once, must be non-empty, must contain non-empty strings, and must contain no duplicates. The serialized order is lexicographically sorted for determinism.
- `book_for(token_id)` is the owning `ShardedMarketCollector.book_for` seam.
- `interval_seconds` must be a finite number greater than zero.
- `writer` exposes synchronous `append(Envelope)`; production passes the shared `QueuedEventWriter`.
- Any unexpected `book_for`, book accessor, encoding, or writer exception propagates. A sampler failure is a supervised ingestion HALT, not a silently missing history stream.

`snapshot_once()` contract:

1. Call the shared stamper exactly once, before reading books.
2. Iterate the fixed sorted token list without awaiting.
3. For each token:
   - `book is None` -> omit;
   - `book.midpoint() is None` -> omit (covers stale, missing side, locked, crossed);
   - otherwise obtain `best_bid()` and `best_ask()` and encode bid/ask/midpoint with `str(Decimal)`.
4. Append exactly one `Envelope` even when no token is usable:

```python
Envelope(
    source="clob-midpoint",
    source_tier="VENUE",
    event_id=f"batch:{observed_at}",
    observed_at=observed_at,
    content=<compact deterministic JSON>,
    published_at=None,
    market_links=tuple(included_token_ids),
)
```

5. Return `len(included_token_ids)`.

The exact compact JSON wire shape is:

```json
{
  "schema": 1,
  "books": {
    "<token_id>": {"bid": "0.60", "ask": "0.62", "mid": "0.61"}
  }
}
```

Production encoding uses `sort_keys=True` and `separators=(",", ":")`. No sizes, full depth, raw deltas, venue frame, question text, or profile metadata is included.

`decode_midpoint_batch` must fail loud (`ValueError`) unless all of the following hold:

- decoded JSON is an object with exactly `schema` and `books`;
- `schema == 1` (unknown versions are not guessed);
- `books` is an object;
- every token ID is a non-empty string;
- every quote has exactly string fields `bid`, `ask`, and `mid`;
- every value parses as a finite `Decimal`;
- `0 <= bid < ask <= 1`;
- `mid == (bid + ask) / 2` exactly.

It returns `dict[str, MidpointQuote]`. Corruption or a future schema change aborts the reader rather than warming a risk model from partial or ambiguous prices.

### 4.2 `src/polybot/runtime/config.py` — modify

```python
@dataclass(frozen=True)
class IngestionConfig:
    ...
    snapshot_interval_seconds: float = 60.0
```

- Add the field to the existing finite-and-positive interval validation.
- Add it to `_FLOAT_FIELDS`, preserving TOML/env overlay through `POLYBOT_INGEST_SNAPSHOT_INTERVAL_SECONDS`.
- Add no raw-persistence flag.

### 4.3 `src/polybot/runtime/ingestion.py` — modify

Production wiring becomes:

```python
writer = QueuedEventWriter(EventStore(config.db_path, check_same_thread=False))
ws = ShardedMarketCollector(
    ws_connect,
    stamper,
    token_ids,
    sink=None,
    max_assets_per_shard=config.max_assets_per_shard,
    reconnect_on=WS_RECONNECT_ON,
)
snapshotter = MidpointSnapshotter(
    token_ids=token_ids,
    book_for=ws.book_for,
    stamper=stamper,
    writer=writer,
    interval_seconds=config.snapshot_interval_seconds,
)
services = [
    _supervised("clob-ws", lambda: ws.run(max_connections=None)),
    _supervised("clob-midpoint", snapshotter.run),
]
```

The existing `DataApiPoller` service remains unchanged and is appended when `data_api_enabled` is true. `IngestionRuntime` supervision, stop behavior, heartbeat, and writer-close durability remain byte-for-byte unless a test proves a minimal change is required. `runtime/ingestion.py` no longer imports or constructs `PersistingSink`.

Service counts become:

- Data API enabled: WS + midpoint sampler + Data API = 3 supervised services.
- Data API disabled: WS + midpoint sampler = 2 supervised services.

### 4.4 `src/polybot/ers/comove.py` — modify only the store adapter

```python
def build_bar_series(store, *, bar_ns, until=None, source=None): ...
```

Pinned behavior:

- Materialize the same bounded sequence as today: `store.all()` or `store.replay_until(until)`.
- `source is None`:
  - if at least one `MIDPOINT_SOURCE` row exists in the bounded sequence, consume only midpoint rows;
  - otherwise consume legacy `clob-ws` rows through the existing `MarketStream` replay path.
- `source == MIDPOINT_SOURCE`: consume only midpoint rows, returning empty bars if none exist.
- Any other explicit `source`: use the existing raw-frame reconstruction path for that source.
- Midpoint path: `decode_midpoint_batch`, compute `bar_index = env.observed_at // bar_ns`, and assign each included token's midpoint to that bar. A later snapshot in the same bar overwrites an earlier one (closing midpoint), matching existing semantics.
- A token omitted from a batch receives no point for that batch/bar. Never forward-fill.
- A store containing both legacy raw rows and midpoint batches uses only midpoint batches under auto mode, preventing duplicate/conflicting observations.
- `correlation`, `_returns`, `ClusterModel`, and every risk-cap consumer remain byte-for-byte.

### 4.5 Deployment/config/docs

- `deploy/config.example.toml`: add `snapshot_interval_seconds = 60.0` and state that production does not retain raw CLOB frames.
- `scripts/downsample_endurance_check.py`: new public-data-only manual gate. It runs the real D4a composition against a temporary database for a configurable duration (release gate: 1,800 seconds), requests a graceful stop, then reports/asserts:
  - at least one `clob-midpoint` row;
  - at least one `data-api` row when enabled;
  - zero `clob-ws` rows;
  - midpoint batch schema decodes;
  - usable books were captured;
  - total database footprint projected from the run is `<= 0.5 GiB/day`;
  - no collector/writer HALT and graceful close completed.
- After code and review are green, update `docs/HANDOFF.md`, the original D4a design's built/deferred language, and `deploy/README.md` to reflect the new production substrate and the loss of raw historical replay.
- Deployment remains a separately approved operation. Preserve the existing raw capture under an evidence filename and start the corrected service with a fresh `market_memory.db`; do not delete or overwrite the evidence database.

---

## 5. Safety and correctness invariants

1. **Read-only boundary:** no signer, intent, ERS decision, harness, wallet, or order import is added.
2. **No production firehose:** the production factory never constructs a raw WS persistence sink. A live acceptance database contains zero `source="clob-ws"` rows.
3. **Live book unchanged:** every WS frame still reaches `MarketStream` and mutates/verifies the same `LocalBook`; only the sink changes from raw persistence to `None`.
4. **One atomic batch:** one stamper call and one envelope per cadence; synchronous sampling has no event-loop yield and therefore no half-old/half-new interleaving by another frame.
5. **Fail closed on stale data:** no stale/empty/locked/crossed token is serialized; no forward-fill exists.
6. **Exact money/price math:** all persisted prices originate as `Decimal` and are encoded as strings; the decoder rejects non-string, non-finite, out-of-domain, or internally inconsistent prices.
7. **Fail loud on schema/corruption:** unknown schema, malformed payload, or writer failure propagates.
8. **Point-in-time/no-look-ahead:** `until` bounds rows before decoding; bars use only observations within that bounded sequence.
9. **Representation exclusivity:** auto mode consumes snapshot batches if present, otherwise legacy raw frames; it never merges both.
10. **Trade losslessness:** Data API polling, event IDs, payload fields, cadence, and dedup semantics remain unchanged.
11. **Durable shutdown:** the existing writer-close-exactly-once `finally` remains the one durability spine for snapshots and trades.
12. **Bounded footprint:** one snapshot row per minute, one global trade poll per configured interval, no raw WS rows, and a measured release ceiling of 0.5 GiB/day.
13. **Sacred surfaces untouched:** `ers/validator.py`, `evaluate_intent`, `propose_trade`, caps, signer, safety state machine, and every money-moving seam remain byte-for-byte.

---

## 6. Acceptance criteria

| ID | Criterion |
|---|---|
| A1 | `snapshot_once()` with two fresh books appends exactly one `clob-midpoint` envelope, stamps exactly once, includes both token links, and returns 2. The compact payload round-trips through `decode_midpoint_batch` with exact Decimals. |
| A2 | Missing, stale, one-sided, locked, and crossed books are omitted. An all-unusable cycle still writes one valid empty batch and returns 0. |
| A3 | Token/input order cannot change serialized content: output keys and `market_links` are deterministic. Duplicate/empty token IDs and invalid intervals fail at construction. |
| A4 | Decoder mutations are killed by named tests: accept unknown schema; accept extra/missing keys; coerce numeric JSON values; accept NaN/Infinity; relax bid/ask domain or crossing; trust a forged midpoint. Every mutation must make a named test fail. |
| A5 | `run()` sleeps before its first snapshot, emits once per completed interval, propagates snapshot/writer errors, and cancels cleanly under `IngestionRuntime`. |
| A6 | `IngestionConfig` defaults to 60 seconds; TOML and env overrides work; zero, negative, NaN, and infinity fail loud. No raw-persistence config field exists. |
| A7 | Production factory wires 3 supervised services with Data API enabled and 2 when disabled; the WS collector has no raw sink; the sampler and all collectors share one stamper/writer. Existing writer-close durability tests stay green. |
| A8 | Snapshot-backed `build_bar_series` takes the closing midpoint per bar, aligns multiple tokens, respects `until`, omits missing token points, rejects malformed batches, and ignores raw rows when snapshots are present. |
| A9 | Legacy raw-only stores still produce the exact current bars under auto mode and explicit `source="clob-ws"`; existing replay-fidelity tests remain green. |
| A10 | Structural import sweep proves production `runtime/ingestion.py` does not import `PersistingSink`, and the diff does not touch sacred ERS/signing/risk surfaces. |
| A11 | Targeted tests, then `./.venv/bin/pytest -o addopts="" -q`, pass with zero skips/xfails and an explicit count. |
| A12 | Independent spec-compliance review passes, followed by an adversarial mutation review covering A4 plus: re-enable raw sink; include stale books; forward-fill a missing token; merge raw+snapshot bars; change one batch to per-token rows; skip writer close. Every mutation is killed by a named test or structural check. |
| A13 | The 30-minute real-venue endurance gate at top 200 markets exits 0, persists midpoint+trade rows and zero raw rows, and projects total SQLite growth at `<=0.5 GiB/day`. |
| A14 | Before any service start, the old raw database is preserved and verified by size/checksum under an evidence filename; the corrected service starts on a fresh database. Service enable/start requires separate owner approval after code lands. |

---

## 7. Serial implementation decomposition

The plan must decompose this into serial RED→GREEN sub-slices; no parallel writers:

1. **D4a-ds1 — midpoint codec and one-shot sampler:** pure quote/schema validation, deterministic batch creation, stale omission.
2. **D4a-ds2 — sampler cadence and config:** async loop, finite-positive 60-second config field, TOML/env overlay.
3. **D4a-ds3 — production factory cutover:** `sink=None`, sampler supervised beside WS, Data API unchanged, service-count and no-raw integration tests.
4. **D4a-ds4 — co-move snapshot adapter:** snapshot-first bars, no-look-ahead, omission semantics, legacy fallback.
5. **D4a-ds5 — operational gate and docs:** endurance script, example config, handoff/original-design/runbook reconciliation only after code is green.

Each sub-slice: one behavior per RED, run the true RED, minimal GREEN, targeted tests, commit; then independent spec review and adversarial mutation review. Re-review after fixes. Final whole-slice review and full suite precede any merge.

---

## 8. Explicitly deferred risks and follow-ups

### 8.1 Cross-OS-reboot `observed_at` ordering

`MonotonicStamper` defaults to `time.monotonic_ns`, giving a strict process/boot-local order but resetting after an OS reboot. A persistent store spanning a VPS reboot can therefore contain newer rows with smaller `observed_at` values. This is an existing D4a/S1 limitation, not introduced by downsampling.

It is intentionally not fixed here because `MarketStream.last_frame_at`, the L5 WS sentinel, reconcile windows, and pinned safety documents share the monotonic clock domain. A partial clock change inside a volume fix could create a fail-open liveness comparison. Before D4b, write a separate design resolving persistent chronology versus monotonic health time (likely distinct persisted and health clock seams) and migration semantics. Do not silently switch the production stamper in this slice.

### 8.2 Synthetic events are no longer recomputable from production history

The original D4a design deferred synthetic wiring because raw frames could reproduce it. Production will no longer retain those frames. D4a.2 must therefore either wire a separately approved, tuned, low-volume live synthetic stream or explicitly drop that historical feature. Current defaults are not acceptable for blind persistence: the captured sample projected roughly 827,682 synthetic rows/day and `large_print` cannot distinguish fills from cancellations.

### 8.3 Full trade payload dominates retained volume

The full `/trades` rows project to approximately 199.7 MiB/day of JSON before SQLite overhead. This is retained now to avoid deleting identity/market fields before D1/D2/D3 pin their exact consumer contract. If the 30-minute gate exceeds 0.5 GiB/day, the slice fails; it does not loosen the ceiling. The owner must then approve a second design that compacts the trade schema with explicit downstream field requirements.

### 8.4 No production full-depth replay

After this cutover, production history cannot reconstruct full order-book depth or run the old replay-fidelity gate against the 24/7 database. That is deliberate. Bounded manual diagnostics can still use `PersistingSink` to a temporary database; the production store captures only midpoint batches and the trade tape.

---

## 9. Definition of done

The slice is done only when:

- the exact contract above has owner sign-off;
- its serial TDD plan has owner sign-off;
- every sub-slice completed true RED→GREEN cycles and both review stages;
- the final mutation review has no survivors;
- the full suite is green with explicit pass count and no skips/xfails;
- the 30-minute live rate gate passes at `<=0.5 GiB/day` with zero raw WS rows;
- docs reflect that raw history is not retained;
- the old raw database remains preserved and verified;
- merge/push/deploy/service-start are each evidenced and separately approved as required.

Until then, `polymarket-ingestion.service` stays stopped and disabled.
