# polymarket-bot

Autonomous 24/7 Polymarket trading bot. The **Hermes** agent is the reasoning brain; a
**deterministic Execution & Risk Service (ERS)** is the hands. There is **no human-in-the-loop
confirm** — deterministic guardrails replace it, with Telegram as notify + remote kill only.

**Status:** Design phase — no code yet. Spec approved 2026-06-24. Build tracked in YouTrack **POL**.

## Read first
1. [`docs/CONTEXT.md`](docs/CONTEXT.md) — everything a human or LLM needs to onboard (verified facts,
   decisions, landmines). **Start here.**
2. [`docs/specs/2026-06-24-autonomous-polymarket-bot-design.md`](docs/specs/2026-06-24-autonomous-polymarket-bot-design.md)
   — the full master design.

## The safety model, in one sentence
Hermes only ever calls **read tools + one write tool `propose_trade(...)`** that does nothing but
INSERT a *pending* proposal row. A separate deterministic service re-validates every field,
re-prices off the live book, re-sizes (¼-Kelly), runs all risk caps, and is the **only** thing that
ever signs or submits. **Hermes never holds a key and can never move money** — mandatory, because
Hermes's approval gate covers only shell commands and its MCP tool calls are *not* gated.

## Honest stance
The null hypothesis is that this system is **break-even-to-slightly-negative** after fees, spread,
slippage, capital lockup, and adverse UMA resolution. ~80% of traders lose; no open-source Polymarket
bot has a credible profit claim. **Its first job is to not blow up, and to prove a net edge in shadow
mode before risking more than a small test wallet.** If nothing clears its bar in shadow, the correct
outcome is *do not deploy.*

## Build order (epic POL + S1–S9)
`S1` ingestion + Market-Memory DB → `S2` signing spike (build-gating) → `S3` ERS + propose_trade →
`S4` safety envelope + out-of-band supervisor + reconciliation + Telegram → `S5` calibration +
base-rate + Anchor Gate → `S6` Hermes integration + signal fusion + truth-gate → `S7`
smart-money/insider detectors (defensive) → `S8` maker module (honest net accounting) → `S9` shadow
harness + ramp controller.

Nothing touches real money until **S4 is tested** (kill path against a wedged process) and **S9 has
run a full shadow period** with net-positive, calibrated, out-of-sample results.

## Tracking
YouTrack project: <https://tracker.example.invalid/projects/POL>
