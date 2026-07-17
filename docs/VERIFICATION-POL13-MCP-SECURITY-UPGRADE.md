# VERIFICATION — POL-13 MCP security upgrade

Date: 2026-07-17 UTC

## Security scope

GitHub Dependabot alerts 1–3 identify three high-severity MCP SDK vulnerabilities. The affected
repository pin was 1.26.0; the highest patched floor is 1.28.1. The deployed Polymarket integration
uses only a capability-minimal stdio MCP server connected to a local Unix proposal RPC. It exposes
no MCP HTTP/WebSocket listener or experimental task API, so this is dependency hardening rather
than evidence of an exploited production path.

The change pins MCP 1.28.1 in project metadata, the stopped installer, and the fail-closed Hermes
profile verifier. It does not upgrade or recreate Hermes Agent 0.18.2, any profile, model,
authentication, cron state, or tool configuration.

## TDD and compatibility evidence

The first RED asserted the patched supported version and observed the old 1.26.0 constant. The
second RED asserted the stopped installer pin and observed its old 1.26.0 requirement. Minimum
changes made each focused test green. An explicit regression proves that 1.26.0 now fails the
effective-inventory verifier.

With MCP 1.28.1 installed in the development venv, 36 focused profile, deployment, MCP bridge, and
whole-slice tests pass. A temporary MCP 1.28.1 import overlay on the actual native Hermes 0.18.2
Python environment passed the installed profile's exact-five tool discovery and imported the
patched `ClientSession`. The overlay did not modify the running production environments.

The final canonical suite passed **2,338 tests** on MCP 1.28.1, and the environment dependency
compatibility check passed. Independent specification review passed after 62 focused tests and an
independent canonical tmpfs run of 2,338 tests. Independent security review passed after 61 focused
profile/deployment/real-stdio/RPC tests and direct confirmation that both production-venv dry runs
replace only MCP 1.26.0 with 1.28.1.

An isolated 5/5 mutation battery had zero survivors. Named tests killed a downgrade in project
metadata, stopped installer, native-Hermes runbook, and verifier constant, plus removal of the
fail-closed version comparison. Review also confirmed no MCP HTTP, WebSocket, SSE/Streamable HTTP,
or experimental task server is configured; resources and prompts remain absent and the exact-five
tools-only inventory is unchanged.

## Landing and stopped deployment

PR #39 landed as merge `26e2009`. Before maintenance, both services were active+enabled with zero
restarts and MCP 1.26.0 in both venvs. Hermes was stopped first, then POL-17; both reached
inactive/dead with `Result=success`. The service checkout fast-forwarded while stopped and the
installer left both units stopped+disabled. Each real environment then resolved and performed one
package replacement only: MCP 1.26.0 to 1.28.1. Hermes Agent remained exactly 0.18.2.

Both environment compatibility checks and the stopped exact-five preflight passed. Maintenance
preserved configuration SHA-256
`f42f99379627f441e1363a7976430ef8a81c979cb5382c6a62afa587ab499361`, native Hermes auth
SHA-256 `275e8a8c29728794104683627d818f6bb0d176b4d89263bc8780a869fa6e2fef`, all seven
database integrity checks, all four raw-firehose manifest entries, lock/config ownership, the
approved cron, and absence of profile-local auth/env files.

## Ordered restart and live result

POL-17 started at 15:53:57 UTC and reached fresh runtime/registry readiness with 144 authoritative
live-book tokens. Hermes started at 15:54:09 after its systemd exact-five preflight passed on MCP
1.28.1. Its first scheduled post-upgrade turn completed `ok` at 15:56:52.

At 15:57:29, both units were active+enabled with `NRestarts=0`, service swap zero, and all cgroup
`low/high/max/oom/oom_kill` counters zero. POL-17 current/peak memory was
252,772,352/424,222,720 bytes and Hermes was 280,162,304/283,459,584 bytes, within unchanged caps.
There were zero pending intents, fills, shadow executions, execution-outbox entries, shadow trades,
and raw `clob-ws` rows. All configuration/auth/evidence checksums and database integrity checks
still passed. No profile-local auth/env file appeared.

GitHub still reported alerts 1–3 open at 15:57 UTC because Dependabot had not yet rescanned the
post-merge manifest. At 16:18:24–25 UTC, the dependency graph detected MCP 1.28.1 and automatically
marked all three alerts fixed; none was manually dismissed.

## One-hour shadow checkpoint

At 16:53:40 UTC, the post-upgrade POL-17 invocation had run for almost one hour. Both services
remained active+enabled with `NRestarts=0`, service swap zero, memory-pressure averages zero, and
all cgroup `low/high/max/oom/oom_kill` counters zero. POL-17 current/peak memory was
293,462,016/424,222,720 bytes and Hermes was 287,621,120/289,554,432 bytes, within unchanged caps.
The latest scheduled Hermes turn completed `ok` at 16:50:32. One book read was rejected fail-closed
during a changing live universe; there was no service error or authority fallback.

All seven database quick checks passed. There were zero pending intents, fills, shadow executions,
execution-outbox entries, shadow trades, resolution subjects/assessments/terminals/outbox entries,
and raw `clob-ws` rows. Periodic midpoint batches and the full deduplicated data-API tape continued
to grow. The shadow remains safely idle until Hermes produces a genuine proposal; no fake production
proposal is synthesized for test coverage.
