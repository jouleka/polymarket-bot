# Autonomous Polymarket Trading Bot — Master Design

**Status:** Design phase, no code. **Architecture pattern:** LLM synthesis brain (Hermes) + deterministic execution/risk service (ERS) + sandboxed secrets + human as kill-switch only. **Honest prior:** the null hypothesis is that this system is break-even-to-negative after fees/spread/slippage/lockup/adverse-resolution. ~80% of traders lose; no open-source PM bot has a credible profit claim. The design's job is to make **ruin essentially impossible** and keep the bot small and alive long enough to find the few modest accessible edges — not to chase size.

---

## 1. SYSTEM OVERVIEW

```
                          ┌─────────────────────────────────────────────────────────────┐
                          │  HOST A  (sandboxed, key-holding, NO inbound from Hermes)       │
                          │                                                                 │
  ┌──────────────┐        │   ┌────────────────────┐      ┌──────────────────────────┐    │
  │ EXTERNAL      │        │   │ INGESTION SERVICE   │      │  EXECUTION & RISK SERVICE │    │
  │ FEEDS         │        │   │ (deterministic)     │      │  (ERS — sole key-holder)  │    │
  │ • RSS/wire    │───────▶│   │ • normalize+dedup   │      │  • re-fetch live book     │    │
  │ • X/tweets    │        │   │ • point-in-time log │      │  • truth-gate citations   │    │
  │ • CLOB WS     │        │   │ • UNTRUSTED tagging  │      │  • portfolio-corr sizing  │    │
  │ • Data API    │        │   │ • sanitizer firewall │      │  • fractional Kelly       │    │
  │ • Polygon logs│        │   └─────────┬──────────┘      │  • ALL guardrails (veto)  │    │
  └──────────────┘        │             │ normalized      │  • EIP-712 sign + L2 HMAC │    │
                          │             │ event store     │  • pre-staged GTD exits   │    │
                          │             ▼                  └──────▲──────────┬─────────┘    │
                          │   ┌────────────────────┐              │ read     │ orders       │
                          │   │  pending_intents    │◀─ propose ──┐│ ledger   ▼              │
                          │   │  (status=PROPOSED)  │  _trade    ││   ┌──────────────┐       │
                          │   └─────────┬──────────┘  (INSERT)   ││   │  CLOB / chain │       │
                          └─────────────┼──────────────────────┐ ││   └──────────────┘       │
                                        │ poll                  │ ││                          │
        ┌───────────────────────────────┼──────────────────┐   │ ││  ┌──────────────────┐    │
        │ HOST B (Hermes box, NO KEYS)   ▼ read-only        │   │ ││  │ OUT-OF-BAND       │    │
        │   ┌───────────────────────────────────────────┐   │   │ ││  │ SUPERVISOR/WATCHDOG│   │
        │   │ HERMES (frozen-model harness)              │   │   │ ││  │ • own cancelAll    │   │
        │   │ read tools: news, book, ledger, flags      │───┘   │ ││  │   credentials      │   │
        │   │ ONE write tool: propose_trade(...)         │───────┘ ││  │ • hard-kill ERS    │   │
        │   │ → INSERTs a PENDING row, nothing else      │         ││  │ • dead-man switch  │   │
        │   └───────────────────────────────────────────┘         ││  └──────────────────┘    │
        └─────────────────────────────────────────────────────────┘│                          │
                                                                    │   ┌──────────────────┐   │
   HUMAN (Telegram: NOTIFY + KILL/PAUSE/FLATTEN/lower-caps only) ◀───┴──▶│ alerts + override │   │
                                                                        └──────────────────┘
```

**Hermes is necessary, not bolted on.** The only documented, repeatedly-verified *accessible* edges (maker rewards, resolution bonding) are mechanical and need no LLM — but they are thin yields against adverse-selection and dispute tails. The *judgment* tasks that have no API — reading messy UMA resolution criteria for traps, deciding whether breaking news is already priced, mapping fuzzy reality to a precise market question (where naive bots lose money), and interpreting whether an on-chain anomaly is real informed flow vs. wash/bait — are exactly Hermes's comparative advantage. Hermes is the brain that decides *which slow, judgment-heavy markets are worth a thesis at all*; the deterministic ERS is the hands that does all money, math, keys, and safety. Neither can do the other's job, and the ERS can always veto the brain.

---

## 2. DIVISION OF LABOR

**The interface (the entire safety model in one sentence):** Hermes's tool config exposes **only read tools + exactly one write tool, `propose_trade(...)`, which does nothing but `INSERT` a row into `pending_intents` with `status=PROPOSED`.** Hermes has no tool that can sign, submit, cancel, or move funds and never holds a key. This is mandatory because Hermes's approval gate covers only shell commands — **MCP tool calls are NOT gated and cron sessions disable `clarify`**, so any order tool in its config *would* fire unattended. The ERS polls `pending_intents`, treats every field as an untrusted hint, re-fetches live state, recomputes everything itself, and either signs+submits or rejects with a reason code. It never asks; it disposes.

`propose_trade(token_id, conditionId, event_id, side, target_price, max_price, size_usd_suggestion, p, p_confidence, resolution_summary, thesis, citations[])` — every field upper-bounded/untrusted; `size_usd_suggestion` is a request capped, never trusted upward.

| Pipeline task | Owner | Why (one line) |
|---|---|---|
| CLOB/Gamma/Data-API/WS/Polygon ingestion, normalize, dedup, point-in-time stamp | **Bot** | High-freq, schema-strict, latency-critical; Hermes must never touch a raw untrusted socket |
| Parse Gamma JSON-encoded `clobTokenIds`/`outcomes`/`outcomePrices`; assert `[0]=Yes,[1]=No` | **Bot** | One validated normalizer prevents Yes/No token mixups → wrong-side orders |
| Untrusted-content sanitizer (strip control/zero-width, delimit, spotlight) | **Bot** | Indirect prompt injection is live in-the-wild; firewall must sit outside the model |
| News/tweet/market interpretation; is it new info, already priced, what does it *mean* | **Hermes** | Pure NL judgment — the LLM's core value; output is text+hints, never an order |
| Market **mapping** (does reality match *this* market's exact question; semantic non-fungibility) | **Hermes** | Highest-value LLM task; where naive bots lose (Kalshi "arb" is a mirage) |
| Resolution-criteria reading (rules, source, void/50-50 clauses, ambiguity) | **Hermes** | Dense legal-ish text with traps; misreading resolution is a top loss source |
| Probability estimate `p` with confidence band + citations | **Hermes** | Subjective synthesis; treated as untrusted input gated by calibration |
| Scenario tree enumeration (past/present/future paths) | **Hermes** | Open-ended synthesis; bot enforces leaves sum to 1 + NegRisk netting |
| **Citation truth-gate** (source allowlist, N-of-M independent corroboration, book cross-check) | **Bot** | URL-fetchability ≠ truth; injection laundered through a fake catalyst is the master key |
| Calibration tracking (rolling Brier/reliability per category) | **Bot** | Pure math over the ledger; the model that produces `p` cannot grade itself |
| Smart-money/insider **feature** computation (fresh wallet, sizing, timing, clustering, p-value) | **Bot** | Statistical/graph compute over on-chain data; deterministic, repeatable |
| Smart-money/insider **interpretation** (informed vs wash/bait; follow?) | **Hermes** | Contextual judgment; may propose-follow via the same pipeline, never directly |
| Signal fusion → one proposed view | **Hermes** | Weighing heterogeneous qual+quant signals is synthesis; bot only validates/sizes |
| **Portfolio-correlation cluster assignment + sizing** | **Bot** | Red-team #1: correlation must be a HARD pre-trade gate, not a monitor |
| Fractional-Kelly sizing on executable price + all caps + liquidity cap | **Bot** | Money math must be exact, conservative, reproducible; never let LLM choose stake |
| Risk checks (bankroll/event/category/cluster caps, drawdown, lockup reserve, dispute haircut) | **Bot** | Safety-critical limits that *replace the human gate*; veto/downsize only |
| Order construction (live tick/min-size, rounding, GTC/GTD/FOK/FAK, book-walk, 250ms delay) | **Bot** | Microstructure-precise; wrong tick/min-size loses money silently |
| EIP-712 signing + L2 HMAC + POST /order + sig-path selection | **Bot** | Touches keys; sandboxed entirely off Hermes; only component that ever signs |
| Pre-staged GTD exit brackets at entry; out-of-band kill | **Bot + Supervisor** | Red-team #5: de-risking must never need a healthy brain at 3am |
| Throughput governor (~200/s), idempotency, dedupe | **Bot** | Deterministic infra; prevents self-DoS and double-fire |
| Maker-rewards quoting + **net-of-adverse-selection** accounting | **Bot** | Mechanical, but must book inventory loss, not just the reward line |
| Speed-gated NegRisk/intra-market arb (~21ms) | **Bot (or excluded)** | LLM in loop physically can't compete; pure deterministic micro-strategy or skip |
| Execution/fill/position monitoring + 3-way reconciliation | **Bot** | Continuous stateful truth; must be deterministic and always-on |
| UMA resolution/dispute monitoring (proposal, challenge window, DVM vote) | **Shared** | Bot watches on-chain + freezes; Hermes interprets dispute text, advises exit |
| Append-only audit log of every intent/decision/veto/fill/resolution | **Bot** | Tamper-evident, machine-written, for calibration + post-mortems |
| Self-review / learning (post-mortem, rewrite own SKILL.md, source weighting) | **Hermes** | Reflective NL learning; affects only its own context, gated downstream by caps |
| Strategy-param / risk-limit / cap changes | **Human** | Boundary of the safety system; LLM may recommend, human edits signed config |
| Kill / flatten / pause / lower-caps / blacklist | **Human** | Final authority via Telegram; obeyed deterministically, bypasses Hermes |
| Alerting / notification routing | **Bot** | Must fire even if Hermes is mid-session or stalled |

---

## 3. HOW EACH CAPABILITY WORKS

### 3.1 News ingestion
- **Bot:** Two-speed design. **FAST PATH** (1–5s conditional-GET long-poll) over a curated allowlist of *primary* feeds — AP/Reuters wires, White House/agency press RSS, CourtListener/PACER, BLS/BEA/FRED release pages, sports league feeds — plus a **calendar pre-stager** (cron arms connections seconds before scheduled FOMC/CPI/SCOTUS/election/game events so the bot is positioned at T-0, not reacting late). **SLOW PATH** = GDELT (verified 15-min cadence → demoted to discovery/backtest only, never a trade trigger) + one cheap aggregator (Mediastack ~$25/mo). NewsAPI.org rejected (localhost-only free tier, $449/mo paid). Every item → canonical envelope `{source, source_tier, event_id, observed_at(monotonic ns), published_at, content, entities[], market_links[], trust=UNTRUSTED}`.
- **Hermes:** Reads only the normalized, sanitized store; assesses credibility, novelty vs already-priced, which markets it bears on. Confidence: **high** (architecture), medium (that it produces alpha).

### 3.2 Tweets / X
- **Bot:** Tiered by trust+cost. **Tier-A** official X API pay-per-use (~$0.005/read, hard monthly credit ceiling), narrow filtered pulls of a **~50–150 account allowlist** (politicians, agency press, exchange orgs, sports insiders, key journalists). **Tier-B** cheap third-party vendors (twitterapi.io ~$0.15/1k, GetXAPI ~$0.05/1k, Apify) for breadth, marked **lower-trust**. Dedup by tweet_id. Verified: X killed flat tiers for new devs; Enterprise is ~$42k/mo — official firehose at scale is not affordable solo.
- **Hermes:** Classifies whether a tweet asserts a *checkable fact* about a tracked market, links to market, scores novelty. Confidence: **high** (cost/plumbing reality), low (sentiment alpha — heavily down-weighted vs hard catalysts).

### 3.3 Market data
- **Bot:** Three concurrent collectors. **(1) WS** `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribe `{assets_ids, type:'market', custom_feature_enabled:true}`, **shard ≤~500 assets/connection**, dedicated pong responder (<10s to 5s ping), maintain local books from `book`+`price_change`, emit synthetic liquidity-evaporation/large-print events, auto-reconnect with snapshot re-request, stamp `observed_at` at socket receive. **(2) Data API** polling `/trades /positions /holders /leaderboard` (positions ~150 req/10s). **(3) On-chain Polygon** watcher (Alchemy/QuickNode `eth_subscribe logs` on V2 exchange + ConditionalTokens ERC-1155) as tamper-proof ground truth. **Critical:** `/prices-history` is lossy post-resolution (~12h granularity) and `/orderbook-history` has been dead since ~Feb 2026 → run a **self-snapshotting Market Memory DB from day one**; never design around querying fine resolved-market history. Confidence: **high**.

### 3.4 Smart-money / profitable-wallet tracking
- **Bot:** **Two-layer model.** Discovery = `/v1/leaderboard` (no auth) seeds candidates — but **never size off leaderboard PnL** (mark-to-market, corrupted by auto-redemption which *deletes winners from /positions*). Truth = **realized PnL reconstructed from the immutable cash-flow ledger**: `PnL = ΣSELL + ΣREDEEM + ΣMERGE + ΣREWARD − ΣBUY − ΣSPLIT + current_market_value`, bucketed per conditionId, reconciled against on-chain CTF events (`PositionSplit`/`PositionsMerge`/`PayoutRedemption`). **Luck correction is the whole game:** hard gate on `N≥50–100` resolved bets AND one-sided binomial `p<0.001` that *edge (outcome − entry price)* beats the price-implied baseline AND bootstrap-CI excludes zero AND not single-event-dominated; otherwise `weight=0`. Classify `{SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE}`; exclude MMs (edge = uncopyable rebates). Cluster sybils by common Polygon funder. Real-time detection via RTDS `activity` topic (no auth, platform-wide fills), wallet-filtered service-side.
- **Hermes:** Narrative labeling ("why is this wallet sharp?"), regime-shift spotting, proposes weight changes (ERS clamps/applies). Confidence: **high** (PnL reconstruction), medium (that following nets positive).

### 3.5 Insider / informed-flow detection
- **Bot:** Six detectors, all from public read APIs, each emits a 0–1 sub-score → composite 0–10 with Low/Med/High/Critical bands. **D1** pre-news one-sided flow (VPIN-style toxicity, one-sided ratio ≥0.75, z≥2 vs market's own baseline). **D2** fresh/low-history confident concentrated wallet (conviction = `(size/walletValue)·(1−entry)·recency`). **D3** abnormal odds/volume move with **no public catalyst** (Hermes supplies the catalyst timeline to subtract). **D4** wallet clustering / coordinated entry (Polygon funding graph — clustering proves *coordination, not guilt*; the Theo 2024 case was pure skill). **D5** trade-timing vs news timeline (lead-time = public_ts − trade_ts). **D6** smart-money conviction (the FOLLOW side, screened hard for wash).
- **Hermes:** Establishes the authoritative "when did this become public" timeline; sanity-reads flagged wallets; may propose-follow.
- **Decision policy:** **FOLLOW / AVOID / FLAG-ONLY.** Verified legal reality: observing public on-chain flow and copying is *not* insider trading (the crime is the leaker's — Spagnuolo/Google May-2026, Maduro/Army-soldier Apr-2026); enforcement targets leakers, not followers. The genuine trap is downstream adverse-selection. **Default = AVOID/defensive + FLAG**: D1 toxicity directly feeds the maker module to widen/pull quotes (the one clearly +EV use). FOLLOW stays OFF until precision is empirically proven (it probably can't be). Confidence: **medium** (detection), low (that FOLLOW is +EV).

### 3.6 Past / Present / Future scenario reasoning
- **Past (Bot):** Self-snapshotting **Market Memory DB** (SQLite FTS5 to start) — resolved-market metadata+outcomes (Gamma), minute-bars recorded live, per-wallet ledgers. Reference-class **base-rate prior engine** (e.g. "incumbent re-election", "scheduled-Fed-hold", "favorite-by-spread"). Hermes retrieves analogues via FTS5 keyword search (no vectors).
- **Present (Bot):** Live fusion — walk-the-book executable price (not midpoint), WS odds, current flow per conditionId, decay-weighted news/social.
- **Future (Bot):** Catalyst calendar off Gamma `endDate` + external feeds; time-value/theta model (variance compresses toward resolution); enforces conditional probabilities sum to 1 + NegRisk netting.
- **The Anchor Gate (Bot, safety core):** Hermes's posterior is clamped by a max-log-odds-shift from BOTH the empirical base-rate prior AND current market price; hard deviation requires a fresh, deterministically-verifiable, **independently-corroborated** catalyst, else `p` is shrunk toward prior. This stops narrative runaway. Confidence: **high**.

---

## 4. THE TRADING ALGORITHM

### 4.1 Signal fusion → one calibrated probability
**Weighted log-odds (logit) ensemble with the market mid as prior** (so the crowd is never double-counted; each signal is a *delta vs mid*):

```
L_fused = logit(mid) + Σ_i w_i · clip( logit(p_i_cal) − logit(mid) )
p_raw   = sigmoid(L_fused)
p_final = Recalibrate(p_raw)        # final isotonic/Platt layer
```

Four signals, each **independently calibrated** on its own out-of-sample reliability curve before earning any weight:
- `p_news` (Hermes synthesis) — **least trusted**, hard-capped `w_news ≤ 0.25`, admitted only as a delta with mandatory **independently-corroborated** cited evidence (≥2 allowlisted primary sources for anything above tiny size), weight decays on calibration drift.
- `p_flow` (smart-money) — confirmation modifier, not its own bankroll.
- `p_micro` (book imbalance/spread/short-horizon drift) — small capped residual for timing/slippage-avoidance, never a thesis.
- `p_base` (reference-class base rate + longshot correction + time-decay).

Weights `w_i ∝ 1/Brier_i` shrunk toward zero, EMA-updated daily, **auto-zeroed if a signal stops beating just-quote-the-market**.

### 4.2 Bullish/bearish edge rule
Trade only if fused edge clears a stacked hurdle:
```
edge_buy  = p_final − ask ;  edge_sell = bid − p_final
H = half_spread
  + taker_fee(category)            # CORRECTED below
  + expected_slippage(walk book to intended size)
  + calibration_margin (k·√(p(1−p)/N_eff), k≈1.0–1.5)
  + adverse_resolution_premium     # per-category, larger for ambiguous/disputed
Go LONG Yes if edge_buy > H ; SHORT (buy No) if edge_sell > H ; else NO TRADE.
```
The `calibration_margin` term makes thin evidence (low `N_eff`) widen the hurdle so the bot won't fire on noise.

**Corrected fee schedule (red-team override — the brief was wrong):** as of **2026-03-30 Polymarket rolled out broad category taker fees**: sports $0.75, politics/finance/tech $1.00, econ/culture/weather $1.25, crypto $1.80 per 100 shares (`fee = C·0.03·p·(1−p)`, peaking at 50c — exactly where uncertain markets sit). **Only geopolitics/world-events remain free.** Makers still pay 0 + earn rebates. **This biases the entire system to maker-only execution.**

### 4.3 Fractional-Kelly sizing (on executable price, not mid)
```
f_full   = (p_final − price_exec)/(1 − price_exec)        # binary Yes-long; mirror for No
frac_eff = 0.25 · min(1, calib_score)                     # poor calibration auto-shrinks
stake    = min( frac_eff·f_full·bankroll,
                liquidity_cap,                             # ≤X% resting depth / ≤Y¢ impact
                per_position_cap, per_event_cap,
                per_category_cap, per_CLUSTER_cap,         # ← red-team #1
                total_deployed_cap )
```
**Portfolio-correlation cluster cap is a HARD PRE-TRADE GATE (red-team #1):** before sizing, assign the intent to latent-driver clusters (entity, asset, resolution-source, macro-theme, **+ a learned co-movement matrix from Market Memory snapshots**) and apply portfolio Kelly **treating unknown correlation as +1, not 0**. Ten individually-"safe" positions on the same latent driver are one oversized bet. NegRisk legs netted to one event-level mutually-exclusive bet. Kelly is the *requested* size; the smaller of Kelly and liquidity wins.

### 4.4 Maker vs taker
**Default MAKER** (post GTC/GTD limit at/just-inside fair value): earns spread + rebates + reward score `S(v,s)=(v−s/v)²·b`, doubles as a better entry. Use **TAKER** (FOK/FAK with explicit `max_price`) only for time-sensitive fresh flow, edge large enough to clear the full fee+slippage hurdle, or fast exit. Prefer maker on the 250ms-taker-delay crypto/finance up-down markets. **Never cross the spread to chase a thin edge that only existed at mid.**

### 4.5 Position management (continuous, rule-based)
Each position carries entry `p`, entry price, thesis snapshot, live re-estimate. **(1)** Re-evaluate `p_final` each cycle; broken-thesis cut if it crosses to the wrong side of price by >H. **(2)** Partial take-profit on edge-convergence (e.g. exit 50% at half-edge-captured). **(3)** Hard stop on broken thesis / MAE / max-loss — **never average down**. **(4)** Hold-to-resolution **only** for low-UMA-risk bonding (reclassified as *risky*, see §5). **(5)** Time-decay harvest near resolution. **(6)** Lockup-aware: never deploy capital that can't be locked to resolution given the total-deployed cap. **Pre-stage a GTD exit bracket at entry** so de-risking never requires a healthy brain.

---

## 5. AUTONOMOUS SAFETY ENVELOPE

The guardrails **are** the judgment — they replace the human confirm. Everything here lives in the deterministic ERS, fails **closed**, and cannot be overridden by Hermes. Default action under any ambiguity: **DO NOT TRADE + ALERT** (inaction is free).

**L0 — Secrets isolation.** Private key + L2 HMAC secret only in the ERS on **Host A**, a separate VM/host from the Hermes box (Host B). Small **hot balance**; auto-sweep realized profit above a threshold to a cold wallet the ERS cannot spend from; **bounded (non-MAX) ERC-20 allowances** topped in small increments so an exploit caps loss at the hot balance. Hash-pinned deps + lockfile + egress allowlist (red-team: a correctly-shaped malicious dep has total authority). **Signed caps config**, verified at startup. Secrets never in MEMORY.md/SKILL.md/transcripts/env visible to the model.

**L1 — Per-intent hard caps.** Per-trade notional (start $25), per-market, per-NegRisk-event (legs netted), per-resolution-source, liquidity (≤X% resting depth / ≤Y¢ impact). Every field of the intent re-validated; token_id/conditionId must exist and be tradeable; re-price from live book.

**L2 — Portfolio caps.** Per-CLUSTER (latent-driver, unknown-corr=+1), total-open-risk = Σ worst-case loss ≤ X% NAV, max concurrent positions (~10), **hard ceiling on capital locked-to-resolution** (a liquidity-reserve floor a multi-day dispute freeze cannot breach), **new-positions-per-unit-time cap independent of budget** (red-team #4: stops stacking 30 bets before the first resolves).

**L3 — Calibration circuit breaker.** Rolling Brier/reliability on the bot's own resolved predictions → multiplier `k∈[0,1]`; `k=0` (stop opening) when Brier worse than baseline or sample too small. **Forward-exposure gate (red-team #4):** cap total *unresolved* capital per category whose calibration sample is below min-N; cold-start categories are **paper-only**; require min resolved forecasts before `k>floor`. **Regime-shift detector** ratchets `k` down on live PnL-vs-expected *divergence*, not only on resolution. **Disputed-and-lost outcomes tagged separately** so a whale-captured vote doesn't poison Brier or auto-zero `k` for honest categories (red-team #2).

**L4 — Novelty/uncertainty veto.** Refuse-and-alert if: confidence below threshold; signals disagree; market category not yet validated; liquidity thin / spread wide; inside the UMA danger zone; intent references a market the ERS can't independently re-price; **or news-only with no independent corroboration**. Unfamiliar = alert, don't act.

**L5 — Anomaly kill-switch.** Immediate `cancelAll()` + halt-new on: UMA dispute/MOOV2 event on a held market; abnormal book (crossed/locked, midpoint jump, depth collapse, our order is the whole book); API error/5xx/auth storm; **clock skew >2s** (EIP-712/GTD depend on it — NTP/chrony, halt signing on drift); WS disconnect or fill-reconciliation mismatch; scheduled maintenance window; **periodic signing-canary failure** (sign+place+cancel a min-size order every N min — catches mid-session sig-path rot, never blind-retry).

**L6 — Watchdog + OUT-OF-BAND kill (red-team #5).** The kill path must NOT share fate with the trading loop. A **separate supervisor process** (ideally separate host) can (a) hard-kill the ERS, (b) **independently call `cancelAll()` with its own credentials**, (c) rely on **pre-staged GTD exit brackets placed at entry** so a wedged process still has standing venue-side exits. Watchdog **FLATTENS-or-protects**, not merely halts-new. **Acceptance gate: test the kill path against a deliberately-wedged process.** External dead-man's-switch on an independent host. Crash policy = HOLD (GTD self-expires resting orders); on restart, full **3-way reconcile** before trading.

**L7 — Real-time portfolio drawdown breaker (red-team #1).** Beyond realized daily-loss/consecutive-loss halts: if **total open *unrealized* exposure** moves >X% in <Y minutes, freeze adds + alert — *before anything resolves*. The existing realized/sequential breakers fire too late for a simultaneous correlated markdown.

**L8 — Telegram NOTIFY + remote KILL/PAUSE only.** Push every action/fill/reject/breaker/sweep + heartbeat. Inbound commands authenticated (allowlisted chat-id **+ signed rotating secret + nonces**), restricted to **safety-increasing** actions (KILL/PAUSE/RESUME/FLATTEN/lower-caps/blacklist) — **no "open trade" command exists**, so a compromised channel can at worst stop the bot. Trading loop never blocks on Telegram.

**Three-way continuous reconciliation (red-team #9):** internal ledger vs CLOB fills vs **on-chain ERC-1155 balances** (the only truth robust to the auto-redeem vanishing-positions quirk). Compute exposure caps against the on-chain-confirmed set. Any divergence beyond tiny tolerance → halt + alert.

### Earn-autonomy staged ramp
- **Stage 0 — SHADOW (paper):** log every intent + simulated walk-the-book fill **net of corrected fees + slippage + lockup opportunity cost + dispute/void haircut**. Accrue ≥150–200 resolved predictions/category; prove rolling **Brier beats the market-mid baseline** (not just 0.5), reliability slope ~1, and simulated net PnL **positive with margin**, out-of-sample/walk-forward with multiple-comparisons discipline.
- **Stage 1 — TINY-LIVE:** real key, micro caps ($25/trade), **maker-only**, most-defensible edges only.
- **Stage 2..n — RAMP:** caps widen by fixed steps **only** while calibration holds AND no breaker tripped AND **the tail was survived** (red-team #10: require ≥ some resolved *disputed* markets and ≥1 correlated-stress episode in the sample; tie max caps to a stress-test simulating a 100%-adverse co-move across the current cluster book staying within ruin limits). Any daily-loss halt or calibration/maker-net regression **auto-ratchets caps DOWN**. Ramp-up is human-gated; ramp-down is automatic. **Absolute non-loosenable hard ceiling on total bankroll-at-risk regardless of calibration.**
- Speed-gated ~21ms arb is **out of scope** for an LLM-orchestrated loop.

---

## 6. MASTER CASE CATALOG

Red-team must-fixes are marked **[RT]**. Severity: 🔴 catastrophic, 🟠 severe/high, 🟡 medium/low.

### Market / Resolution
- 🔴 **[RT] Adverse/whale-captured UMA resolution flips a "won" book.** 1,150+ disputes in 2026, DVM decided by top-10 wallets (~60% hold positions), $7M falsely resolved, $60M MSTR in live dispute, 4–6 day lockup. → **Invert the brief: bonding/hold-to-resolution are the RISKIEST strategies, not the safe core.** Hard-cap locked-to-resolution capital (reserve floor); refuse subjective-wording / high-voter-concentration / contested-source markets; tag disputed-lost separately in calibration; per-resolution-source cap.
- 🔴 **[RT] Correlated simultaneous overnight wipeout.** One macro event marks down a dozen "independent" positions at once. → Hard pre-trade cluster gate (unknown corr=+1) + L7 unrealized-drawdown breaker.
- 🟠 Market void / 50-50 / refund → carry a void branch in EV; don't size as pure binary.
- 🟠 Ambiguous/revised wording → snapshot+hash rules at entry; alert + re-evaluate on Gamma change; blocklist known-misread wording.
- 🟠 Illiquid/delisted/halted → pre-trade liquidity gate; exit-only state; size for hold-to-resolution.
- 🟠 NegRisk mis-netting → model exposure at event level; net legs; never let Hermes size legs independently.
- 🟠 Last-second resolution / pre-resolution blackout → stop new entries T-min before scheduled resolution; respect 250ms delay; marketable-limit only.

### Technical
- 🔴 **[RT] Signing path broken/unsupported.** Verified: **sigtype-0 EOA is REJECTED post-2026-04-28**; POLY_1271 sigtype-3 bug still OPEN (400 "signer address has to be the address of the API KEY"). Working paths today: **POLY_PROXY (Magic email wallet, sigtype 1/2)** or the **official Rust client** (correct EIP-1271 wrapping). → **Pick the signing path FIRST; it gates the whole build.** Abstract the signer; periodic canary; never blind-retry; ensure the exit path doesn't need a fresh sign during an outage (pre-staged GTD exits).
- 🔴 **[RT] V2 order-struct drift.** Struct changed 2026-04-28 (nonce/feeRateBps/taker/expiration removed; timestamp/metadata/builder added). → Pin SDK; assert domain/struct hash at startup; auto-HALT on any contract/struct/endpoint version change.
- 🟠 Signature/nonce/timestamp/clock errors → NTP/chrony, halt signing on drift; classify errors, back off, don't blind-retry.
- 🟠 Partial fills / order-state ambiguity → idempotent client IDs; reconcile against /order status + on-chain before acting.
- 🟠 Failed/stuck relayer settlement → track to confirmed tx hash with timeouts; reconcile CLOB fills vs on-chain ERC-1155.
- 🟠 WS disconnect / stale book → heartbeat + sequence-number; mark STALE, halt taking, resync via REST before resuming; always re-fetch live book before submitting.
- 🟡 Rate limits / throttling (POST /order ~5k/10s burst, throttled-not-rejected → silent stale data) → client-side token bucket well under ceiling; treat delayed responses as potentially stale.
- 🟠 pUSD approvals / wrong collateral → assert pUSD `0xC011a7E1…E82DFB` (NOT USDC.e) + sufficient bounded allowance to all spenders at startup.

### Financial
- 🟠 Slippage on thin books → walk the book for true fill before sending; reject if effective slippage > per-trade cap; marketable limits only.
- 🟠 **[RT] Fees/spread eat the edge (corrected schedule).** → Require modeled edge > full hurdle; **bias to maker**; track realized net PnL after ALL costs; auto-throttle net-negative strategies.
- 🟡 Capital lockup (worse with disputes: 4–6 days) → free capital is a sizing constraint; model time-to-resolution + dispute-lockup opportunity cost in EV; cap simultaneously-locked capital.
- 🟠 **[RT] Correlated/over-concentrated exposure** → cluster cap in sizing path (see above).
- 🔴 Drawdown/bankroll spiral → daily/weekly max-loss circuit breaker (deterministic), ¼–½ Kelly, calibration-gated sizing, size off current bankroll each cycle.
- 🟡 Bankroll fragmentation/dust → min-position floor; periodic dust sweep at resolution.

### Security
- 🔴 Private-key compromise → key only in ERS sandbox off Hermes box; deposit/proxy hot wallet with limited funds; sweep profits to cold; per-day spend cap below the app layer.
- 🔴 **[RT] Prompt injection laundered through a "numeric signal."** Attacker moves Hermes's `p` via a fabricated-but-fetchable citation (and can co-move a thin-book mid + place a bait fresh-wallet fill). → **Truth-gate: require source INDEPENDENCE + trust-tier, not just URL-fetchability**; ≥2 independent allowlisted primary sources for anything above tiny size; **refuse when the p-shift AND a thin-book mid-shift trace to the same fresh source/timestamp** (injection+pre-position signature); smart-money confirmation = ZERO-weight when originating wallet is fresh AND catalyst single-sourced; cap per-cycle/per-source-novelty exposure.
- 🟠 Malicious/typosquat dependency → hash-pin + lockfile + vendor critical deps + egress allowlist + least privilege; watch typosquats of py-clob-client-v2.
- 🔴 **[RT] Hermes ungated MCP tool calls** → expose only `propose_trade`; all caps/kill behind the ERS; assume Hermes is a confused deputy whose worst call is bounded.
- 🔴 VPS compromise / lateral movement → VM separation; signed caps verified at startup; off-host kill + cap-change alarm; mutual auth; no inbound to Host A except the needed channel.
- 🟠 Secret leakage via env/logs/transcripts → never in model-readable surfaces; secrets manager; redact logs; periodic scan.
- 🟡 **[RT] Telegram control-channel attack** → signed/rotating secret + chat-id + nonces; fail-safe halt if alerts down; dead-man's-switch on independent host; off-host kill credential.

### Model / AI
- 🔴 Hallucinated probability / fabricated source → truth-gate citations; sanity-bound `p` vs market price; reject intents whose evidence can't be fetched **and independently corroborated**.
- 🔴 Overconfidence / miscalibration → continuous Brier/reliability; calibration map shrinks toward market; gate sizing on calibration; ¼–½ Kelly only.
- 🟠 Model/regime drift, stale frozen knowledge → lean on fresh retrieval; monitor calibration drift; treat model as reasoning engine over fresh data, not a knowledge source.
- 🟡 Token-cost/loop blowup → hard hourly/daily cost circuit breaker; cap context; rate-limit invocations; break looping sessions.
- 🟠 **[RT] Poisoned MEMORY.md / self-written SKILL.md** → treat as reviewed code; **forbid trust-rules/trade-rules/secrets in model-mutable text** (ERS computes trust from data); version-control + audit; cold-start clean; hard caps override anything memory says.

### Operational
- 🟠 VPS reboot/crash, orphaned orders → supervised systemd **system-level** service (not --user — user's WSL lesson); on boot reconcile/cancel/adopt before trading; safe-HALT on unclean restart.
- 🟡 Clock/NTP desync → chrony + monitoring; halt signing past tolerance.
- 🟠 Monitoring/alerting gaps at 3am → Telegram notify on all state changes + periodic heartbeat + dead-man's-switch; page on critical (key, drawdown, version-change, settlement-stuck).
- 🟠 Audit/explainability → append-only immutable log of every intent (inputs+hashes, `p`+rationale, sizing math, book snapshot, signed order, fill, settlement).
- 🟠 Deploy/secrets/config drift → config-as-code under review; startup self-test (sign canary, verify contracts/collateral/allowances/caps) must pass before trading; staged rollout; refuse to start on sanity-check fail.
- 🟠 Backups/DR → encrypted offsite backup of seed (off Hermes box, off model-readable surfaces) + state/audit DBs; tested restore runbook.
- 🟡 Observability of net edge → per-strategy/cluster net-of-cost PnL + attribution; auto-throttle net-negative strategies.

### Strategy
- 🟠 Overfitting / backtest illusion → realistic fill model (walk historical book, fees/slippage/latency); out-of-sample + walk-forward; paper before capital; assume real edge < backtest edge.
- 🟡 Regime change → monitor live vs expected; auto-disable on sustained divergence; diversify across uncorrelated strategy types.
- 🟠 Latency disadvantage / front-run → don't compete in ~21ms arb with an LLM; target seconds-to-minutes edges only.
- 🟠 **[RT] Copying wrong/honeypot/spoofed wallet** → vet on reconstructed realized PnL + statistical gate; confirmation-only; copies pass the same gates; blacklist negative-post-entry-drift wallets; cluster by funder.
- 🟡 **[RT] Insider-signal traps + legal/association** → detect+notify always; auto-follow OFF for v1 (defensive use only); legal/ToS review before ever enabling.
- 🟠 **[RT] Maker adverse selection (the "safe" strategy bleeds invisibly).** Break-even-adverse-move = `daily_reward/order_size` (tiny). → Account rewards **net of mark-to-resolution of accumulated inventory**, in real time; wire D1 toxicity to a **HARD pull-quotes** trigger; stop quoting a market when recent adverse move exceeds break-even; **L8 ramp requires positive net-of-cost PnL, never reward-gross**; do NOT default maker-only as "safe."
- 🟠 **[RT] Determinism-as-predictability** → bounded non-determinism (randomize size, jitter timing, vary windows); keep thresholds secret; prefer maker; cap size vs depth.

---

## 7. PROPOSED BUILD DECOMPOSITION

Build order chosen so the **first slice is shippable and shadow-testable** before any capital, and so safety infra precedes anything that can lose money.

**S1 — Ingestion + Market Memory DB (foundation, build first).** Self-snapshotter from day one (CLOB WS sharded collector, Data API poller, Polygon log watcher), canonical-envelope normalizer with the validated Gamma JSON-string parser, dedup, point-in-time `observed_at` log, untrusted-content sanitizer. *Independently testable: replay fidelity, no-lookahead, Yes/No mapping unit tests.* **Nothing downstream is trustworthy without this.**

**S2 — Signing & order-construction spike (de-risk the build-gating unknown).** Prove a tiny POLY_PROXY (or Rust-client) order signs and is accepted; assert V2 struct shape; abstract the signer. *Until this works, the rest is academic.*

**S3 — Execution & Risk Service skeleton + `pending_intents` + propose_trade interface.** The chokepoint, the cap config (signed), the validator that re-prices/re-sizes/vetoes, idempotency, audit log. Wired to S2. *Testable with synthetic intents, no Hermes yet.*

**S4 — Safety envelope: L0/L5/L6/L7/L8 + out-of-band supervisor + 3-way reconciliation + Telegram.** Build the kill path and test it against a deliberately-wedged process (acceptance gate) **before** any live capital.

**S5 — Calibration + base-rate prior engine + Anchor Gate (L3/L4).** Brier/reliability ledger, reference-class priors, deviation clamp.

**S6 — Hermes integration (read tools + propose_trade only) + signal fusion + truth-gate citations.** Now the brain proposes; the ERS disposes. Cluster-correlation sizing gate.

**S7 — Smart-money/insider detector service (defensive first).** PnL reconstruction, statistical gates, RTDS watcher, D1 toxicity → maker pull-quotes (AVOID/FLAG). FOLLOW stays OFF.

**S8 — Maker-rewards module with honest net-of-adverse-selection accounting.** The most defensible profit center — but only ships live if TOTAL net is positive in shadow.

**S9 — Shadow harness → staged ramp controller.** Drives S0→S1→S2 of §5. Everything runs paper here for the full Stage-0 period.

*Optional/excluded:* speed-gated NegRisk arb (pure deterministic micro-strategy or skip — out of scope for the LLM loop).

---

## 8. OPEN DECISIONS FOR THE USER

These are the few things only you can decide; they define the guardrails that replace the human gate and must be concrete numbers before go-live.

1. **Signing path (gates the whole build):** POLY_PROXY (Magic email wallet, sigtype 1/2, Python stack) **or** build the executor on the official **Rust client** (correct EIP-1271 wrapping) with Hermes calling it as a sandboxed service? sigtype-0 EOA is *not* a fallback (rejected). **Decide first.**
2. **Box topology:** concrete plan for Host A (sandboxed key-holder) vs Host B (Hermes), given your WSL-on-Windows reality and the documented cracked-game malware vector on the Windows box. Recommend the ERS+keys on a clean dedicated VPS, Hermes anywhere else, secrets never on the Windows box.
3. **Primary strategy to build first** (recommend: **maker-rewards with honest net accounting + resolution-bonding screening as defensive/AVOID**, directional/copy/insider as thin must-be-proven tilts). Accept the break-even-to-negative base case.
4. **Capital & caps (the numbers that replace the human confirm):** starting bankroll, hot-balance ceiling, per-trade $, per-market/event/cluster/resolution-source %, total-open-risk % NAV, max locked-to-resolution %, daily-loss halt %, consecutive-loss count, **absolute non-loosenable bankroll-at-risk ceiling**, Kelly fraction (recommend ¼).
5. **Data budget:** X API monthly credit ceiling + which ~50–150 accounts; whether tweets are worth it for v1 vs news-only; Polygon RPC (free tier vs paid); third-party historical backfill (yes/no) vs cold-start with trading disabled.
6. **Calibration bar & shadow length:** how many resolved forecasts/category and what Brier-beats-mid margin before `k>floor` and before any real sizing. Recommend ≥150–200/category, out-of-sample positive net PnL with margin.
7. **Insider/copy policy boundary:** confirm detect+notify always, FOLLOW off for v1; legal/ToS review before ever enabling auto-follow given active CFTC/Chainalysis enforcement.
8. **Ramp authority:** confirm ramp-UP is human-gated and requires a *survived-tail* sample (≥ some resolved disputed markets + ≥1 correlated-stress episode), while ramp-DOWN is automatic.

**Bottom line, honestly:** keep the propose_trade chokepoint, key sandboxing, per-intent Kelly, and ungated-MCP invariant verbatim — they're genuinely strong. But do not run real money until **(1)** correlation is in the sizing path with a real-time unrealized-drawdown breaker, **(2)** the out-of-band kill + pre-staged GTD exits exist and are tested against a wedged process, **(3)** bonding/maker are reclassified as tail-risky and the corrected (broad, maker-favoring) fee model drives a maker-only bias, and **(4)** a long shadow phase shows *net-of-everything* positive PnL out-of-sample. If nothing clears its bar in shadow, the correct outcome is **DO NOT DEPLOY** — a 3am bleed is not free, inaction is.