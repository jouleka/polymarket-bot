# polymarket-bot

Autonomous 24/7 Polymarket trading bot. The **Hermes** agent is the reasoning brain; a
**deterministic Execution & Risk Service (ERS)** is the hands. There is **no human-in-the-loop
confirm** — deterministic guardrails replace it, with Telegram as notify + remote kill only.

**Status (2026-07-11):** the deterministic S1–S9 engine and corrected D4a ingestion/downsample
runtime are on `main`. POL-14's immutable Gamma `MarketRegistry` and fail-closed ERS integration are
implemented on the local landing branch; the current candidate passes **1,445 tests** and is awaiting
its final independent landing review. The VPS ingestion service remains **stopped and disabled**; the
full paper/shadow runtime, deployed propose-only brain, and live signer are not complete. Build
tracking is in YouTrack **POL**.

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

## Remaining build order

The pure S1–S9 components are built. The owner-approved remaining sequence is:

land `POL-14` MarketRegistry → `POL-15` resolution/settlement feed → `POL-16` shadow-execution
wiring → `POL-17` continuous ERS/harness runtime → `POL-18` isolated propose-only Hermes brain →
≤2-week paper/shadow run → `POL-4` live signing gate.

POL-4 is blocked on a funded wallet on a clean non-Windows machine. Nothing in the current runtime
signs or moves money.

Nothing touches real money until **S4 is tested** (kill path against a wedged process) and **S9 has
run a full shadow period** with net-positive, calibrated, out-of-sample results.

## Tracking
YouTrack project: <https://tracker.example.invalid/projects/POL>
