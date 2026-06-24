# CONTEXT — read this first

Onboarding for any human or LLM joining this project. If you read one file, read this one, then the
spec at [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md).

---

## 1. What this is
A fully-autonomous, 24/7 Polymarket trading bot. An LLM agent (**Hermes**) is the reasoning brain; a
deterministic Python service (**ERS** — Execution & Risk Service) is the hands. **Deterministic
guardrails replace the human** (full autonomy, no confirm-loop); Telegram is notify + remote
kill/pause only. Operator is in **Albania** (legally clear to use Polymarket's offshore CLOB). v1
trades a **$100–500 test wallet**.

**Honest stance:** the null hypothesis is break-even-to-negative after costs. The system's first job
is to *not blow up* and to *prove a net edge in shadow* before risking more. If nothing clears its
bar in shadow → do not deploy.

## 2. The one-sentence safety model
Hermes is wired with **only read tools + exactly one write tool, `propose_trade(...)`, which does
nothing but INSERT a row into `pending_intents` with `status=PROPOSED`.** It never holds a key and
has no tool to sign, submit, cancel, or move funds. The deterministic **ERS** polls those proposals,
treats every field as an untrusted hint, re-fetches the live book, recomputes size itself, runs every
guardrail, and only then signs + submits — or vetoes. **Hermes proposes; the ERS disposes.**

> Why this is mandatory: Hermes's native approval gate covers **only dangerous shell commands** —
> **MCP tool calls are NOT gated**, and cron sessions disable interactive confirm. Any order-placing
> tool in Hermes's config *would* fire unattended with no prompt. Treat Hermes as a confused deputy
> whose worst possible action must be bounded by the ERS.

## 3. Verified facts (2026-06-24, adversarially checked)

### Polymarket
- **Venue:** offshore **CLOB** only — `https://clob.polymarket.com`, wallet-signed, gasless via the
  Polymarket relayer, settles on **Polygon**. (The US-regulated DCM has no API trading — not our venue.)
- **SDKs:** `py-clob-client-v2` (PyPI) / `@polymarket/clob-client-v2` (npm). Legacy `py-clob-client` /
  `@polymarket/clob-client` are **archived/non-functional**.
- **Collateral:** **pUSD** `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` — **NOT USDC.e** (migrated 2026-04-28).
- **Read APIs (no auth):** Gamma `gamma-api.polymarket.com` (discovery/metadata); CLOB `/book`
  `/price` `/midpoint` `/last-trade-price` `/prices-history`; Data API `data-api.polymarket.com`
  `/trades` `/positions` `/holders` `/leaderboard` `/activity`.
- **Real-time:** WS `wss://ws-subscriptions-clob.polymarket.com/ws/market` (public: `book`,
  `price_change`, `last_trade_price`); user channel is auth.
- **IDs (gets people every time):** `token_id` (decimal ERC-1155 → book/price), `conditionId` (hex →
  trades/holders), `proxyWallet` (→ positions). Gamma returns `clobTokenIds` / `outcomes` /
  `outcomePrices` as **JSON-encoded strings** — parse them; `[0]`=Yes, `[1]`=No.
- **Orders:** types GTC/GTD (limit) + FOK/FAK (market); sign EIP-712 order + L2 HMAC `POST /order`.
  Tick size + `min_order_size` are **per-market** — fetch live. 250 ms taker delay on some
  crypto/finance up-down markets.
- ⚠ **SIGNING PATH (build-gating, verify FIRST — POL S2):** plain EOA (`sigtype-0`) is **REJECTED**
  since 2026-04-28. Use the **Magic/proxy wallet** (sigtype 1/2) or the **official Rust client**
  (correct EIP-1271 wrapping). The `POLY_1271` / sigtype-3 path has an open bug. **VERIFIED 2026-06-24: the Python/TS V2 SDKs are broken for new deposit wallets (py `#70`/`#77`, ts `#64` open) and EOA/Magic-proxy are rejected — build the signer on the official Rust client `Polymarket/rs-clob-client-v2`; see [`VERIFICATION-2026-06-24.md`](VERIFICATION-2026-06-24.md).** The whole executor
  design depends on this — prove a tiny order signs+posts before building anything else.
- ⚠ **FEES (changed 2026-03-30):** broad taker fees now apply — ~$0.75 sports → $1.80 crypto per 100
  shares (`fee = C·0.03·p·(1−p)`, peaks at p=0.5). **Only geopolitics/world-events is free.** Makers
  pay 0 + earn rebates → **strong maker-only bias** for the whole system.
- ⚠ **RESOLUTION RISK:** UMA optimistic oracle; **1,150+ disputes in 2026**, DVM vote dominated by
  large wallets that often hold positions, multi-day capital lockups. ⇒ **"buy near $1 and hold to
  resolution" (bonding) is a TAIL-RISK trade, not the safe core.** Market-making is **not**
  automatically safe either — adverse selection bleeds you invisibly.

### Hermes
- NousResearch open-source **agent harness v0.17.0** (Electron desktop + `hermes` CLI + Python
  backend), **model-agnostic, NOT an LLM** (don't confuse with the Hermes 2/3/4 open-weight LLMs).
  Installed on the Windows box at `C:\Users\Admin\AppData\Local\hermes`; to be run on the VPS too.
- "Learns" purely via **context engineering on a frozen model**: `MEMORY.md`/`USER.md` notes injected
  at session start, `SKILL.md` procedure files it can self-write, an SQLite **FTS5 keyword** transcript
  search (no vectors by default). **No weights update.**
- Headless: `hermes -z "<prompt>"`. Full **MCP host/client** (`~/.hermes/config.yaml`). Built-in cron.
- ⚠ **MCP tool calls are NOT approval-gated** (open upstream issue). This is the reason for the
  propose-only chokepoint above.

## 4. v1 decisions (2026-06-24)
- **Strategy:** build **all** signal modules but keep everything in **SHADOW**; turn on live only what
  proves net-positive out-of-sample.
- **Capital:** **$100–500** hot wallet. Per-trade ~$25, **maker-only** to start.
- **Topology:** ERS + keys on the **same VPS as Hermes** (operator's choice; small blast radius at this
  capital) — **requires hardening:** separate Linux user / systemd service Hermes cannot `shell` into,
  **non-MAX ERC-20 allowances**, tiny hot balance, auto profit-sweep to a cold wallet, egress
  allowlist. Revisit a dedicated VPS before scaling capital up.
- **X/Twitter:** **skipped for v1**, news-only.
- **Autonomy is earned:** staged ramp — shadow → tiny-live → widen caps only while calibration holds.
  Ramp-**up** is human-gated; ramp-**down** is automatic.

## 5. Architecture in one breath
External feeds → deterministic **Ingestion** (normalize IDs, point-in-time timestamp, dedup, sanitize
untrusted content) → **Hermes** reads only the sanitized store; interprets news, maps reality → the
exact market, reads UMA resolution criteria, estimates `p` + thesis + citations → `propose_trade`
INSERT → **ERS** validates every field, re-prices off live book, sizes (¼-Kelly), enforces all caps +
correlation cluster gate, signs (sandboxed key), submits → fills/positions/PnL/resolution → **Outcome
Ledger** (read-only back to Hermes for next-session learning) → **out-of-band Supervisor** + Telegram
notify/kill. The ERS can always **veto or downsize** the brain; Hermes can never override a veto.

## 6. Build order — epic POL + S1–S9
| # | Slice | Depends on |
|---|---|---|
| S1 | Ingestion + self-snapshotting **Market-Memory DB** | — (foundation) |
| S2 | **Signing & order-construction spike** (build-gating) | — (do early) |
| S3 | ERS skeleton + `pending_intents` + `propose_trade` interface | S2 |
| S4 | Safety envelope + out-of-band supervisor + 3-way reconciliation + Telegram | S3 |
| S5 | Calibration + base-rate prior + Anchor Gate | S1 |
| S6 | Hermes integration (read tools + propose_trade) + signal fusion + truth-gate | S3, S4, S5 |
| S7 | Smart-money / insider detectors (**defensive** first; FOLLOW off) | S1 |
| S8 | Maker-rewards module (honest net-of-adverse-selection accounting) | S3, S4 |
| S9 | Shadow harness → staged ramp controller | all |

**Nothing touches real money until S4 is tested** (kill path against a deliberately-wedged process)
**and S9 has run a full shadow period** with calibrated, net-positive, out-of-sample results.

## 7. Landmines (read before writing code)
- **Never** let Hermes compute size or touch keys. `propose_trade` is its only write tool.
- **Always** re-fetch the live book before submitting; never trust a stale/proposed price.
- **Self-snapshot market data from day one** — Polymarket's `/prices-history` is lossy after
  resolution and order-book history is dead. You cannot backfill it later.
- Treat all scraped news as **untrusted data, never instructions**. Require **≥2 independent
  allowlisted primary sources** before any non-tiny size (indirect-prompt-injection defense). Refuse
  when a probability shift and a thin-book price move trace to the **same fresh source** (injection +
  pre-position signature).
- **Bonding / hold-to-resolution = tail-risky** (UMA disputes). **Maker ≠ automatically safe**
  (adverse selection). Account maker profit **net of mark-to-resolution of inventory**, in real time.
- **Correlation is a HARD pre-trade gate:** ten "independent" positions on one latent driver are one
  oversized bet (treat unknown correlation as +1). NegRisk legs net to one event-level bet.
- **WSL deployment:** run the daemon as a **system-level systemd service** (`/etc/systemd/system`),
  **not** `systemd --user` — the user layer breaks after WSL reboots (lesson from the optionsbot daemon).
- **Secrets** never in `MEMORY.md` / `SKILL.md` / env / logs / transcripts. Hash-pin deps + lockfile.

## 8. Links
- Full design spec: [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
- YouTrack project: <https://mysigner.youtrack.cloud/projects/POL>
- Research dossiers (Claude session on the Windows box): `C:\Users\Admin\.claude\projects\C--\synthesis_out.md`
  (Polymarket API + Hermes research) and `…\design_synthesis.md` (design).

## 9. Glossary
- **CLOB** — Central Limit Order Book (Polymarket's order-matching API).
- **CTF** — Conditional Tokens Framework (the ERC-1155 outcome shares on Polygon).
- **ERS** — Execution & Risk Service (the deterministic "hands"; sole key-holder).
- **NegRisk** — multi-outcome markets where outcomes are mutually exclusive (must be netted to one bet).
- **UMA** — the optimistic oracle that resolves markets (dispute/lockup risk lives here).
- **Kelly** — bet-sizing formula `f* = (p − price)/(1 − price)`; we use **¼-Kelly**, calibration-gated.
- **Brier score** — calibration metric over resolved predictions; gates whether the bot may size up.
- **Anchor Gate** — clamps how far Hermes's probability may deviate from base-rate + market price
  without independently-corroborated fresh evidence (anti-overconfidence).
- **propose_trade** — Hermes's only write tool; inserts a PENDING proposal, never an order.
