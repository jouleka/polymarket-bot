# POL-13 / POL-17 first-start verification evidence

Status: PASS; POL-17 was started without enablement, observed through three persistence intervals,
then stopped gracefully. POL-18/Hermes was never started. Both units are stopped and disabled.

Date: 2026-07-16 UTC

Installed GitHub `main`: `790ff765817e2099d7bff2b2f863f2c89b2aa5fc`

Reviewed executable fix: `8e8e677` through [PR #22](https://github.com/jouleka/polymarket-bot/pull/22)

## Pre-start preservation and containment

- The service checkout fast-forwarded cleanly to `790ff76`; only the expected untracked
  `config.toml` and `config.toml.pre-pol17` remain.
- The idempotent installer ran while both units were inactive and disabled and left them that way.
- Production config hashes remained exactly
  `5c085043547dcfa538e4ba9f86075eb013be3dd32c15e1f53cd3ac772c690f75` and
  `4d20478488130b4b95350c9ab0cc66a16229a450775ed5aa8ea3fffbbe0d346f`.
- All seven SQLite stores passed `PRAGMA integrity_check` before start.
- Every entry in the preserved raw-firehose `SHA256SUMS` manifest passed before and after the run.
- Installed memory limits remained 512 MiB high / 768 MiB hard / 128 MiB swap for POL-17 and
  320 MiB high / 512 MiB hard / 128 MiB swap for Hermes.

## Observed first start

Systemd began startup at `13:58:17`, published ready/running at `13:58:19`, and stopped cleanly at
`14:01:43`. The runtime remained active for about 204 seconds with `NRestarts=0`, exactly one
systemd `Started` transition, and no warning, error, traceback, wrong-chain, stale-registry, or
supervision-halt journal entry.

The live operational checks proved:

- both read-only Polygon providers returned HTTP 200 during chain-137 preflight;
- `/run/polybot-proposal` was `0750 polybot:polybot-proposal` and `proposal.sock` was
  `0660 polybot:polybot-proposal`;
- atomic status repeatedly parsed as controller `RUNNING`, no registry/news failure, zero pending
  intents, zero resolution/execution outboxes, and no promotion recommendation;
- heartbeat and status timestamps advanced throughout the observation;
- Hermes remained inactive/dead/disabled, so no production proposal was synthesized;
- memory stabilized near 87 MiB with a 107,700,224-byte peak, zero swap, and zero `low`, `high`,
  `max`, `oom`, `oom_kill`, or `oom_group_kill` events.

## Compact persistence result

The final `market_memory.db` contained 1,164 deduplicated rows:

| Source | Rows |
|---|---:|
| `clob-midpoint` | 3 |
| `data-api` | 1,000 |
| `bea-news` | 46 |
| `cftc-press` | 10 |
| `fed-monetary` | 15 |
| `fed-press` | 20 |
| `google-news-top` | 45 |
| `sec-press` | 25 |
| `clob-ws` | **0** |

The three midpoint rows span three 60-second persistence intervals. The database grew from
802,816 bytes before the successful run to 1,486,848 bytes afterward; this short diagnostic run is
not a replacement for the prior 1,800-second endurance evidence. No raw websocket frame was
persisted.

All seven stores passed post-stop integrity checks. Intent, forecast, component, maker, shadow,
resolution, execution, and both outbox tables remained empty. `op_audit` contains five historical
boot/reconciliation records accumulated across the bounded first-start attempts; they are
preserved, not reset.

## Failures closed before PASS

Three live-only integration defects were stopped, reproduced, fixed through strict TDD, reviewed,
mutation-tested, merged, and installed before this successful observation:

1. one live Gamma market had no market-owned deadline; PR #20 quarantined only that market while
   preserving fatal identity contradictions and the all-unusable halt;
2. systemd reapplied `RuntimeDirectory` ownership after `ExecStartPre`; PR #21 declared the
   socket-only group directly in the service identity;
3. Gamma filtered bulk requests defaulted to 20 rows and can transiently omit a requested market;
   PR #22 added complete limits plus TTL-bounded last-good retention for pure omissions only.

PR #22's final suite passed 2,285 tests. Independent specification/security review found and closed
cross-snapshot token masking, TTL non-renewal coverage, and malformed iterable identity containers.
The isolated 9/9 mutation gate has zero survivors. Extra/replaced/malformed identities remain fatal.

## Final boundary

The successful first-start observation does not authorize enablement, continuous activation,
Hermes first start, or live money. POL-17 and Hermes are both inactive/dead/disabled. Every paper
database and the raw-firehose evidence remain in place. The next separate owner gate is POL-18
first start with POL-17 running; enablement remains later.
