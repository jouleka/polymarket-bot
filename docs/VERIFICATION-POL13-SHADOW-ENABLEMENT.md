# POL-13 shadow enablement gate evidence

Status: first enablement attempt failed closed on live-book readiness; corrective build complete and
awaiting independent review, landing, stopped installation, and retry

Date: 2026-07-16

## Authorized attempt

The owner explicitly approved enabling both paper/shadow services and beginning the bounded shadow
run. The service checkout was at `c8e55d576db077e72fc96b3f1a0e6cff1850ee6b`; installed Hermes
preflight reported the exact five approved tools. Both units were enabled, POL-17 started first,
and Hermes started only after POL-17 reported `controller=RUNNING` and exposed the proposal socket
as `polybot:polybot-proposal 0660`.

Hermes's catch-up turn at 15:18:51 UTC exercised the new prompt guard: `get_flags` returned an empty
`live_book_tokens` set, so the turn made exactly one tool call, returned `[SILENT]`, and completed
`ok`. The regular turn at 15:24:49 did the same. No proposal or fallback authority was used.

The gate did not pass. Six consecutive 60-second midpoint batches contained `{"books":{}}`, and
the fresh-book set remained empty after more than six minutes even though registry, Data API,
news, both Polygon providers, socket, status, and process supervision were otherwise healthy.
Leaving that runtime enabled would consume VPS memory without exercising the shadow loop. Hermes
was therefore stopped before POL-17 and both units were disabled. Both ended inactive/dead with
`Result=success`, `NRestarts=0`, and no surviving runtime, gateway, or MCP process.

Peak values observed before shutdown were 125,558,784 bytes for POL-17 and 255,905,792 bytes for
Hermes. Both cgroups recorded zero swap, `high`, `max`, `oom`, and `oom_kill` events. All seven
databases returned `PRAGMA integrity_check=ok`; intents, fills, shadow executions, and execution
outbox remained zero; raw `clob-ws` persistence remained zero; and every historical raw-firehose
checksum still matched.

## Root cause reproduction

With both services stopped, bounded public websocket probes used the same selected 200-token
universe and no persistence sink:

| Max tokens/shard | Shards | Present books | Stale books | Usable two-sided books |
|---:|---:|---:|---:|---:|
| 200 | 1 | 200 | 200 | 0 |
| 100 | 2 | 200 | 100 | 76 |
| 50 | 4 | 200 | 100 | 86 |
| 25 | 8 | 200 | 0 | 162 |

A one-token subscription reconstructed and verified normally. The protocol and Decimal book logic
were therefore intact. The failure was the production shard blast radius: one high-activity asset's
top-of-book divergence correctly requested a resync, but the one 200-token shard then marked every
book stale. Repeated shard-wide resyncs prevented any token from remaining execution-authoritative.
The existing safety behavior is correct; the unsafe configuration grouped too many independent
books behind it.

The second defect was readiness semantics. `ShadowRuntime` waited only for
`collector.last_frame_at()`. Full snapshots and subsequently divergent frames satisfied that
liveness check even though every `LocalBook.midpoint()` remained unavailable, allowing systemd
`READY=1` and proposal admission with no usable execution book.

## Corrective build

Three strict serial TDD checkpoints close the defects without weakening the safety rule:

- `3e96324` adds an optional, backward-compatible `live_book_ready` predicate to `ShadowRuntime`;
  production composition supplies a predicate that requires at least one non-stale, two-sided
  midpoint before controller boot, systemd readiness, or proposal admission.
- `27f7d89` changes the collector default from 500 to 25 assets per shard, bounding one divergence
  or disconnect to its 25-token shard.
- `b8ac430` changes the ingestion/config default and deployment example to the same exact value.

The intended readiness and shard-default REDs were observed. Closing focused results are 9
runtime/root cases, 14 sharding cases, and 84 configuration/build/sharding cases. The complete
suite passes 2,295 tests on tmpfs. No validator, propose-only facade, caps, signer protocol,
controller authority, persistence format, or live-money surface changed.

## Independent review and mutation gate

Two independent read-only reviews passed at exact clean head
`f18d48a64f670a5ffc89b4a7dc1c951fa53ede61` with no specification, security, deployment-boundary,
or authority-expansion findings. The reviewers independently reproduced focused results of 61 and
95 tests, the canonical 2,295-test suite, compile checks, clean diff checks, and systemd unit
verification. They confirmed that absent, stale, one-sided, crossed, and locked books fail closed;
the optional `None` seam remains backward compatible; shard failures remain bounded while systemic
supervision failures remain fatal; raw websocket persistence stays disabled; and no sacred
validator, facade, caps, signer, controller, execution, or resolution surface changed.

The isolated adversarial battery killed 8/8 mutations with zero survivors: bypassing usable-book
readiness, accepting a stale book, dropping production predicate wiring, restoring the collector
default to 500, restoring the runtime-config default to 500, restoring the deployment example to
500, making the optional seam mandatory, and accepting a one-sided book. The one-sided mutation
initially survived; strict TDD added the missing regression at `f18d48a`, after which it was killed.

## Remaining release gate

The reviewed correction is not installed. The service checkout and explicit production config
still use the prior build and `max_assets_per_shard = 500`. Required sequence: owner-authorized
GitHub landing; stopped service-checkout/config installation; exact loaded-config and Hermes
preflight; then a fresh enablement observation. The retry must prove non-empty fresh books and
non-empty midpoint batches, bounded eight-shard cgroup memory under the existing limits, zero raw
`clob-ws` persistence, healthy databases/outboxes, and a clean regular Hermes turn before the
services are left enabled.

## Stopped-install auth-isolation gate

PR #27 landed the reviewed live-book correction as merge `9df7c10`. With both units confirmed
inactive and disabled, the service checkout fast-forwarded to that merge, the idempotent installer
completed without activation, and the sole production config change set
`max_assets_per_shard = 25`. The loaded config reports seven distinct databases and the same two
approved Polygon provider IDs. All seven integrity checks, the historical raw-firehose manifest,
unit-file comparison, systemd verification, ownership, modes, and memory ceilings passed.

The exact-five Hermes preflight then failed closed because
`/root/.hermes/profiles/polymarket/auth.json` existed. Metadata dates its creation to 15:19:48 UTC,
60 seconds after the failed enablement attempt started Hermes. A structure-only inspection (no
credential values were printed) found one borrowed `nous` credential and no OpenAI credential.
Hermes 0.18.2 unconditionally starts its global Nous auth keepalive with a 60-second initial delay,
even though this profile selects `openai-codex`; the keepalive's global fallback then persisted that
unselected provider into the profile. No unit was started during this discovery.

Strict TDD checkpoint `369b477` routes the same existing profile through a small repo-owned
bootstrap. Before importing the Hermes CLI, it replaces only the unselected Nous keepalive starter
with a no-op, then executes the unchanged `--profile polymarket gateway run --replace` command. The
launcher still strips inherited authority variables and now supplies only the exact root-owned
repository `PYTHONPATH`. It does not create a profile, credential, cron, model configuration, tool,
or runtime authority. Focused tests pass 6/6, the canonical suite passes 2,296 tests, and an
installed-Hermes probe confirms the exact 0.18.2 entry point is disabled. An isolated 5/5 mutation
battery killed restoration of the keepalive, bypass of the bootstrap, loss of its exact source
path, loss of the exact named-profile command, and a production `os.execve` bypass of the reviewed
command builder.

The first specification re-review found that the initial test exercised command construction and
bootstrap behavior separately, so a direct-exec bypass at the production launcher could survive.
The closing regression invokes `launch_installed_profile`, intercepts `os.execve`, and pins the
Python bootstrap argv plus exact scrubbed environment; the proposed bypass now fails. It also pins
the incident-specific stopped cleanup commands below. Closing results are 29 focused cases and the
2,297-test canonical suite.

The services remain inactive and disabled. Before retry, this follow-up must pass independent
review and land; the service checkout must fast-forward while stopped; the generated forbidden
profile-local auth file must be removed without altering the native root auth store; and exact-five
preflight must pass. The live observation must run beyond the 60-second keepalive boundary and
prove that no profile-local auth file is recreated, in addition to the live-book, midpoint, memory,
database, outbox, and raw-persistence gates above.

## Corrected retry observation and second fail-closed stop

PR #28 landed the auth-isolation bootstrap as merge `5302d92`. The stopped service checkout
fast-forwarded to that merge. The recorded incident file was root-owned mode 0600, 4,854 bytes,
born and modified at 15:19:48 UTC, inode 2366575, SHA-256
`c624611d976ae730cceeddb3a3f63a232889dc47627617934b9fed80c9287f90`. Targeted cleanup removed
only that profile-local file. The native root auth checksum was identical before and after
(`50df4b431bb07151f2c09b043191e86258dbd4479dc8a48277343c8a744f829b`). Exact-five preflight,
loaded shard 25, both units, seven databases, and historical raw-firehose checksums then passed.

The approved retry began at 16:08:33 UTC. POL-17 satisfied usable-book readiness in about two
seconds, reported `RUNNING`, and advertised 149 fresh book tokens before Hermes started. New
60-second batches contained 170, 126, and 124 books; raw `clob-ws` rows remained zero. Hermes
started at 16:09:20 after exact-five preflight. Its catch-up used `gpt-5.6-terra` through
`openai-codex`, read `get_flags`, and created no proposal. The forbidden profile auth file remained
absent beyond the prior 60-second leak boundary and after shutdown, proving the keepalive guard.

The catch-up did not pass the useful-research gate. Two valid bounded `get_market` page requests
failed closed. A stopped live Gamma probe reproduced the exact cause: the fixed snapshot had 100
raw rows; row 96 raised `MarketMetadataUnavailable`, and `MarketReadView` aborted the entire page
instead of isolating the unusable row. Ordered shutdown exposed a second bootstrap integration
defect: the OS command line no longer looked like a Hermes gateway to its PID verifier, so
`ExecStop` could not identify the target and place the planned-stop marker. Systemd sent SIGTERM,
Hermes classified it as unexpected, and the unit ended `Result=exit-code` without restarting. No
process survived. The stopped failure state was reset only after diagnosis; both units are now
inactive, disabled, `Result=success`, and `NRestarts=0`.

The observation remained economically inert: pending intents, fills, components, Maker/Shadow
trades, shadow executions, both outboxes, assessments, terminals, and receipts are all zero.
POL-17 peaked at 114,552,832 bytes and Hermes at 265,347,072 bytes; both recorded zero swap,
`high`, `max`, `oom`, and `oom_kill`. All seven databases and every historical raw-firehose
checksum pass after the stop.

Strict TDD checkpoints `a391868` and `7a3b0b9` close the two defects. The bootstrap still executes
with Hermes's Python, but its OS `argv[0]` and inert profile/gateway/run tokens now satisfy the
pinned Hermes 0.18.2 runtime and profile identity checks, preserving marker-before-SIGTERM and
exact PID/start-time wait. The market page skips only rows whose event identity or registry
metadata is unavailable; an exact selector for that bad row raises `ReadViewUnavailable`, an exact
healthy row still succeeds, and registry freshness plus Gamma normalization remain outside the
isolation catch. A stopped probe returns 99 usable markets and the requested first 10. Closing
focused results are 48 tests and the canonical suite passes 2,305 tests. An isolated 10/10 mutation
battery kills loss of gateway identity/profile tokens, page-wide row failures, fail-open exact
condition/token lookups, unrelated-row impact, and swallowed freshness/normalization failures.
