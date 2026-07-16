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

## Remaining gate

Do not install or retry from these checkpoints merely because tests pass. Required sequence:
independent specification/security review; adversarial mutations for usable-book readiness,
optional-seam compatibility, and shard blast radius; owner-authorized GitHub landing; stopped
service-checkout/config installation; exact preflight; then a fresh enablement observation. The
retry must prove non-empty fresh books and non-empty midpoint batches, bounded cgroup memory,
zero raw `clob-ws` persistence, healthy databases/outboxes, and a clean regular Hermes turn before
the services are left enabled.
