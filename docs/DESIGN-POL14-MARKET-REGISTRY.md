# POL-14 — D1 MarketRegistry design

**Status:** owner-approved 2026-07-10
**Ticket:** POL-14 (child of POL-13)
**Branch:** `pol-14-market-registry`
**Verification:** [VERIFICATION-POL14-MARKET-REGISTRY.md](VERIFICATION-POL14-MARKET-REGISTRY.md)

## 1. Purpose

Replace the production-facing `StubMarketMeta` seam with an immutable, network-free registry that
re-derives a proposal's market category, canonical question, and seconds to resolution from Gamma
metadata. This is the keystone for per-category calibration: an intent must not choose its own
category or pair an unrelated condition with a token.

POL-14 builds the pure registry and the ERS fail-closed integration. POL-17 will compose the real
Gamma fetches into the full shadow runtime. The current ingestion service, database, deployment,
signer, and paper/live gates are untouched.

## 2. Live-provider audit that changes the naive ticket interpretation

The live Gamma schema was inspected on 2026-07-10 against official Polymarket documentation and
current API responses:

- `/markets` reliably supplies `conditionId`, `question`, market-specific `endDate`,
  `clobTokenIds`, and a nested event identifier.
- Although the published schema still documents `category`, live `/markets` and `/events`
  responses omitted that field in the sampled active rows.
- `/events` supplies ordered tag objects, including stable broad tag IDs such as Sports `1`,
  Politics `2`, Crypto `21`, Finance `120`, Geopolitics `100265`, and Weather `84`.
- Nested markets under `/events` are not sufficient for time-to-resolution: many omit market
  `endDate`, and the event deadline can be later than an individual market's deadline.
- Therefore the registry consumes two already-fetched snapshots: selected market rows for identity,
  question, and deadline; matching event rows for category tags. It performs no network I/O.

## 3. Approved contract

### 3.1 Types

`MarketMetadata` is frozen and contains:

- `category: str`
- `question_text: str`
- `seconds_to_resolution: int`

`MarketRegistry` stores immutable market definitions indexed by both `condition_id` and each exact
ERC-1155 `token_id`. It exposes one operation:

```python
metadata_for(intent) -> MarketMetadata
```

The intent must expose both `.condition_id` and `.token_id`. Both identifiers must exist and must
resolve to the same market. Lookup never trusts one identifier while ignoring a conflicting sibling.

### 3.2 Category policy

The policy is versioned, explicit, and allowlisted by Gamma tag ID. It maps into the existing
repository category vocabulary:

| Category | Gamma tag IDs in v1 |
|---|---|
| sports | `1` |
| geopolitics | `100265` |
| politics | `2` |
| crypto | `21` |
| finance | `120` (Finance), `107` (Business) |
| econ | `100328` (Economy), `159` (Fed), `225` (economics) |
| tech | `1401` (Tech), `439` (AI) |
| culture | `596` (Culture / `pop-culture`) |
| weather | `84` |

A tag label or slug alone never activates a category; IDs are the reviewed identity surface.
Multi-category events use the owner-approved deterministic precedence:

```text
sports > geopolitics > politics > crypto > finance > econ > tech > culture > weather
```

The precedence deliberately maps geopolitics+politics to `geopolitics` and crypto+finance to
`crypto`. A future taxonomy change must edit this reviewed table and its tests; arbitrary new Gamma
tags fail closed rather than creating calibration buckets.

### 3.3 Time semantics

- Parse only offset-aware RFC3339/ISO-8601 market `endDate` values.
- Store the UTC epoch deadline; use one injected wall-clock reading per lookup.
- `seconds_to_resolution = max(0, floor(end_epoch - now_epoch))`.
- Past deadlines return zero. Missing, naive, or malformed deadlines are unavailable.
- Never compare a Gamma wall-clock deadline with `MonotonicStamper` values.

### 3.4 Snapshot parsing and identity invariants

- Top-level market/event snapshots must be lists of mappings.
- Conditions, event IDs, questions, and token IDs must be non-empty strings.
- `clobTokenIds` may be Gamma's JSON-encoded string or an already-parsed list, but must contain
  exactly two distinct string token IDs. Numeric coercion is forbidden.
- A market must name exactly one event represented by the event snapshot.
- The referenced event must independently embed the selected `conditionId` with the exact same
  two-token sibling list before its tags may authorize a category.
- A missing event or missing event-contained market relationship leaves that market unavailable;
  a contradictory event/market token identity is a fatal snapshot-construction error.
- Duplicate identical rows are idempotent.
- A condition with conflicting definitions, a token reused by two conditions, or duplicate token
  siblings is a fatal snapshot-construction error.
- An event with no reviewed category tag makes its markets unavailable; it never maps to
  `unknown` or an arbitrary raw category.
- If no market is usable, construction fails loudly.

### 3.5 ERS behavior

`_process_intent_pipeline` resolves metadata after the cheap book/detector/truth gates and before
fusion/forecast recording. A known `MarketMetadataUnavailable` becomes:

```text
REJECT market_meta_unavailable
```

No forecast or component row is written. Unexpected implementation failures continue to use the
outer `internal_error` isolation path.

`StubMarketMeta` remains only as an explicit legacy/test fixture and implements the same
`metadata_for` contract. Production composition in POL-17 must supply `MarketRegistry`; it may not
silently default to the stub.

## 4. Security and safety invariants

1. Hermes cannot self-assign a category, question, deadline, or mismatched market identity.
2. Missing/malformed/unmapped provider data never means safe; the intent is unavailable.
3. Unavailable metadata never enters the non-backfillable forecast/calibration substrate.
4. Unknown categories never accumulate enough mixed outcomes to warm `k`.
5. Provider parsing is strict about wire types; exact token strings are never converted through
   floating-point or integer JSON representations.
6. The registry is immutable after construction. Refresh means build a complete new registry and
   atomically replace it in the future composition root.
7. No network call, database write, signing, service state change, or deployment is in this slice.

## 5. Acceptance criteria

- Targeted tests prove the canonical category map and every precedence boundary.
- Tests prove condition/token cross-validation, event-contained condition/token reconciliation, exact
  string handling, duplicate conflicts, malformed snapshots, unknown events/categories, missing
  questions/deadlines, and clock boundaries.
- An ERS integration test proves unavailable metadata returns `market_meta_unavailable` and writes
  neither forecast nor component rows.
- A whole-slice test uses representative live-shaped `/markets` + `/events` fixtures and returns the
  Gamma question/category/time, not proposal-owned metadata.
- Existing stub-dependent behavior remains explicitly testable but is not the production default.
- Full test suite, compile check, diff check, independent specification review, and adversarial
  mutation battery pass before the slice is called complete.

## 6. Out of scope

- Fetching/paginating Gamma in the full ERS runtime (POL-17 composition work).
- Periodic/atomic registry refresh orchestration.
- Resolution/settlement ingestion (POL-15).
- Shadow fill execution wiring (POL-16).
- Full D4b ERS/harness runtime (POL-17).
- Brain proposal-loop deployment (POL-18).
- Deployment, database migration, service activation, or live signing.
