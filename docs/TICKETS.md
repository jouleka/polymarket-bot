# Build tickets — YouTrack POL

Project board: <https://mysigner.youtrack.cloud/projects/POL>

This file is a repository roadmap, not a substitute for the live tracker. Ticket states below were
reconciled with YouTrack and the repository on 2026-07-15. Each ticket is self-contained; global
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
| **POL-13** | D4a onward — shadow deployment work-stream | IN PROGRESS — stopped install/config plus native existing-Hermes profile/auth/model and exact five-minute cron complete; first-start exposed Gamma filtered pagination/omission behavior, corrected candidate `8e8e677` passes 2,285 tests, 9/9 mutations, and final independent re-review at PR head `f879e63`; both units stopped/disabled, Hermes never started, seven paper DBs preserved; merge/install/retry and later gates remain | POL-11, POL-12 |
| **POL-14** | D1 — MarketRegistry from Gamma metadata | DONE — landed on `main` via [PR #1](https://github.com/jouleka/polymarket-bot/pull/1); merged suite 1,482 passed; 64/64 required mutants and 19/19 bounded sweep pass with zero survivors; not deployed or runtime-composed | POL-13 ingestion substrate |
| **POL-15** | D2 — resolution / settlement feed | DONE — exact reviewed head `6dc9f6a` landed via [PR #3](https://github.com/jouleka/polymarket-bot/pull/3) as merge `5c4eb7b`; 2,070 tests and final specification/security/mutation gates pass with zero survivors; installed on the VPS after the 1,800-second storage gate, with service stopped/disabled pending later runtime composition | POL-13 substrate; Gamma/on-chain resolution sources |
| **POL-16** | D3 — shadow-execution and ledger wiring | DONE — exact reviewed head `89e1b6a` landed via [PR #5](https://github.com/jouleka/polymarket-bot/pull/5) as merge `4d1090c`; 2,121 tests; whole-slice crash/replay/settlement proof; independent review found and closed explicit-rounding and restart-integrity gaps; closing re-review and isolated 8/8 mutation gate pass with zero survivors; no runtime, deployment, or signing | POL-15 |
| **POL-17** | D4b — continuous ERS + harness runtime | DONE — reviewed branch head `c165971` landed through [PR #7](https://github.com/jouleka/polymarket-bot/pull/7) as merge `b6ae7e1`; 2,208 tests, independent specification/security PASS, 16/16 mutations; not installed, activated, or deployed | POL-14, POL-15, POL-16; POL-18 later attaches through propose-only transport |
| **POL-18** | deployed isolated propose-only Hermes brain | DONE — exact reviewed build head `5e57449` landed through [PR #9](https://github.com/jouleka/polymarket-bot/pull/9) as merge `09a3a6b`; native-profile correction landed through PR #15; 2,276 tests, independent specification/security PASS, 46/46 isolated mutations; existing-Hermes profile/model/auth and exact cron preflighted stopped, but no start/enable/activation | facade/MCP deployment; consumed by POL-17 runtime |

## Current build order

The owner-approved no-funding sequence is:

`POL-17 → POL-18 → POL-13 stopped install/activation gates → ≤2-week paper/shadow run → POL-4 live gate`

POL-15 is the evidence keystone: without resolution/settlement, forecasts and simulated fills never
become scored outcomes. POL-14 must land first because it supplies the real market/category metadata
the calibration and market-mapping paths require.

## Safety gate

No real money until the S4 kill path remains proven and the deployed S9 shadow produces calibrated,
net-positive, out-of-sample evidence. If nothing clears that bar, do not deploy live execution.
