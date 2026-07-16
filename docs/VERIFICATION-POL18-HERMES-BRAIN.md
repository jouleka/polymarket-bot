# POL-18 isolated propose-only Hermes brain verification evidence

Status: landed on `main`; independent specification/security review passed; stopped installation
and the non-enabled first-start observation passed; both services are stopped and disabled

Base: `8deee0459a61754bc33da4733d4efea6f59e764f`

Branch: `pol-18-hermes-brain`

Exact independently reviewed code head: `5e57449aabe43ac861e3570c373feb67937e3c92`

GitHub landing: [PR #9](https://github.com/jouleka/polymarket-bot/pull/9), merge
`09a3a6b18d9e393ce535c7612789586399d37feb`

Canonical suite at reviewed head: **2,273 passed**

POL-18 adds a capability-minimal Hermes proposal brain to the paper-only POL-17 runtime. POL-17
remains the sole live-book owner, `IntentStore` writer, ERS/controller authority, and shadow
execution process. Hermes sees exactly five tools through a bounded local socket and cannot open a
database, collect a second websocket, sign, size, price, submit, cancel, settle, or operate the
runtime.

## Owner-approved boundary

The owner approved the architecture and implementation after reviewing
[`DESIGN-POL18-HERMES-BRAIN.md`](DESIGN-POL18-HERMES-BRAIN.md). Work followed
[`PLAN-POL18-HERMES-BRAIN.md`](PLAN-POL18-HERMES-BRAIN.md) from baseline 2,208 tests.

Publication and merge were separately authorized after review. The stopped installer, service
checkout changes, Linux identities/groups, Hermes profile, model/provider, cron state, and service
activation remain separate gates. `/opt/polymarket-bot`, systemd, production databases, config,
raw-firehose evidence, and existing Hermes profiles were not changed.

## Implemented authority boundary

- `ProposalRpcServer` runs inside the supervised POL-17 process and composes the unchanged
  `ProposeOnlyFacade` plus sanitized registry, shared live-book, resolved-ledger, and conservative
  flag views. No second store or collector exists.
- The versioned one-frame Unix RPC accepts exactly `propose_trade`, `get_market`, `get_book`,
  `get_ledger`, and `get_flags`. It rejects duplicate/extra/missing keys, ambiguous framing,
  malformed Unicode, JSON floats, noncanonical decimals, oversized requests/responses, stale
  books, and unknown methods before invoking the facade.
- Admission is readiness-gated, concurrency/rate/timeout bounded, and tracked through shutdown.
  Handler escape, listener return, or an overdue synchronous dispatch without an acknowledgement
  HALTs supervision. Ordinary malformed or unavailable requests isolate one brain run.
- Socket construction binds a non-listening socket inside a private `0700` staging directory,
  verifies type/mode/group, listens, then atomically publishes it. Stale and shutdown cleanup is
  inode-identity protected.
- `mcp_bridge` imports only the MCP SDK and socket client capability. It exports strict schemas for
  the exact five tools and no resources, prompts, store, signer, controller, wallet, order, shell,
  file, browser, or service-management path.
- The stopped profile verifier pins Hermes 0.18.2 and MCP 1.26.0, validates authored and effective
  inventories, rejects any missing/extra/native tool, and pins manual approvals, cron denial,
  unsafe-skill denial, URL/secrets/Tirith/lazy-install settings, and hook auto-accept.
- The exact cron contract is one enabled five-minute `polymarket-propose-only` job with only the
  `polymarket` toolset. Model/provider placeholders fail activation; the owner must select both in
  a later stopped profile gate.
- The 2026-07-16 stopped-deployment correction replaces the originally reviewed separate
  `polybot-hermes` home with a normal `/root/.hermes/profiles/polymarket` profile in the existing
  Hermes installation. It uses native root-provider fallback with no profile credential copy; the
  exact-five verifier and systemd path restrictions preserve the authority boundary.
  `Requisite` refuses brain startup unless POL-17 is already active without
  pull-starting it; `PartOf` propagates explicit POL-17 stop/restart operations.
- The installer is idempotent but stopped-only. Before any mutation it requires ingestion
  `ActiveState=inactive, LoadState=loaded`; Hermes must be inactive and either loaded for a stopped
  rerun or not-found for first install. Missing ingestion, active/transitional/failed units, and
  unrecognized/error states refuse installation. The installer still leaves both units literally
  inactive and disabled and creates no Hermes profile.
- Before a separately approved profile/cron exists, the runtime remains genuinely idle with zero
  proposals. It never synthesizes fake production proposals.

`evaluate_intent`, `ProposeOnlyFacade`, caps, signer protocol, POL-15 terminal authority, POL-16
apply-before-ack semantics, and every signing/live-money boundary remain unchanged.

## Serial TDD and checkpoints

Production work and closing review fixes used focused RED, minimum implementation, focused GREEN,
canonical suite, then checkpoint commits:

`ea7288d`, `b426159`, `d581d3c`, `02232fa`, `b7e259f`, `015ecfb`, `f7878a7`, `9891f31`,
`bc85f76`, `90f24fc`, `3a339e6`, `a12db1b`, `82658bd`, `240a87a`, `15eb319`, `9574a94`,
`5e57449`.

The final deployment correction observed the intended RED: an absent first-install Hermes unit was
rejected. A first fix exposed a real cross-systemd ambiguity (`is-active` may print `inactive` for
an absent unit). The final RED/GREEN harness therefore models both `ActiveState` and `LoadState` and
pins the exact safe first-install and rerun matrices rather than relying on command text or exit
codes.

## Whole-slice restart proof

`tests/test_pol17_whole_slice.py` now crosses the actual POL-18 boundary while retaining real
POL-17 stores and authorities. It proves:

1. a stale brain `get_book` cannot propose, while a current shared `LocalBook` supports sanitized
   reads and one untrusted proposal;
2. ERS independently validates the stored proposal and a second live execution-book fetch remains
   mandatory before atomic shadow outbox creation;
3. Maker/Shadow projection preserves exact Decimal economics, including an injected Maker commit
   before acknowledgement and idempotent process-restart replay;
4. an injected resolution target failure retries through the durable POL-15 outbox, terminal state
   fans out to Forecast/Maker/Shadow, risk retires, and terminal value dominates marks/evidence;
5. disconnected partial clients, repeated socket/bridge restarts, stale validation/execution books,
   and non-five methods fail closed without duplicate proposal, economic, or terminal authority.

Separate lifecycle tests pin proposal-server supervision exactly once, readiness-before-admission,
bounded tracked-client drain, no fake proposal source, shared store/collector identity, and reverse
shutdown.

## Independent specification and security review

Both independent reviewers returned PASS at exact clean head `5e57449` and independently reproduced
the canonical **2,273-pass** suite. Specification-focused checks passed 90 cases; security-focused
checks passed 95 cases. Both also verified `bash -n`, compileall, diff cleanliness, clean worktree,
and unchanged sacred ERS/facade/caps/signer surfaces.

Review findings were closed and regression-pinned, including:

- private-before-public socket publication, private staging cleanup, and inode-safe replacement;
- immediate bounded admission, tracked handler drain, handler escape supervision, and no success
  acknowledgement after an overdue synchronous dispatch;
- strict request/response duplicate keys, framing, byte ceilings, correlation IDs, method schemas,
  Unicode, and canonical Decimal data;
- resolved-only bounded ledger queries and explicit stale-book rejection even when cached prices
  remain visible;
- exact authored, discovered, cron, and final model-visible tool inventories, including missing-tool
  rejection and every unsafe approval/security setting;
- profile ownership/mode/group checks, socket-only host capability, no inherited `.env`, and no
  ingestion pull-start;
- exact stopped installation state across real systemd absent-unit behavior while preserving safe
  idempotent reruns.

No reviewer found signer, wallet, order, cancellation, redemption, chain-write, database-write, or
runtime-authority expansion.

## Isolated adversarial mutation gate

At reviewed head, **46/46 required mutations were killed with zero survivors** in isolated
worktrees; every worktree was restored clean and removed. The prior 40 runtime/security mutations
plus the final six exact systemd-state mutations cover:

| Mutation family | Named killing evidence |
|---|---|
| missing/subset raw, effective, cron, or final model-visible tools | POL-18 profile exact-inventory tests |
| inline shell, unsafe security/approval fields, or bridge runtime-authority import | profile negative matrix and MCP import audit |
| stale book with cached values; pending-inclusive ledger query | book/read-view and ERS seam tests |
| duplicate keys, framing, request ceiling, or exact parameter-schema bypass | dispatcher boundary tests |
| response ceiling, duplicate response, wrong ID, or hanging endpoint | hostile-client and client-timeout tests |
| rate/readiness/concurrency/idle-timeout bypass | RPC admission tests |
| overdue synchronous acknowledgement or escaped-handler isolation | RPC supervision tests |
| tracked-client drain removal | deterministic preconnected-handler shutdown test |
| staged socket/mode/cleanup or inode/stale identity bypass | Unix publication and collision tests |
| proposal listener omitted, fake proposal synthesized, or early admission | root/runtime lifecycle tests |
| second execution-book fetch or real drawdown safety wiring removed | production planner/component tests |
| execution/resolution acknowledge-before-apply | POL-16/POL-15 crash-replay tests and whole slice |
| terminal-first mark precedence removed | terminal mark test |
| missing ingestion accepted; active/failed/activating Hermes accepted | behavioral systemd state-matrix test |
| safe first-install absent or stopped rerun Hermes rejected | behavioral systemd state-matrix test |

The detailed 40-mutant ledger additionally pins raw inventory missing, effective/cron subsets,
profile shell/security/approvals, service dependency, stale reads, pending rows, request/response
parser bounds, rate/admission/readiness, listener and handler supervision, staged publication and
identity cleanup, client correlation/timeout, root composition, ERS freshness, second-book safety,
drawdown wiring, both apply-before-ack paths, and terminal precedence. The final six mutations each
changed one `ActiveState`/`LoadState` outcome and were all killed by the behavioral installer test.

## Verification commands and results

Canonical full suite using the owner-provided tmpfs equivalent because the VPS has unrelated disk
contention:

```sh
rm -rf /dev/shm/pol18-final-suite
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol18-final-suite
```

Result at `5e57449`: **2,273 passed in 10.51s**. Independent reviewers reproduced **2,273 passed**
in 15.56 seconds and in their exact-head specification run.

Focused closing results: 90 specification/boundary/deployment cases, 95 security/deployment cases,
and the restored mutation battery all passed. Closing checks also passed: `bash -n
deploy/install.sh`, compileall, `git diff --check`, clean porcelain status, sacred-surface diff,
static no-authority import audit, exact deployment/profile artifacts, and isolated-worktree cleanup.

## 2026-07-16 first-start reconciliation addendum

The separately approved first Hermes start began at 14:21:56 UTC with POL-17 already active and
both units still disabled. The stopped preflight observed exactly five model-visible MCP tools and
passed. Live Hermes then exposed four configuration/runtime mismatches that the static verifier had
not pinned:

1. authored MCP-only `platform_toolsets` lists resolved the right MCP but triggered Hermes's
   native-tool validator warning on every reviewed platform;
2. the existing root Hermes environment supplied a Telegram token, so the new profile attempted to
   connect the already-running root bot and hit a token/PID collision;
3. the default gateway kanban dispatcher attempted to open the machine-global kanban database,
   which the systemd sandbox correctly kept read-only;
4. a bare systemd stop was classified by Hermes as unexpected, used the default zero-second drain,
   interrupted the in-flight cron turn, and exited status 1.

The gate failed closed. Hermes was stopped before POL-17, neither unit restarted or became enabled,
and no intent, fill, shadow execution, execution/resolution outbox, or terminal/economic row was
created. Seven database integrity checks remained `ok`; production raw-firehose evidence was not
changed. Hermes peaked at 291,999,744 bytes (278.4 MiB), below `MemoryHigh=320M`, with zero swap,
pressure, or OOM events.

The first independent security review of checkpoint `d4bd5ae` found two blocking gaps: Hermes
could re-enable several environment-driven adapters (including omitted platform/tool surfaces),
and the first planned-stop helper returned before systemd's main process had exited. Checkpoint
`cd5ca30` closes both findings:

- every explicit platform toolset list is empty, which installed Hermes 0.18.2 resolves to no
  native tools plus the sole globally enabled `polymarket` MCP;
- all pinned built-in and registered-plugin gateway tool surfaces/adapters are explicit, and
  effective preflight requires all 31 adapters disabled;
- a minimal-environment `execve` launcher strips inherited provider/messaging/relay/plugin secrets,
  while systemd hides root/project/managed env/config sources and profile-local auth/env refuses
  startup;
- kanban dispatch is disabled in both profile config and the unit environment;
- a 20-second drain remains within the existing 60-second unit stop budget; and
- a profile-scoped `ExecStop` helper requires Hermes's native planned-stop marker before SIGTERM
  and waits for the exact PID/start-time identity to exit before returning.

The installed Hermes resolver proof reports 31 disabled adapters and exactly the `polymarket` MCP
on all 33 reviewed surfaces. Focused profile/deployment tests pass 26/26; the complete suite passes
2,293 tests on tmpfs. The isolated hardening batteries killed 22/22 mutations with zero survivors:
the initial 12 covered toolset, messaging, kanban, drain, unit, marker-before-signal,
marker-failure, verifier-bypass, and extra-tool paths; the closing 10 covered omitted installed
platforms, effective-adapter bypass, inherited-environment leakage, profile-local secret sources,
sandbox source exposure, launcher bypass, missing synchronous wait, PID/start-time identity,
timeout headroom, and omission of the effective-gateway check from the installed preflight. This
addendum does not itself authorize installation, retry, enablement, or continued operation.

Independent closing re-review returned PASS at exact clean head `6f7e62e`: the reviewer reproduced
the 26 focused and 2,293 complete tests, the installed 31-adapter/33-surface inventory, hostile-env
rejection, native root `openai-codex` auth visibility, identity-aware bounded stop, systemd unit
verification, and absence of any signer/runtime/sacred-surface change.

Publication landed through [PR #24](https://github.com/jouleka/polymarket-bot/pull/24) as merge
`efead032d97f6ceae159fe743e7d6fd077a56db7` after explicit approval. The stopped service checkout
was then fast-forwarded to that merge, the idempotent installer ran, and the existing native
`polymarket` profile received the reviewed isolation template while retaining only its owner-selected
`gpt-5.6-terra` / `openai-codex` / ChatGPT Codex base URL and `high` reasoning values. No profile,
credential, cron, user, database, or Hermes installation was created. Stopped preflight again
reported `exact five; PASS`; all 31 installed adapters resolved disabled and all 33 surfaces resolved
to the sole `polymarket` MCP.

## 2026-07-16 hardened first-start observation

The separately approved retry ran without enablement. POL-17 started at 14:55:13 UTC, reached
`controller=RUNNING`, exposed the proposal socket as `polybot:polybot-proposal 0660`, and retained
healthy registry, resolution, execution-outbox, controller, and two-provider seams. Hermes started
at 14:55:36 after its installed exact-five preflight. Its gateway reported that no messaging
platforms were enabled; there was no messaging connection, token collision, invalid platform
toolset warning, kanban dispatcher/database access, profile migration, extra MCP server, or
credential/config error.

The automatic catch-up cron turn completed `ok` at 14:56:05 without a proposal. Three attempted
`get_book` calls used arguments that POL-17 rejected, after which Hermes's MCP circuit breaker
failed later calls closed; there was no alternate tool or authority path. This is an honest
no-proposal result, not a synthetic test input. The production stores remained at zero pending
intents, fills, Maker/Shadow rows, executions, execution/resolution outboxes, assessments,
terminals, and receipts.

Hermes peaked at 278,609,920 bytes (265.7 MiB), below `MemoryHigh=320M`; POL-17 peaked at
106,127,360 bytes (101.2 MiB), below `MemoryHigh=512M`. Both cgroups recorded zero swap, `high`,
`max`, `oom`, and `oom_kill` events and zero restarts. Hermes stopped first through the reviewed
marker → SIGTERM → exact-identity wait path in about two seconds; POL-17 then stopped gracefully.
Both units finished `Result=success`, `NRestarts=0`, inactive/dead/disabled, with no surviving
profile gateway or MCP process.

All seven production databases returned `PRAGMA integrity_check=ok`. Persistence remained compact:
five `clob-midpoint` rows, zero raw `clob-ws` rows, and the full deduplicated `data-api` trade tape.
Every historical raw-firehose checksum still matches. Final stopped profile preflight again passed
exact-five, and the single cron record now truthfully records its last run as `ok`. This observation
authorizes neither enablement nor a live-money path.

Post-stop transcript inspection showed that the `get_book` arguments were valid registry token
IDs; the catch-up run had started while `get_flags.live_book_tokens` was still empty, before the
shared websocket books became usable. PR #26 therefore narrows the exact cron prompt: an empty
fresh-book inventory ends the cycle, and Hermes may inspect only outcome tokens POL-17 advertises
in that inventory. The intended RED and focused 27-case GREEN were observed; the complete suite is
2,294 passing. This prompt-only correction adds no tool or runtime authority, and it was not used to
manufacture another production run.

## Deployment boundary

[`deploy/hermes/README.md`](../deploy/hermes/README.md) separates code/identity installation,
profile creation, model/provider selection, cron creation, activation, and enablement. This document
is build evidence, not operational authorization. Do not run the installer, change `/opt`, create
users/groups/profiles/cron, install units, start/enable services, open production databases, or
deploy without approval for that exact action.
