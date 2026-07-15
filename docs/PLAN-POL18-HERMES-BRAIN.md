# PLAN — POL-18 isolated propose-only Hermes brain

**Design:** [`DESIGN-POL18-HERMES-BRAIN.md`](DESIGN-POL18-HERMES-BRAIN.md)

Every code task is one serial RED → minimum GREEN cycle. Observe the intended focused failure
before production edits, run focused GREEN, then the canonical full suite and make a checkpoint
commit. Do not accumulate unrelated failing tests or edit the validator, facade, caps, signer
protocol, settlement authority, or POL-16 durability semantics.

Baseline: `8deee0459a61754bc33da4733d4efea6f59e764f`, 2,208 tests passing. Branch:
`pol-18-hermes-brain`. Delivery boundary: reviewed build only; nothing installed or activated.

## Task 1 — Sanitized facade read views

1. RED: exact market pagination/detail over the current fresh fixed registry, with no raw provider
   rows and no stale-generation fallback.
2. GREEN: minimum immutable market view adapter using existing public registry/provider seams.
3. Serial RED/GREEN: live `LocalBook` decimal-string view and rejection of unknown, stale, empty,
   locked, and crossed books.
4. Serial RED/GREEN: bounded resolved-only ledger view and conservative flag/readiness view.
5. Prove injected `None` readers remain backward compatible; focused/full GREEN and checkpoint.

## Task 2 — Bounded Unix proposal RPC

1. RED: one strict versioned request invokes exactly one approved facade method and returns
   decimal-safe JSON.
2. GREEN: strict codec/dispatcher with exact schemas, duplicate-key rejection, unknown-key/method
   failure, byte/string/list bounds, and sanitized errors.
3. Serial RED/GREEN: async Unix listener lifecycle, readiness gate, concurrency/timeout/proposal
   rate bounds, socket type/ownership/mode, and request isolation.
4. Wire the server as an optional supervised POL-17 service with `None` preserving old behavior;
   listener return/failure halts while request failures isolate.
5. Prove clean shutdown stops admission and removes only its own socket; full suite and checkpoint.

## Task 3 — Exact-five-tool MCP bridge

1. RED: MCP discovery returns exactly the five approved tools with strict JSON schemas and no
   resources/prompts.
2. GREEN: stdio bridge using the reviewed official MCP SDK and the Unix client only.
3. Serial RED/GREEN: each tool maps one-to-one to RPC, typed failures remain failures, decimal
   strings remain strings, socket absence is bounded, and no server-side authority imports exist.
4. Add static public/import surface pins for no store/signer/controller/order/shell/file/browser
   paths; full suite and checkpoint.

## Task 4 — Hermes profile and effective-inventory verifier

1. RED: native profile template contains a single MCP server, exact include list, resources/prompts
   false, cron platform limited to the MCP toolset, and no clone/default-profile inheritance.
2. GREEN: version-controlled template, propose-only SOUL/skill/cron prompt, and stdlib stopped
   renderer/validator; no live profile writes.
3. RED/GREEN: fail-closed verifier pins Hermes 0.18.2 plus compatible MCP SDK and rejects any
   effective missing/extra/utility/plugin/native tool.
4. Prove the profile owns separate state/memory/cron and cannot mutate tools/cron from the model;
   full suite and checkpoint.

## Task 5 — Stopped deployment artifacts

1. RED: installer creates/validates dedicated nologin identity and socket-only group, never grants
   `polybot` group membership, leaves both services stopped/disabled, and never creates profile or
   production DB during a code-only install.
2. GREEN: additive brain unit/template and runbook with explicit stopped profile/config/model/cron
   gates and separate activation commands.
3. RED/GREEN: systemd ordering, restart/timeouts, no keys/env inheritance, read-only code, writable
   isolated Hermes home, proposal socket permissions, exact preflight, and rollback preservation.
4. Full deployment-contract suite and checkpoint. Do not run the installer.

## Task 6 — Whole-slice and restart evidence

1. Extend the real POL-17 whole-slice test through MCP/RPC: live book → five-tool reads → proposal →
   ERS validation → atomic shadow outbox → Maker/Shadow projection → resolution fanout → terminal
   mark/evidence.
2. Inject MCP/client disconnect, server restart, process failure, Maker target failure before ack,
   and restart replay. Prove no duplicate proposal authority, economic risk, or terminal effect.
3. Prove stale books reject at brain read and again at ERS/execution, terminal precedence survives,
   and the brain cannot invoke any non-five method.
4. Focused/full GREEN and checkpoint.

## Task 7 — Independent review and mutation gate

1. Independent specification review against live POL-18, approved design, POL-17, and repository
   evidence.
2. Independent security review of confused-deputy surface, host/profile isolation, tool inventory,
   protocol/parser bounds, Unix permissions, SQLite single-writer, stale-book authority, secrets,
   supervision, and no-signing/no-authority expansion.
3. Fix confirmed findings one serial RED/GREEN at a time and re-review each checkpoint.
4. Run isolated mutations covering extra/missing tool, native tool leakage, inventory bypass,
   method confusion, stale-book fallback, Decimal float coercion, payload/rate/timeout bypass,
   listener non-supervision, socket permission widening, direct DB access, facade bypass,
   apply-before-ack inversion, terminal precedence, and signer/controller import expansion.
5. Every mutation must be killed by a named test; rerun full suite.

## Task 8 — Verification and reconciliation

1. Write `docs/VERIFICATION-POL18-HERMES-BRAIN.md` with RED/GREEN commands, commits, suite count,
   whole-slice trace, reviews/findings, and mutation evidence.
2. Update `HANDOFF.md` and `TICKETS.md`; reconcile YouTrack only after final evidence.
3. Run compile, `git diff --check`, link/marker/cache checks, authority/import audit, deployment
   artifact checks, focused review suites, and canonical full suite.
4. Present the reviewed SHA and separate push/merge/install/profile/activation gates. Do not push,
   merge, install, create users/profiles, migrate, start, enable, or deploy without explicit scope.
