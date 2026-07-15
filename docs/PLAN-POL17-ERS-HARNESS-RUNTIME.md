# PLAN — POL-17 ERS + harness runtime

**Design:** [`DESIGN-POL17-ERS-HARNESS-RUNTIME.md`](DESIGN-POL17-ERS-HARNESS-RUNTIME.md)

Every implementation task is one serial RED → minimum GREEN cycle. Observe one real focused
failure for the intended reason before editing production code. After focused GREEN, run the full
canonical suite on tmpfs, review the diff, and create a checkpoint commit. Do not combine REDs from
future tasks, parallelize SQLite writers, or mutate sacred safety/signing surfaces.

Baseline: `22c0a21af5d8965311745237c3abf6175fd291b8`, 2,121 tests passing. Branch:
`pol-17-ers-harness-runtime`.

## Task 1 — Atomic ACCEPT journal

1. Add one failing `IntentStore` test proving a typed accept journal commits decision, audit,
   internal fill, flow entry, shadow execution, and two-role outbox together.
2. Implement the frozen record and optional `record_decision(..., accept_journal=None)` seam.
3. Add serial boundary REDs for non-ACCEPT use, identity/economic mismatch, invalid time/Decimal,
   rollback injection, exact restart round-trip, and legacy `None` compatibility.
4. Add `accept_wall_clock=None` to `process_pending`; when wired, derive the journal from the ERS
   decision/position before persistence and do not call post-commit fill/flow sinks.
5. Focused GREEN, full suite, diff/sacred-surface review, checkpoint commit.

## Task 2 — Eligibility and resolution portfolio state

1. Add a failing service test proving an explicit eligible-ID set leaves ineligible PROPOSED rows
   untouched with zero pipeline, forecast, decision, signer, or book side effects.
2. Implement `eligible_intent_ids=None` in `process_pending` and thread it through
   `ERSController.run_cycle` without changing the existing default order.
3. Add failing controller tests for terminal-condition retirement and finalized-unknown freezing.
4. Implement tighten-only `apply_resolution_state`; mutation-pin no add, no unfreeze, no NAV/state
   change, idempotency, and terminal dominance over frozen.
5. Focused GREEN, full suite, diff/sacred-surface review, checkpoint commit.

## Task 3 — Runtime configuration and fixed registry generation

1. Add a failing config test for distinct database paths, two distinct read-only Polygon provider
   identities/URLs, positive bounded cadence/timeouts, fixed paper mode, and rejection of unknown
   signer/key/order fields.
2. Implement the minimum frozen POL-17 configuration and TOML/env loader without breaking the D4a
   `IngestionConfig` loader.
3. Add failing registry-provider tests for consistent market+bulk-event snapshots, fixed-universe
   replacement, last-good TTL, per-market unavailability, and contradictory/freshness failure.
4. Implement immutable generation replacement using `MarketRegistry.from_gamma_snapshots`.
5. Focused GREEN, full suite, config secret/log review, checkpoint commit.

## Task 4 — Shared ingestion assembly and clocks

1. Add a failing builder test proving exactly one `ShardedMarketCollector` is exposed to both
   midpoint sampling and ERS `book_for`, with no raw websocket sink.
2. Refactor D4a construction into a backward-compatible shared assembly; keep
   `build_ingestion_runtime` behavior and public signature unchanged.
3. Add serial REDs for history-clock floor above durable max, separate health clock, NewsPoller
   allowlist wiring, zero raw rows, and one EventStore writer.
4. Implement minimum clock/assembly helpers; blocking discovery remains outside the event loop or
   before it starts.
5. Focused GREEN, D4a endurance-contract checks, full suite, checkpoint commit.

## Task 5 — Ordered cycle coordinator

1. Add one failing orchestration test for the exact cycle trace: heartbeat/status → registry due →
   subject union → resolution poll → resolution dispatch → portfolio resolution state → ERS →
   execution dispatch → marks/evidence → status/sleep.
2. Implement a pure injected coordinator with one cycle at a time and explicit off-loop call seam.
3. Add serial REDs for subject deduplication, only-UNRESOLVED eligibility, UNAVAILABLE/UNKNOWN
   deferral, terminal exclusion, bounded outboxes, no proposals, and registry-expired gate.
4. Add failure-policy REDs proving market/source failures isolate while settlement, target,
   corruption, and unexpected orchestration failures propagate fatally.
5. Focused GREEN, ordering mutation probes, full suite, checkpoint commit.

## Task 6 — Supervised startup, readiness, and shutdown

1. Add one failing lifecycle test for startup trace: singleton → config/clocks/registry/stores →
   services → resolution recovery → resolution drain → execution drain → shard frame readiness →
   resolution state → controller boot → READY.
2. Implement the composite TaskGroup lifecycle and readiness gate; a service normal return is fatal.
3. Add serial REDs for recovery/readiness/controller reordering, finite readiness timeout,
   operator stop during RPC, writer close exactly once, reverse store close, lock release,
   non-overlapping cycles, and non-zero fatal exit.
4. Prove restart replays both outboxes before ERS and never compares persisted monotonic epochs.
5. Focused GREEN, supervision/restart mutations, full suite, checkpoint commit.

## Task 7 — Real production composition root

1. Add a failing construction test for real component types and connections: IntentStore,
   SafetyController and S4 seams, real `HermesPipeline`/MarketRegistry, Forecast/Component ledgers,
   two-provider `ResolutionFeed`, both dispatchers, Maker/Shadow ledgers, terminal-first marks,
   evidence/ramp objects, and `PaperSigner` only.
2. Implement `build_shadow_runtime` with injectable network/factory seams and explicit store
   ownership/closure. Do not import or accept a live signer, wallet, key, order, redemption, or
   chain-write client.
3. Add failing entry-point and systemd artifact tests for `Type=notify`, current unit extension,
   network/readiness/restart/timeout policy, stopped/disabled installer behavior, and config example.
4. Implement the composite CLI and deployment artifacts; retain ingestion-only CLI diagnostics.
5. Focused GREEN, no-authority-expansion/static import review, full suite, checkpoint commit.

## Task 8 — Whole-slice crash/restart test

1. Build one real-stack failing e2e using actual SQLite stores, `LocalBook`, registry, pipeline,
   controller, planner, both outboxes, both economic ledgers, resolution store/feed/dispatcher,
   marks, evidence, and injected provider observations.
2. Drive live book → proposal → ACCEPT and prove atomic audit/fill/flow/execution/outbox bytes.
3. Inject process/target failure after Maker apply and before acknowledgement; reopen every store,
   run startup recovery, replay idempotently into Maker then Shadow, and prove no duplicate risk.
4. Deliver a canonical terminal through POL-15; prove Forecast/Maker/Shadow terminal identity,
   retired controller risk, terminal-over-live mark, and final evidence.
5. Focused GREEN, full suite, checkpoint commit.

## Task 9 — Independent review and adversarial mutation gate

1. Perform an independent specification review against the live POL-17 contract, approved design,
   predecessors, and code diff.
2. Perform an independent security/authority review focused on signer exclusion, proposal isolation,
   stale-book authority, clock domains, SQLite ownership, RPC/event-loop separation, terminal
   authority, and systemd boundaries.
3. Fix every confirmed finding through a new serial RED → GREEN cycle; re-review each fix and run
   the full suite after every checkpoint.
4. Run an isolated mutation battery covering supervision, all startup/cycle ordering edges,
   restart, stale-book fallback, acknowledgement-before-apply, terminal precedence, controller
   bypass, eligibility bypass, atomic-journal splitting, duplicate collectors, and signer/authority
   expansion. Every mutation must be killed by a named test.
5. Rerun focused review suites and the complete canonical suite.

## Task 10 — Verification and reconciliation

1. Write `docs/VERIFICATION-POL17-ERS-HARNESS-RUNTIME.md` with RED/GREEN commands, checkpoint SHAs,
   full-suite counts, whole-slice trace, review findings/fixes, and mutation results.
2. Update `docs/HANDOFF.md` and `docs/TICKETS.md` with the reviewed build boundary and explicit
   stopped/disabled/no-deployment state.
3. Run compile, `git diff --check`, Markdown link checks, marker/pycache checks, sacred-surface diff,
   deployment artifact checks, and the complete suite from a clean working tree.
4. Update POL-17 in YouTrack with reviewed evidence only after the repository evidence is final.
5. Present exact reviewed SHA and separately gated choices. Do not push, merge, install, migrate,
   start, enable, or deploy.
