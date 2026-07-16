# DESIGN — POL-17: ERS + harness runtime

**Date:** 2026-07-15 · **Ticket:** POL-17 · **Status:** owner-approved contract

## 1. Goal

Run the existing paper-only propose → validate → shadow-execute → settle → evaluate slice
continuously, under one supervisor, without weakening any ERS or settlement authority boundary.
POL-17 composes the D4a ingestion runtime, POL-14 `MarketRegistry`, POL-15 resolution authority,
POL-16 atomic shadow execution, the S4 safety controller, and the S9 harness. Before POL-18 exists
the runtime is genuinely idle: it consumes zero proposals and never manufactures production data.

This ticket ends at a reviewed build. Installation, checkout movement, database creation in the
service footprint, systemd start/enable, and activation are separate owner gates.

## 2. Resolved architecture forks

| Fork | Decision |
|---|---|
| Execution book | **One process and one websocket collector.** Ingestion and ERS share the collector's in-memory `LocalBook` objects. Persisted 60-second midpoint batches are evidence only and can never authorize an ACCEPT. |
| Service shape | Extend the existing `polymarket-ingestion.service` deployment contract to launch the composite entry point. Keep the ingestion-only Python entry point for diagnostics; do not create a concurrently active collector unit. |
| Concurrency | Websocket mutation, `LocalBook` reads, ERS validation, and POL-16 planning stay on one asyncio event-loop thread. Blocking Gamma and Polygon calls execute in one bounded worker and are awaited at explicit barriers. |
| Universe | Discover once at startup. Registry refreshes may replace metadata for that fixed selected universe, but may not silently add subscriptions. Dynamic resubscription is deferred. |
| Resolution preflight | Poll every unresolved canonical forecast plus every current PROPOSED intent condition before ERS. Only provider-confirmed `UNRESOLVED` intents are eligible in that cycle. `UNAVAILABLE` and finalized `UNKNOWN` are deferred, not guessed. |
| Acceptance durability | A paper ACCEPT atomically persists the decision, internal fill, flow-journal entry, canonical shadow execution, and Maker/Shadow outbox. Existing optional seams keep `None` behavior compatible. |
| News | Wire the existing allowlisted, sanitized `NewsPoller`, because the real truth gate and future POL-18 proposal path need durable citations. No calendar, synthetic feed, or new source class is added. |
| Future proposals | POL-18 attaches through a trusted local Unix-socket adapter exposing only `ProposeOnlyFacade`. It receives no database handle, signer, controller, resolution feed, book mutation, or runtime lifecycle authority. |
| Watchdog | Preserve and test existing S4 controller/anomaly/loss/restart seams. A live signer-B watchdog is deferred with POL-4 because `PaperSigner` has no external venue state to de-risk. |
| Delivery | Reviewed build only. No push, merge, install, migration, service start, enablement, or production database mutation in POL-17 without a new explicit approval. |

## 3. Process architecture

```text
                         ONE supervised Python process

Gamma snapshot ──► fixed token universe + immutable MarketRegistry generation
                                      │
CLOB websocket ──► ShardedMarketCollector ──► shared live LocalBook
                         │                         │
                         ├─► 60s midpoint evidence│
Data API trade tape ─────┴─► QueuedEventWriter    │
allowlisted news ──────────► EventStore           │
                                                   ▼
PROPOSED IntentStore ──► ERSController ──► POL-16 planner
                               │             (fresh book re-fetch)
                               ▼                    │
                  ACCEPT + fill + flow + execution + outbox
                                                   │
Polygon provider A + B ─► ResolutionFeed           ▼
             │              │             ShadowExecutionDispatcher
             │              ▼                  │          │
             └────► ResolutionDispatcher ──► MakerLedger  ShadowLedger
                              │
                              └──────────────► ForecastLedger
                                                   │
                                     terminal-first marks/evidence/ramp advice
```

The composition root lives under `polybot.runtime` and owns construction and closure of every
store. The orchestration core accepts injected collaborators and transport seams so ordering,
failure injection, and restart behavior remain hermetic in tests. D4a's existing
`build_ingestion_runtime` remains supported; shared assembly exposes the collector rather than
reaching into its private state.

### Clock domains

Two time domains are deliberate:

- **history clock:** epoch-compatible nanoseconds, floored strictly above the maximum persisted
  observation at boot, shared by midpoint, Data API, news, intent, forecast, component, execution,
  resolution, and evidence writers;
- **health clock:** process-local monotonic nanoseconds, shared by websocket frame stamps,
  anomaly age checks, restart reconciliation, readiness timeouts, and cadence.

Persisted monotonic values from a prior OS boot are never compared with the current monotonic
epoch. The production websocket path still persists zero raw `clob-ws` rows, so using the health
clock for in-memory frame stamps cannot break durable replay ordering.

## 4. Pinned lifecycle ordering

### Startup and restart barrier

1. Acquire a non-blocking singleton lock before opening writable databases.
2. Parse and validate configuration: paper-only mode, distinct paths, fixed positive cadences,
   distinct provider IDs/URLs, Polygon chain 137, and no signer/key/order endpoint fields.
3. Initialize the history clock above durable history and the independent health clock.
4. Fetch one consistent Gamma market snapshot and the matching bulk event snapshot; build the
   fixed token universe and immutable `MarketRegistry` generation. A missing usable startup
   registry is fatal.
5. Open and integrity-check EventStore, IntentStore, ForecastLedger, ComponentLog, MakerLedger,
   ShadowLedger, and ResolutionStore. Any path alias or durable identity contradiction is fatal.
6. Start exactly one websocket collector plus midpoint, trade-tape, and approved news services.
7. Run `ResolutionFeed.recover_pending()` through both configured providers. This is a hard
   recovery barrier; no outbox dispatch or ERS processing may precede it.
8. Drain the POL-15 resolution outbox in canonical FORECAST → MAKER → SHADOW order.
9. Drain the pre-existing POL-16 execution outbox in MAKER → SHADOW order.
10. Wait until every websocket shard has observed a real bookable frame
    (`collector.last_frame_at() is not None`). Individual books may still be absent/stale and
    will fail closed per market. Startup readiness has a finite timeout.
11. Call `ERSController.boot()` exactly once with `RestartReconciler(wallet=None)` to rebuild the
    durable portfolio. Only its clean DORMANT reconciliation may transition HALTED → RUNNING.
12. Apply delivered resolution state to that rebuilt portfolio: retire terminal conditions;
    freeze finalized-unknown conditions. Applying state before `boot()` would mutate only the
    empty construction portfolio and could resurrect terminal risk during the rebuild.
13. Publish readiness and begin cadence. Readiness must never precede recovery, live-book, and
    restart reconciliation barriers.

### One runtime cycle

1. Honor shutdown and write the runtime heartbeat/status.
2. If due, fetch a fixed-universe registry replacement off-loop. Every filtered Gamma request
   carries an explicit limit for the already-frozen market/event set. A coherent strict subset
   publishes a fresh registry containing only its currently usable conditions; omitted or
   metadata-quarantined conditions lose ERS and Hermes read authority but remain harmless extra
   websocket subscriptions. A later response may restore them only with their exact frozen
   identities. An extra condition, any market/event token-identity change (including for an
   omitted frozen condition embedded in an event), or a replacement with no usable market is
   fatal. Transport/server unavailability retains the last coherent generation only inside its
   maximum age; beyond that budget, halt new intent processing.
3. Form unique canonical resolution subjects from unresolved forecasts plus the current PROPOSED
   intent snapshot using the active registry.
4. Poll both Polygon providers off-loop at one common acceptance coordinate.
5. Drain POL-15 terminal outbox apply-before-ack to Forecast, Maker, and Shadow.
6. Retire delivered terminal positions and freeze finalized-unknown positions in controller state.
7. Call `ERSController.run_cycle(eligible_intent_ids=...)` on the event-loop thread. Its internal
   ordering remains Telegram (when later wired) → heartbeat → anomaly → loss breakers/ramp-down →
   op-state/breaker → serialized intents. The eligible set contains only this cycle's provider-
   confirmed `UNRESOLVED` proposals.
8. Drain POL-16 shadow execution outbox apply-before-ack to Maker then Shadow.
9. Compute terminal-first marks, category evidence, and advisory `RampController` decisions.
   Evidence may recommend promotion but cannot mutate caps or activate money.
10. Atomically update runtime status and wait with an interruptible cadence sleep.

Cycle N never overlaps cycle N+1. Blocking resolution or registry work is serialized in one
bounded executor; it cannot stall websocket frame handling and cannot race another resolution
poll. An outbox batch may be bounded, but ERS may run only after all older terminal work for the
cycle has drained.

### Graceful shutdown

1. Stop admitting a new cycle and signal all async services.
2. Let an in-progress blocking call reach its configured timeout; do not abandon an unknown target
   commit and do not begin more RPC work.
3. Drain already-enqueued market evidence and close `QueuedEventWriter` exactly once.
4. Close stores in reverse construction order, release the singleton lock, and remove readiness.
5. Exit zero only for an operator-requested clean stop. A supervised service's normal return,
   durability error, or fatal orchestration failure exits non-zero for systemd restart handling.

The deployment contract uses `Type=notify`, `After/Wants=network-online.target`,
`Restart=on-failure`, `RestartSec=5`, `StartLimitIntervalSec=300`, `StartLimitBurst=5`, and a
60-second stop timeout. Installation must still leave the unit stopped and disabled.

## 5. Failure model

### Isolate one market or source

- missing, stale, crossed, one-sided, or degenerate `LocalBook`;
- registry metadata unavailable for one condition in an otherwise coherent generation;
- one selected market's missing or malformed market-owned deadline, provided at least one other
  market remains usable; no event-level deadline fallback is permitted;
- one condition's provider observation unavailable or finalized with unknown authority;
- a coherent Gamma strict subset: publish only its usable conditions and stop advertising or
  serving Hermes books for omitted/metadata-quarantined tokens;
- malformed/untrusted proposal and existing per-intent pipeline failures;
- an unfilled maker simulation;
- one allowlisted news source fetch/parse failure (the other allowlisted sources continue).

These conditions never authorize an ACCEPT. A proposed intent remains pending when authority is
temporarily unavailable; deterministic invalid proposals retain existing audited rejection rules.

### Halt the supervised runtime

- websocket schema change, writer death/backlog exhaustion, or any infinite service returning;
- startup/recovery/readiness ordering violation or timeout;
- database alias, corruption, outbox orphan, acknowledgement mismatch, or contradictory identity;
- `SettlementConflict`, persistent resolution integrity halt, or target projection contradiction;
- loss of both configured provider identities, wrong chain, or malformed durable terminal data;
- an initial or replacement registry with no usable market, a refresh that expands or
  contradicts frozen market/event token identity, or a transport-retained last-good registry
  exceeding max age;
- evidence corruption or an unexpected orchestration exception outside a per-market boundary.

Transient full-provider outage yields per-condition `UNAVAILABLE` and zero eligible proposals for
that cycle; it does not erase authority. Sustained outage is visible in heartbeat/status and may
trip the configured supervision staleness threshold. No path falls back to persisted midpoint data.

## 6. Additive public seams

```python
# ers/intent_store.py
@dataclass(frozen=True)
class AcceptJournalRecord:
    token_id: str
    condition_id: str
    event_id: str
    shares: Decimal
    price_exec: Decimal
    worst_case_risk: Decimal
    wall_at: float

class IntentStore:
    def record_decision(
        self, intent_id, decision, *, shadow_execution=None, accept_journal=None
    ): ...
```

`accept_journal=None` is the exact existing behavior. A supplied journal is valid only for ACCEPT,
must match the proposal/decision/execution identity and economics, and writes the internal fill and
flow row in the same SQLite transaction as decision/audit/execution/outbox.

```python
# ers/service.py
def process_pending(..., shadow_planner=None, eligible_intent_ids=None,
                    accept_wall_clock=None): ...

# ers/controller.py
class ERSController:
    def run_cycle(self, *, eligible_intent_ids=None): ...
    def apply_resolution_state(self, *, terminal_condition_ids=(),
                               frozen_condition_ids=()): ...
```

`eligible_intent_ids=None` and `accept_wall_clock=None` preserve every prior call site. An explicit
eligible set filters before any pipeline/forecast/decision side effect. Atomic journaling replaces
post-commit fill/flow sinks only when the new wall-clock seam is wired. Resolution state can only
remove risk or set `frozen=True`; it can never create a position, unfreeze one, change NAV, or
transition controller state.

The runtime package adds frozen self-validating configuration, a fixed-universe registry provider,
a pure ordered cycle coordinator, a supervised composite lifecycle, and a production builder. No
new API exposes signing, order submission, cancellation, wallet, keys, redemption, or chain writes.

## 7. Persistence and ownership

Production defaults are distinct files under `/opt/polymarket-bot/data`:

| File | Sole logical writer |
|---|---|
| `market_memory.db` | `QueuedEventWriter` (midpoints, trade tape, sanitized news) |
| `intents.db` | event-loop ERS / `IntentStore` |
| `forecasts.db` | event-loop resolution dispatcher / pipeline |
| `components.db` | event-loop pipeline |
| `maker.db` | event-loop shadow + resolution dispatchers |
| `shadow.db` | event-loop shadow + resolution dispatchers |
| `resolution.db` | bounded resolution worker/feed plus event-loop dispatcher, never concurrently |

The same path may not back two logical stores: Maker and Shadow share table names and receipt
schemas, so aliasing would destroy role separation. SQLite migrations are forward-only,
transactional, idempotent schema creation/validation at open. POL-17 does not migrate or touch the
existing production files. The runtime singleton lock and readiness/status live under `/run/polybot`
and contain no market authority.

Configuration requires two distinct read-only Polygon HTTPS providers, stable non-secret provider
IDs, request timeouts, Gamma URLs, an explicit source allowlist, cadence, batch limits, database
paths, and readiness/registry maximum ages. Provider credentials, if URLs require them, belong in a
root-owned environment file and are never logged. No private key configuration is accepted.

Initial reviewed defaults: ERS cycle 1 second; midpoint 60 seconds; registry refresh 300 seconds;
registry maximum age 900 seconds; resolution poll 60 seconds; bounded outbox batches; provider
request timeout 15 seconds; readiness timeout 60 seconds.

## 8. Safety invariants

1. `PaperSigner` is the only signer instance and has no network or key material.
2. The pinned RiskCaps content hash and canonical pUSD address self-test run before stores open;
   every paper ACCEPT stages the established deterministic GTD backstop with a 24-hour expiry.
3. Hermes remains propose-only and cannot size, price, sign, settle, operate, or stop the runtime.
4. ERS reads the live book on validation and POL-16 re-fetches it after ACCEPT before execution.
5. Persisted midpoint batches are evidence only and never an execution authority.
6. Terminal authority is exclusively POL-15; terminal fanout precedes new ERS work each cycle.
7. ACCEPT durability is atomic across audit, fill, flow, canonical execution, and outbox.
8. Both outboxes remain target-commit-before-ack, idempotent, ordered, and restart-replayable.
9. Terminal conditions retire risk; disputed/manual/finalized-unknown conditions freeze it.
10. Exact `Decimal` storage and conservative failure behavior are preserved.
11. Existing `evaluate_intent`, `ProposeOnlyFacade`, caps, signer protocol, and terminal models are unchanged.
12. Production stores zero raw CLOB websocket rows and retains the deduplicated trade tape.
13. No POL-17 code signs, submits, cancels, redeems, uses a wallet, or writes to Polygon.

### Paper-only restart limitation

The inherited DORMANT `wallet=None` reconciliation rebuilds paper positions and returns a new
process to `RUNNING`; it does not replay the prior process's sticky HALTED/PAUSED op-state from
`op_audit`. POL-17 retains that existing seam because this runtime has `PaperSigner` only and no
external venue authority. The audit remains durable and visible, but restoring operator/anomaly
state across automatic restart is a mandatory design gate before POL-4 may compose a live wallet
or signer. This limitation does not authorize weakening any in-process S4 sticky behavior.

## 9. Acceptance criteria

- Config and builder tests prove distinct paths/providers, paper-only construction, one collector,
  real `MarketRegistry`, real `HermesPipeline`, real resolution/shadow targets, `PaperSigner`, and
  no signing/key/order transport.
- Lifecycle tests mutation-pin startup recovery, resolution dispatch, execution replay, shard
  readiness, controller boot, readiness notification, service supervision, graceful close, and
  fatal normal-return behavior.
- Cycle tests mutation-pin the exact ten-step ordering, non-overlap, bounded off-loop blocking work,
  subject union/deduplication, and terminal-before-ERS precedence.
- ERS tests prove explicit eligibility has no side effects for deferred intents, `None` compatibility,
  atomic ACCEPT journaling, crash/restart portfolio rebuild, and tighten-only terminal/freeze state.
- Failure tests distinguish per-market/source isolation from fatal integrity/supervision errors and
  prove stale/unavailable books never fall back to persisted midpoints.
- Zero-proposal tests run real cycles without synthesizing an intent, execution, forecast, or trade.
- Whole-slice test covers live book → proposed intent → ERS validation → atomic execution outbox →
  injected target/process failure → restart/replay Maker+Shadow projection → POL-15 terminal fanout
  → terminal mark/evidence, with exact identity and Decimal economics.
- Isolated mutations cover supervision, every ordering edge, restart barriers, stale-book rejection,
  apply-before-ack, terminal precedence, safety-controller wiring, and authority non-expansion.
- Complete suite, compile, diff checks, independent specification/security review, deployment
  artifact review, and final reconciliation evidence pass before any landing request.

## 10. Explicit non-goals

POL-18 Hermes process/profile implementation; live signer or wallet; order submission/cancellation;
Polygon writes; redemption; authenticated live Telegram transport; dynamic universe resubscription;
calendar/synthetic ingestion; raw websocket persistence; cap promotion; production migration;
installation; service start/enable; push or merge.
