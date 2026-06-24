# Build tickets — YouTrack POL

Project board: <https://mysigner.youtrack.cloud/projects/POL>

Each ticket is self-contained (goal / build / acceptance / dependencies). Global context lives in
[`CONTEXT.md`](CONTEXT.md); the full design is in
[`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md).

| Ticket | Slice | Depends on |
|---|---|---|
| **POL-1** | EPIC — Autonomous Polymarket + Hermes trading bot | — |
| **POL-2** | S0 — Phase-0 verification + open decisions to finalize | — (do **first**) |
| **POL-3** | S1 — Ingestion layer + self-snapshotting Market-Memory DB | — (foundation) |
| **POL-4** | S2 — Signing + order-construction spike (**build-gating**) | S0 |
| **POL-5** | S3 — ERS skeleton + `pending_intents` + `propose_trade` | S2 |
| **POL-6** | S4 — Safety envelope + out-of-band supervisor + reconciliation + Telegram | S3 |
| **POL-7** | S5 — Calibration + base-rate prior + Anchor Gate | S1 |
| **POL-8** | S6 — Hermes integration + signal fusion + truth-gate | S3, S4, S5 |
| **POL-9** | S7 — Smart-money / insider detectors (defensive) | S1 |
| **POL-10** | S8 — Maker-rewards module (honest net accounting) | S3, S4, S7 |
| **POL-11** | S9 — Shadow harness + earn-autonomy ramp controller | S5, S6, S7, S8 |

## Critical path
`S0 → S2 → S3 → S4 → S6 → S9`, with `S1` feeding `S5`/`S7`, and `S7 → S8 → S9`.

**No real money** until **S4** is tested (kill path against a deliberately-wedged process) **and**
**S9** shadow proves calibrated, net-positive, out-of-sample results. If nothing clears its bar in
shadow → do not deploy.
