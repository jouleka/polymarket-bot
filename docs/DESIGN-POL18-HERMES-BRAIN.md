# DESIGN — POL-18: isolated propose-only Hermes brain

**Date:** 2026-07-15 · **Ticket:** POL-18 · **Status:** owner-approved contract

## 1. Goal and boundary

Deployable code and stopped deployment artifacts connect one dedicated Hermes profile to the
paper-only POL-17 runtime. The brain may inspect sanitized registry, live-book, resolved-ledger,
and conservative flag views, then enqueue an untrusted `PROPOSED` intent through the existing
`ProposeOnlyFacade`. It receives no database handle, key, wallet, signer, controller, shell, file,
browser, order, cancellation, settlement, process, or service-lifecycle authority.

POL-18 ends at a reviewed build. Creating the Linux user/group or Hermes profile, installing the
checkout or units, configuring a model/provider, creating cron state, starting/enabling either
service, opening production databases, and activation are separate owner gates.

## 2. Resolved architecture

| Fork | Decision |
|---|---|
| Runtime ownership | POL-17 remains the sole `IntentStore` writer and sole live-book owner. Hermes never opens SQLite and never starts a collector. |
| Inter-process boundary | A bounded Unix-domain request server runs inside the supervised POL-17 process. A separate stdio MCP bridge owns only a client capability to that socket. |
| Facade | The server composes the unchanged `ProposeOnlyFacade`; exactly five RPC/MCP names are exported: `propose_trade`, `get_market`, `get_book`, `get_ledger`, `get_flags`. |
| Host isolation | Hermes runs as dedicated unprivileged `polybot-hermes`; `polybot` and `polybot-hermes` share only a proposal-socket group. Hermes is not a member of the database/config owner group. |
| Profile isolation | A dedicated `polymarket` profile owns its config, model state, memory, sessions, skills, and cron. Existing default, coder, memecoin, and options profiles are neither cloned nor modified. |
| MCP implementation | Use the official Python MCP SDK at the version reviewed with installed Hermes 0.18.2. The bridge has no server-side store imports. |
| Tool enforcement | Configure only the dynamic `mcp-polymarket` toolset for the cron platform, disable MCP resources/prompts, and refuse startup unless an effective-inventory probe observes exactly the five names. |
| Model | Code and profile templates remain model-agnostic. The model/provider is selected and verified only at the separately approved stopped-install gate. |
| Schedule | Proposed default is one non-overlapping brain run every five minutes. Before profile/cron activation, the system remains genuinely idle; no fake production proposals are synthesized. |

```text
dedicated polybot-hermes user                    one POL-17 process (polybot)

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
concurrency, and a proposal rate limit. It never logs proposal thesis/citations or raw request
payloads.

The server binds atomically: remove only its own stale socket after proving it is a socket, bind a
temporary/private path, set group/mode, then publish the configured path. A non-socket collision,
wrong ownership/mode, bind failure, listener return, or lost listener is a supervised POL-17 HALT.
Client disconnect, malformed input, rejected proposal, stale book, or rate exhaustion isolates that
request. An unavailable POL-17 socket or MCP subprocess failure fails that brain run only; it cannot
alter ERS state or stop POL-17.

The endpoint does not accept until POL-17 has completed resolution recovery/outbox drains, observed
a live frame, booted the controller, and applied terminal/frozen state. Shutdown first stops new
requests, waits only for the bounded in-flight timeout, unlinks its own socket, then follows POL-17's
existing writer/store/lock closure order.

## 5. Hermes configuration and verification

The reviewed profile template contains one MCP server with absolute command/arguments, no shell
wrapper, no environment secrets, `tools.include` equal to the five names, and both resources and
prompts disabled. `platform_toolsets.cron` names only that MCP server/toolset. Native terminal,
file, web, browser, memory mutation, skills mutation, delegation, code execution, cron-management,
messaging, plugin, and future unknown toolsets must not reach the scheduled agent.

Because Hermes may add tools/plugins across releases, configuration inspection alone is
insufficient. A stopped preflight pins the supported Hermes version and constructs/probes the
profile's effective cron tool inventory. Any missing, extra, renamed, utility, or duplicate tool
refuses installation/activation. The same probe is an `ExecStartPre` condition for the brain unit.
Profile cron state is owned by its Hermes home, but the model receives no `cronjob` management tool.

The dedicated user owns only its Hermes home. It receives no `.env` from POL-17, no supplementary
`polybot` membership, no writable project source, no data-directory permissions, and no shell. A
dedicated shared group grants connect permission only to the Unix socket. The bridge command reads
root-owned/world-readable installed code and executes the project venv, but its module imports only
the MCP SDK, JSON/schema code, and socket client.

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

POL-17 may continue safely if the separate brain service is down. The brain service must remain
down if POL-17 is unavailable; it does not fall back to SQLite, persisted midpoint snapshots, or a
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
