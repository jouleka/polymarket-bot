# DESIGN — POL-18: isolated propose-only Hermes brain

**Date:** 2026-07-15 · **Ticket:** POL-18 · **Status:** owner-approved contract; native-profile
correction 2026-07-16

## 1. Goal and boundary

Deployable code and stopped deployment artifacts connect one dedicated Hermes profile to the
paper-only POL-17 runtime. The brain may inspect sanitized registry, live-book, resolved-ledger,
and conservative flag views, then enqueue an untrusted `PROPOSED` intent through the existing
`ProposeOnlyFacade`. It receives no database handle, key, wallet, signer, controller, shell, file,
browser, order, cancellation, settlement, process, or service-lifecycle authority.

POL-18 ends at a reviewed build. Running the stopped code/identity installer, creating a Hermes profile, installing the
checkout or units, configuring a model/provider, creating cron state, starting/enabling either
service, opening production databases, and activation are separate owner gates.

## 2. Resolved architecture

| Fork | Decision |
|---|---|
| Runtime ownership | POL-17 remains the sole `IntentStore` writer and sole live-book owner. Hermes never opens SQLite and never starts a collector. |
| Inter-process boundary | A bounded Unix-domain request server runs inside the supervised POL-17 process. A separate stdio MCP bridge owns only a client capability to that socket. |
| Facade | The server composes the unchanged `ProposeOnlyFacade`; exactly five RPC/MCP names are exported: `propose_trade`, `get_market`, `get_book`, `get_ledger`, `get_flags`. |
| Host integration | `polymarket` is a normal named profile in the existing root Hermes installation. The custom stopped unit joins only the proposal-socket group and uses systemd path restrictions to hide production config/data, root SSH/Codex/config homes, and unrelated profiles. No second Hermes home, user, install, or login is created. |
| Profile isolation | `/root/.hermes/profiles/polymarket` owns its config, memory, sessions, skills, and cron. It uses Hermes's native read-only fallback to the existing root provider store. Existing default, coder, memecoin, and options profiles are neither cloned nor modified. |
| MCP implementation | Use the official Python MCP SDK at the version reviewed with installed Hermes 0.18.2. The bridge has no server-side store imports. |
| Tool enforcement | Configure only the dynamic `mcp-polymarket` toolset for the cron platform, disable MCP resources/prompts, and refuse startup unless an effective-inventory probe observes exactly the five names. |
| Model | The stopped production profile pins `gpt-5.6-terra`, `openai-codex`, and `high` reasoning. It reuses the existing root Hermes authentication through native profile fallback. |
| Schedule | Proposed default is one non-overlapping brain run every five minutes. Before profile/cron activation, the system remains genuinely idle; no fake production proposals are synthesized. |

```text
existing root Hermes installation               one POL-17 process (polybot)

Hermes polymarket profile
  cron agent, exact five tools
            │ stdio MCP
            ▼
polybot.hermes.mcp_bridge
  no DB/store/runtime imports
            │ bounded Unix socket; shared group only
            ▼
ProposalRpcServer ─► ProposeOnlyFacade ─► IntentStore.propose_trade
                          │
                          ├─► sanitized current MarketRegistry view
                          ├─► shared in-memory LocalBook view
                          ├─► bounded resolved ForecastLedger view
                          └─► read-only safety/readiness/flag view

POL-17 later: pending → validate → second live-book fetch → paper shadow only
```

## 3. Exact tool contracts

All wire numerics are canonical decimal strings; JSON numbers are rejected for price,
probability, size, and outcome fields. Unknown keys, duplicate JSON keys, non-finite values,
invalid Unicode/control characters, overlong strings/arrays, and schema-version mismatch fail
closed without invoking the facade.

### `get_market`

Accepts an optional exact `condition_id` or `token_id`, otherwise bounded `offset`/`limit`
pagination over the fixed selected universe. Returns only canonical event/condition/token identity,
outcome labels, question, category, seconds to resolution, and active/closed state. It reads the
current fresh registry generation; a stale or contradictory generation is unavailable, never
replaced by persisted data.

### `get_book`

Accepts one exact selected `token_id`. Returns best bid/ask and resting sizes plus midpoint as
decimal strings. Unknown, unsnapshotted, disconnected/stale, empty-sided, locked, or crossed books
return a typed unavailable error. The result is advisory: ERS independently reads the book during
validation and POL-16 fetches it again for execution planning.

### `get_ledger`

Accepts an optional reviewed category and a bounded `limit`. Returns resolved forecast/outcome
history only, newest bounded page, with exact stored probabilities, market mids, terminal identity,
resolution status/value, and timestamps. It exposes neither arbitrary SQL nor pending proposals.

### `get_flags`

Returns a read-only snapshot of runtime readiness, controller state, terminal/frozen conditions,
registry freshness, live-book availability, and detector capability. Until POL-9 supplies live
detector inputs, the view explicitly reports detector data unavailable with conservative
`FLAG_ONLY`; it never manufactures an `AVOID`/`FOLLOW` signal or trading permission.

### `propose_trade`

Preserves the existing facade signature and INSERT-only semantics. The brain supplies hypotheses
and suggestions, not authority. ERS independently re-derives/clamps price, size, calibration,
truth-gate, caps, breakers, and execution. Duplicate `intent_id` follows the store's existing
idempotent/conflict rules. No status, signer, order, controller, or settlement parameter exists.

## 4. Transport and supervision

The local protocol is versioned newline-delimited JSON with one request and one response per
connection. Requests carry `{version,id,method,params}`; responses carry either `{version,id,result}`
or `{version,id,error}`. The server enforces a fixed byte ceiling before JSON decoding, strict UTF-8,
duplicate-key rejection, exact method names and schemas, a short per-request timeout, bounded
admission, and a proposal rate limit. The timeout directly bounds incomplete wire reads and the
tracked-client shutdown drain. Facade/SQLite calls remain synchronous to preserve the sole-writer
thread; they are structurally bounded, and an operation that returns after the deadline receives
no success acknowledgement and HALTs the supervised listener. A kernel-level storage stall is not
preemptible by asyncio; the service will not acknowledge the request and requires external
supervision/operator recovery if it does not return. It never logs proposal thesis/citations or raw request
payloads.

The server binds atomically: remove only its own stale socket after proving it is a socket, bind a
temporary/private path, set group/mode, then publish the configured path. A non-socket collision,
wrong ownership/mode, bind failure, listener return, or lost listener is a supervised POL-17 HALT.
Client disconnect, malformed input, rejected proposal, stale book, or rate exhaustion isolates that
request. An unavailable POL-17 socket or MCP subprocess failure fails that brain run only; it cannot
alter ERS state or stop POL-17.

The endpoint does not admit proposals until POL-17 has completed resolution recovery/outbox drains, observed
a live frame, booted the controller, applied terminal/frozen state, and published systemd readiness. Shutdown first stops new
requests, tracks and drains/cancels admitted clients within the configured timeout, unlinks only its own socket, then follows POL-17's
existing writer/store/lock closure order.

## 5. Hermes configuration and verification

The reviewed profile template contains one MCP server with absolute command/arguments, no shell
wrapper, no environment secrets, `tools.include` equal to the five names, and both resources and
prompts disabled. Under pinned Hermes 0.18.2, every explicit `platform_toolsets` list is empty:
that is the supported no-native-tools selection, after which Hermes automatically layers the sole
globally enabled `polymarket` MCP server into the effective inventory. Naming only the MCP server
in those lists resolves the same tools but triggers Hermes's native-tool validator warning, so the
stopped preflight rejects that authored form even though the final inventory is still checked.
Native terminal, file, web, browser, memory mutation, skills mutation, delegation, code execution,
cron-management, messaging, plugin, and future unknown toolsets must not reach the scheduled agent.

Every built-in and registered-plugin gateway adapter in pinned Hermes 0.18.2 is authored as
explicitly disabled, and each corresponding tool surface has an empty native-tool list. Because
several Hermes adapters can override authored `enabled: false` from environment credentials, the
unit does not execute Hermes directly. A reviewed launcher constructs a minimal new environment,
retaining only locale, CA/proxy transport, and systemd metadata before `execve`; provider and
messaging secrets cannot be inherited. The sandbox additionally hides root, project, and managed
Hermes environment/config sources, while allowing only the existing root `auth.json` provider
store. Profile-local `.env`, `.op.env`, and `auth.json` files refuse startup. The stopped preflight
loads Hermes's effective `GatewayConfig` and requires all 31 pinned adapters to remain disabled.
Thus the gateway exists only to run the profile cron scheduler, and an omitted, added, or enabled
platform fails closed. The kanban dispatcher is disabled in both profile config and the unit
environment so it neither opens the machine-global kanban database nor starts worker authority. A
bounded 20-second agent drain fits inside the 60-second service stop timeout with Hermes's required
shutdown headroom.

Because Hermes may add tools/plugins across releases, configuration inspection alone is
insufficient. A stopped preflight pins the supported Hermes version and constructs/probes the
profile's effective cron tool inventory. Any missing, extra, renamed, utility, or duplicate tool
refuses installation/activation. The same probe is an `ExecStartPre` condition for the brain unit.
Profile cron state is owned by its Hermes home, but the model receives no `cronjob` management tool.

The native profile contains no `.env` or local `auth.json`; Hermes reads the existing root provider
store through its built-in named-profile fallback. No credential is copied into the profile or
POL-17. The stopped custom unit hides POL-17 config/data, root SSH/Codex/config homes, and every
unrelated profile, while `polybot-proposal` grants the expected Unix-socket route. The bridge
command executes the project venv, but its module imports only the MCP SDK, JSON/schema code, and
socket client. The exact-five effective inventory remains the primary authority boundary.

Hermes classifies a bare systemd `SIGTERM` as an unexpected failure so `Restart=on-failure` can
revive a crashed gateway. The custom unit therefore uses a profile-scoped `ExecStop` helper that
reads only Hermes's validated profile PID state, writes Hermes's native planned-stop marker, and
only then sends `SIGTERM`. A missing marker refuses the signal. The helper then waits up to 50
seconds for that exact PID/start-time identity to disappear before `ExecStop` returns, preventing
systemd from racing the drain with a second signal while retaining ten seconds of final manager
headroom. This preserves restart-on-crash while making an operator/systemd stop clean and
profile-local.

## 6. Failure policy

### Isolate one request or brain run

- malformed/oversized/unknown RPC input, invalid proposal, duplicate conflict, or rate limit;
- unknown/stale/unready book or registry item;
- model/provider/MCP subprocess timeout or Hermes cron failure;
- one client disconnect or response write failure.

These never mutate status, retry a proposal with a new ID automatically, or weaken validation.

### Halt/refuse POL-17 or brain activation

- proposal listener bind/permission/collision failure or normal listener return;
- effective Hermes inventory differs from exactly five;
- unsupported Hermes/MCP version, extra MCP server/resource/prompt, profile inheritance, or unsafe
  Linux ownership/group membership;
- protocol framing ambiguity, request handler escaping its isolation boundary, or facade/read-view
  construction without the real POL-17 components.

POL-17 may continue safely if the separate brain service is down. `Requisite` refuses brain startup
unless POL-17 is already active without pull-starting it, and `PartOf` propagates explicit POL-17
stop/restart operations. If POL-17 dies unexpectedly while the gateway remains present, every cron
tool call fails on the missing socket and cannot propose; this safer non-pulling tradeoff preserves
the separate activation gate. There is no fallback to SQLite, persisted midpoint snapshots, or a
duplicate collector.

## 7. Safety and acceptance invariants

1. Model-visible effective tools equal exactly the approved five at activation and cron execution.
2. `ProposeOnlyFacade` remains unchanged and `propose_trade` remains the only write.
3. Bridge imports and process capabilities contain no store, signer, controller, wallet, order,
   shell, file, browser, network-research, or lifecycle path.
4. POL-17 remains one process, one websocket collector, one `IntentStore` writer, and the only
   execution authority.
5. Stale persisted midpoint evidence never backs `get_book` or execution.
6. Exact Decimal data survives facade → RPC → MCP without JSON-float coercion.
7. Terminal authority, apply-before-ack recovery, S4 safety seams, and fresh-book execution remain
   unchanged.
8. Whole-slice tests cover proposal, validation, atomic shadow outbox, projection, failure/restart,
   resolution fanout, terminal marks/evidence, plus brain/socket restarts.
9. Independent specification/security review and an isolated mutation battery pass before the
   ticket is called reviewed.
10. No installation, profile/user creation, production migration, start, enable, or activation is
    implied by a passing build.
