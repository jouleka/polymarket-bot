# Build tickets — YouTrack POL

Project board: <https://mysigner.youtrack.cloud/projects/POL>

This file is a repository roadmap, not a substitute for the live tracker. Ticket states below were
reconciled with YouTrack and the repository on 2026-07-11. Each ticket is self-contained; global
context lives in [`CONTEXT.md`](CONTEXT.md), and the master design is in
[`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md).

| Ticket | Slice | Live state / repository reality | Depends on |
|---|---|---|---|
| **POL-1** | EPIC — Autonomous Polymarket + Hermes trading bot | OPEN — umbrella; its old “design phase” description needs reconciliation | — |
| **POL-2** | S0 — Phase-0 verification + finalized decisions | DONE | — |
| **POL-3** | S1 — Ingestion + self-snapshotting Market-Memory DB | DONE | — |
| **POL-4** | S2 — signing + order-construction spike | OPEN/BLOCKED — funded wallet required on a clean non-Windows machine | S0; owner funding/clean box |
| **POL-5** | S3 — ERS + `pending_intents` + `propose_trade` | DONE | S2 seam; current implementation remains paper-only |
| **POL-6** | S4 — safety envelope + supervisor + reconciliation + Telegram | DONE | S3 |
| **POL-7** | S5 — calibration + base-rate prior + Anchor Gate | DONE | S1 |
| **POL-8** | S6 — Hermes integration + fusion + citation truth-gate | DONE | S3, S4, S5 |
| **POL-9** | S7 — smart-money / informed-flow detectors | DONE | S1 |
| **POL-10** | S8 — honest maker net-accounting module | DONE | S3, S4, S7 |
| **POL-11** | S9 — shadow harness + earn-autonomy controller | DONE — components are built; the real shadow run is not | S5, S6, S7, S8 |
| **POL-12** | C2 — off-loop EventStore writes | DONE | S1 ingestion runtime |
| **POL-13** | D4a onward — shadow deployment work-stream | OPEN — downsample code/reviews/rate gate complete; broader build/deploy stream continues | POL-11, POL-12 |
| **POL-14** | D1 — MarketRegistry from Gamma metadata | OPEN — implemented locally; 1,445 tests and 25/25 mutation gate pass; final independent review and landing pending | POL-13 ingestion substrate |
| **POL-15** | D2 — resolution / settlement feed | OPEN — required for any scored shadow outcomes | POL-13 substrate; Gamma/on-chain resolution sources |
| **POL-16** | D3 — shadow-execution and ledger wiring | OPEN | POL-15 |
| **POL-17** | D4b — continuous ERS + harness runtime | OPEN | POL-14, POL-15, POL-16; brain proposal seam |
| **POL-18** | deployed isolated propose-only Hermes brain | OPEN | facade/MCP deployment; consumed by POL-17 runtime |

## Current build order

The owner-approved no-funding sequence is:

`land POL-14 → POL-15 → POL-16 → POL-17 → POL-18 → ≤2-week paper/shadow run → POL-4 live gate`

POL-15 is the evidence keystone: without resolution/settlement, forecasts and simulated fills never
become scored outcomes. POL-14 must land first because it supplies the real market/category metadata
the calibration and market-mapping paths require.

## Safety gate

No real money until the S4 kill path remains proven and the deployed S9 shadow produces calibrated,
net-positive, out-of-sample evidence. If nothing clears that bar, do not deploy live execution.
