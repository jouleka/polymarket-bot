# POL-17 ERS + harness runtime verification evidence

Status: landed on `main`; independent specification/security review passed; the original delivery
was build-only. POL-13 later installed it and ran separately approved non-enabled first-start
attempts; both services are currently stopped and disabled, with every created database preserved.

Base: `22c0a21af5d8965311745237c3abf6175fd291b8`

Branch: `pol-17-ers-harness-runtime`

Primary mutation checkpoint: `8a37f34`

Exact independently reviewed code head: `f0166210511e57b21e68497289f7a8c800ecc327`

Reviewed branch head: `c1659719afaf7dc177059a708c8a2ee9e231370b`

GitHub landing: [PR #7](https://github.com/jouleka/polymarket-bot/pull/7), merge
`b6ae7e180ef64be2e1d71f3daa7f0fb1717f4c91`

Canonical suite at reviewed code head: 2,208 passed

POL-17 composes the existing D4a ingestion, POL-14 registry, POL-15 resolution, POL-16 shadow
execution, S4 controller, real Hermes pipeline, and S9 evidence harness into one paper-only
supervised process. One websocket collector owns the in-memory `LocalBook`; ERS and the execution
planner both read that live authority. Persisted midpoint batches remain evidence only.

## Owner-approved boundary

The owner approved the architecture and implementation after reviewing
[`DESIGN-POL17-ERS-HARNESS-RUNTIME.md`](DESIGN-POL17-ERS-HARNESS-RUNTIME.md). The implementation
followed [`PLAN-POL17-ERS-HARNESS-RUNTIME.md`](PLAN-POL17-ERS-HARNESS-RUNTIME.md) from baseline
2,121 tests.

Publication and merge were separately authorized after the reviewed build. At that landing,
installation while stopped and activation remained independent future gates, and neither had
occurred. The later POL-13 operations are recorded in the dated appendix below; raw-firehose
evidence remains preserved.

## Implemented runtime contract

- Exactly one `ShardedMarketCollector`, shared by midpoint persistence, ERS validation, anomaly
  health, marks, and the POL-16 second execution-book fetch; its raw websocket sink is `None`.
- Atomic paper ACCEPT transaction across intent decision/audit, internal fill, rolling-flow
  authority, canonical execution, and MAKER/SHADOW outbox rows.
- Resolution-gated ERS eligibility: only current `UNRESOLVED` results authorize processing;
  `UNKNOWN` freezes, `UNAVAILABLE` defers, and terminal dispositions retire risk.
- Startup barrier: singleton before stores; registry/config integrity; supervised services;
  Polygon 137 preflight and recovery; both outbox drains; live frame; restart portfolio rebuild;
  terminal state; then `READY=1`.
- Cycle order: heartbeat, fixed registry refresh/freshness, canonical subject union, serialized
  resolution poll, complete POL-15 drain, resolution state, ERS, complete POL-16 drain, evidence,
  atomic status.
- Reverse, all-attempt shutdown: stop RPC admission, join the serialized worker, drain/close the
  event writer, close every component, and release the singleton.
- Real `SafetyController`, flow gate, anomaly monitor, loss breakers, drawdown breaker, restart
  reconciler, startup caps/pUSD self-test, deterministic paper GTD, `PaperSigner`, real registry,
  resolution feed/dispatch, shadow dispatch, terminal-first marks, and harness evidence.
- Frozen fixed-universe Gamma generations with transient last-good use only inside the configured
  age budget.
- Exactly two canonicalized, independently named/hosted, timeout-bounded Polygon providers. The
  only RPC vocabulary is `eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`, `eth_getCode`,
  `eth_getLogs`, and `eth_call`.
- Seven distinct canonical database identities with symlink/hardlink alias rejection, one
  cross-store history clock floor, and explicit construction-failure cleanup.
- Atomic `/run/polybot/shadow-status.json` plus systemd `STATUS=` projection for controller,
  proposal/outbox depth, resolution dispositions, registry/news health, and advisory promotion
  evidence.
- Zero production proposals before POL-18; no fake or synthetic proposal source.

`None` remains backward-compatible for the existing optional journal, eligibility, clock, and
planner seams. `evaluate_intent`, `ProposeOnlyFacade`, caps, signer protocol, and the other sacred
surfaces were not modified.

## Serial TDD and checkpoints

Every production change began with an observed focused RED, followed by minimum implementation,
focused GREEN, the canonical suite, and a checkpoint. The branch history is intentionally granular:

`8852166`, `52f91b7`, `b8b6d80`, `a13caad`, `ee48a05`, `1d5dbec`, `72a09f9`, `350f411`,
`151e714`, `36cf7a3`, `9518c44`, `d34b94f`, `b262d50`, `d1a06e3`, `9254825`, `ae6a099`,
`b39a9ba`, `957cec1`, `c5ca353`, `4616043`, `99f21ad`, `e9aa232`, `7a06b7a`, `f8fe980`,
`4d4d913`, `580cc35`, `bdc1659`, `b1cb26e`, `4572afa`, `5791751`, `e69984b`, `496b5e1`,
`2fb987e`, `5a9e74b`, `2b07892`, `83f9621`, `3f79f64`, `585cb05`, `ce96570`, `b9d18ed`,
`ef64727`, `a9b9169`, `ef52f99`, `4a7f4ce`, `df98067`, `173ab1b`, `58b9517`, `8a37f34`,
`2e6e080`, `f016621`.

The mutation phase itself exposed one real late-construction leak: a pipeline binding failure after
all stores opened did not close them. The new RED reproduced the seven leaked handles; `construct`
now unwinds all opened stores in reverse order for any later constructor/binding failure.

## Whole-slice restart proof

`tests/test_pol17_whole_slice.py` uses real SQLite stores, a real `LocalBook`, real pipeline and
registry metadata, ERS/controller, PaperSigner, the canonical execution planner, Maker/Shadow
ledgers, resolution store/feed/dispatcher, marks, and harness evidence. It proves:

1. a live book and proposed intent pass the calibrated pipeline and deterministic ERS;
2. ACCEPT/audit/fill/flow/execution/two-role outbox persist as one transaction with exact Decimal
   economics;
3. an injected crash after Maker target commit but before acknowledgement leaves durable replay;
4. every store reopens, startup recovery replays without duplicate risk, and a target failure can
   retry through Shadow;
5. POL-15 terminal authority fans out into Forecast, Maker, and Shadow;
6. controller risk is retired, the terminal value dominates the live book, and final evidence uses
   the terminal mark.

Separate lifecycle tests pin the actual supervisor's recovery/drain/live-frame/boot/terminal/ready
ordering, readiness timeout, fatal service return, reverse closure, and singleton release.

## Independent specification and security review

Independent read-only specification and security reviewers found no signing or live-authority
expansion. Their confirmed implementation findings were each closed and regression-pinned:

- apply terminal restart state after, not before, portfolio rebuild;
- attempt every closer and always release the singleton;
- acquire the singleton before adapters, history scanning, or stores;
- preserve a still-fresh last-good Gamma generation on transient transport failure;
- serialize ResolutionStore ownership and join the worker before closing the store;
- create `/run/polybot` through systemd and preflight both providers on Polygon 137;
- wire the S4 GTD derivation and startup caps/pUSD self-test;
- reject canonical-path, symlink, hardlink, and provider-authority aliases;
- veto a production ACCEPT when the second live-book execution fetch cannot fill;
- stop admitting resolution RPC work after shutdown begins;
- floor the shared history clock across all seven stores;
- unwind partial root and component construction;
- replace no-op runtime status with atomic health/evidence output.

The reviewers also identified coverage gaps for service return/readiness, all resolution
dispositions, journal rollback/mismatches, duplicate collector scheduling, sequential tighten-only
resolution state, safety object identity, read-only RPC vocabulary, stale-generation behavior, and
late construction cleanup. All now have named tests in checkpoint `8a37f34`.

Closing re-review at documentation checkpoint `2e6e080` found three remaining lifecycle defects:

1. the stop predicate was checked only at provider-method entry, so one `observe()` could start
   later internal HTTP requests after SIGTERM;
2. production construction cleanup stopped at the first failing provider/Gamma closer and could
   leak the singleton; and
3. fatal shutdown closed the evidence writer before fencing and joining the resolution worker.

Each received an observed RED and minimum fix in `f016621`. The real provider now wires the stop
predicate through to `JsonRpcClient`, which checks immediately before every HTTP POST. Production
cleanup preserves the construction failure, attempts provider/Gamma/lock cleanup independently,
and aggregates cleanup failures. Fatal shutdown is now stop admission → worker join → writer drain
→ components/adapters → lock release. Both original independent reviewers re-read exact clean head
`f016621`, independently ran focused and canonical suites, and returned PASS with no blocker.

One inherited limitation is explicit rather than hidden: paper DORMANT restart rebuilds positions
and returns a new process to RUNNING but does not replay sticky HALTED/PAUSED op-state from
`op_audit`. This has no external authority under `PaperSigner`; durable op-state restoration is a
mandatory design gate before POL-4 may introduce a live wallet/signer.

## Isolated adversarial mutation gate

Thirteen mutations were applied one at a time to a detached tmpfs worktree at exact checkpoint
`8a37f34`. Every mutation was killed by its named test, reverted, and followed by a restored battery.

| Mutation | Killing test |
|---|---|
| supervised normal return raises the wrong failure type | `test_supervisor_treats_a_normally_returning_service_as_fatal` |
| `UNKNOWN` incorrectly authorizes ERS instead of freezing | `test_every_resolution_disposition_maps_to_exact_ers_authority` |
| disable the atomic precommit failure boundary | `test_injected_precommit_failure_rolls_back_journal_and_both_outbox_targets` |
| schedule the one websocket collector twice | `test_every_assembled_service_schedules_the_websocket_collector_exactly_once` |
| remove the real drawdown breaker from composition | `test_component_factory_wires_real_paper_safety_and_authority_types` |
| replace `eth_chainId` with `eth_sendRawTransaction` | `test_polygon_resolution_adapter_has_only_read_only_rpc_vocabulary` |
| bypass cleanup guarding around late pipeline binding | `test_component_construction_unwinds_all_stores_when_pipeline_binding_fails` |
| allow a later empty state update to unfreeze a position | `test_controller_resolution_state_only_retires_or_freezes_risk` |
| acknowledge POL-16 delivery before target apply | `test_dispatcher_crash_after_maker_commit_replays_then_reaches_shadow` |
| extend last-good registry authority beyond its TTL | `test_last_good_registry_fails_closed_after_its_age_budget` |
| apply terminal restart state before controller boot | `test_shadow_runtime_enforces_recovery_readiness_and_reverse_shutdown_order` |
| allow a phantom ACCEPT after the second live-book fetch cannot fill | `test_production_planner_vetoes_accept_when_second_live_book_has_no_fill` |
| bypass terminal-first mark authority | `test_mark_uses_live_midpoint_until_terminal_then_terminal_value_dominates` |

Result: **13/13 killed, zero survivors**. After restoration, all 13 named selectors (17 parameterized
cases) passed in 0.79 seconds; `git diff --exit-code` and porcelain status were empty. The detached
worktree was then removed. The active checkout was never mutated by this battery.

A second detached battery at `f016621` mutated the three closing fixes independently: it disabled
the per-HTTP-request stop check, stopped cleanup after the first closer error, and moved writer
close ahead of stop/join. All **3/3** were killed by their new named RED/GREEN tests. After
restoration those selectors passed 3/3, the detached worktree was clean, and it was removed.
Combined isolated result: **16/16 killed, zero survivors**.

## Verification commands and results

Canonical full suite, using the owner-provided tmpfs equivalent because the VPS has unrelated disk
contention:

```sh
rm -rf /dev/shm/pol17-pytest
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol17-pytest
```

Result at `f016621`: **2,208 passed in 7.30s**. Independent reviewers reproduced **2,208 passed**
in 7.37s and 7.79s respectively.

Focused mutation-coverage batch before the full suite: 45 passed. Latest cycle/adapter/component
batch: 23 passed. No raw CLOB persistence, network deployment, production data access, or service
operation occurs in these tests.

Closing verification passed: compileall; diff/whitespace and repository-local link checks; sacred
surface comparison against base; deployment artifact assertions (29 passed); mutation marker and
tracked-cache checks; independent re-review of every fix; clean porcelain status; and the complete
canonical suite. Documentation reconciliation changes no executable bytes.

## Deployment boundary

[`deploy/README.md`](../deploy/README.md) now covers the composite unit, stopped migration of the
preserved config, seven database identities, two read-only providers, validation that opens no
store, status/readiness checks, activation verification, and non-destructive rollback. The unit is
still inactive and disabled. The service checkout, production databases, raw-firehose evidence,
systemd state, and VPS configuration were not changed by POL-17 development.

This document is evidence, not operational authorization. The owner authorized PR #7 and its
merge only. Do not install, migrate, start, enable, or deploy without approval for that exact action.

## 2026-07-16 first-start Gamma reconciliation

The separately approved, non-enabled first-start gate exposed a live Gamma pagination behavior
that the hermetic adapter had not pinned. Startup selected 100 fixed markets, but the immediately
following filtered `/markets?condition_ids=...` refresh returned the endpoint's default 20 rows
because the request omitted `limit`. After adding the exact frozen-set limit, a live replay returned
99/100: Gamma omitted one requested, still-active esports condition even when queried alone. No
returned condition changed token identity and no extra condition appeared.

Five serial RED/GREEN checkpoints close that operational gap without weakening fixed-universe
authority:

- `a556d65` requires exact market and event limits on filtered bulk requests;
- `8bc7a47` classifies only a strict-subset response whose returned token identities all still match
  as `RegistryRefreshUnavailable`, retaining the prior coherent generation under the existing TTL;
- `ef16a66` performs that completeness/identity decision before candidate metadata construction, so
  quarantined metadata in the partial response cannot preempt last-good retention;
- `daf7df6` closes independent-review findings by validating returned event token identity before
  omission fallback and pinning that incomplete responses never renew the last-good age budget;
- `8e8e677` closes the subsequent re-review finding with one shared strict list-only token parser,
  preventing malformed market or event objects from masquerading as a two-token identity.

Expansion, replacement, duplicate/malformed identity, and omission combined with a token change
remain fatal `MarketSnapshotError` paths. The focused suites pass 17 tests; the complete suite passes
2,285 tests on tmpfs. An isolated 9/9 mutation battery killed removal of the market limit, removal
of the event limit, re-fatalizing a pure omission, masking a token contradiction as an omission,
constructing quarantined candidate metadata before detecting the omission, skipping event-identity
validation, renewing the last-good TTL on an incomplete response, and accepting malformed token
containers on either the market or event side.

The first independent specification/security review rejected the draft because omission fallback
ran before cross-snapshot event-token validation, and because TTL non-renewal lacked a regression
pin. Both findings were reproduced, fixed through the RED/GREEN checkpoint above, and mutation-
verified. The next re-review confirmed those closures but found the iterable-container parser gap;
that finding was likewise reproduced, fixed, and killed by separate market/event mutations.
The final independent closing review passed exact PR head `f879e63`: all three findings remain
closed, 38 focused registry/runtime/whole-slice tests pass, and no signing, authority, lifecycle,
or documentation regression was found. The independently reviewed executable head is `8e8e677`.
Both production services remained stopped and disabled throughout diagnosis and verification;
Hermes was never started, and no database or raw evidence was deleted or reset.
