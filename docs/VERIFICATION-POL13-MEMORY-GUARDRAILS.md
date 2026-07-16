# POL-13 shared-VPS memory guardrail evidence

Date: 2026-07-16 UTC

Status: hard memory and swap containment installed while stopped; no cron, database, service start,
enablement, or activation performed

Service checkout: `6e116aa` (PR #17 merge)

## Reason and host baseline

The owner requires Polymarket to coexist with other memory-sensitive VPS workloads and never consume
the host. Before this gate the 8 GiB VPS had approximately 4.50 GiB available RAM, 1.0 GiB swap in
use, and both Polymarket systemd units reported infinite `MemoryHigh`, `MemoryMax`, and
`MemorySwapMax`.

## Installed containment

PR #17 added explicit cgroup-v2 limits and stopped-activation checks:

| Unit | `MemoryHigh` | `MemoryMax` | `MemorySwapMax` | `OOMPolicy` |
|---|---:|---:|---:|---|
| `polymarket-ingestion.service` | 512 MiB | 768 MiB | 128 MiB | `stop` |
| `polymarket-hermes.service` | 320 MiB | 512 MiB | 128 MiB | `stop` |

The combined hard RAM ceiling is 1.25 GiB and combined swap ceiling is 256 MiB. `MemoryHigh`
pressures Polymarket for reclaim before the hard boundary; `MemoryMax` prevents either cgroup from
growing without bound. Bounded swap prevents a Polymarket memory excursion from becoming prolonged
VPS disk contention. The existing five-starts-per-five-minutes limit bounds failure restart loops.

The runbooks now require `MemoryCurrent`, `MemoryPeak`, swap, and `memory.events` inspection during
first start. Any OOM event, repeated `high` counter growth, or peak near the hard limit fails the
activation gate. Limits must not be raised merely to keep the service alive; universe or concurrency
must instead be reduced through a reviewed change.

## Verification

- Focused memory-contract tests observed RED before implementation and GREEN afterward.
- `systemd-analyze verify` passed both units.
- The canonical suite passed 2,276 tests.
- The stopped-only installer loaded the reviewed units and reported the exact byte values:
  - ingestion: `536870912`, `805306368`, `134217728`;
  - Hermes: `335544320`, `536870912`, `134217728`.
- Both units report `MemoryAccounting=yes`, `OOMPolicy=stop`, loaded, inactive, dead, and disabled.
- No Hermes cron job, proposal socket, production database, status file, start, enablement, or
  activation exists.
- The production data root still contains only `heartbeat` and preserved raw-firehose evidence; all
  four checksum entries pass.

Actual working-set and peak evidence necessarily begins at the separately approved first-start gate.
The installed hard ceilings already prevent that observation from exhausting the VPS.
