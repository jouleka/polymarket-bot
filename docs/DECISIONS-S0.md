# S0 — Finalized Decisions (POL-2)

**Date:** 2026-06-25 · **Status:** decisions FINALIZED (one S0 acceptance item — the empirical tiny-order
proof — is carried by [POL-4](https://mysigner.youtrack.cloud/issue/POL-4)). Read
[`CONTEXT.md`](CONTEXT.md) and [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
§8 first; this file resolves that §8 "Open Decisions" list into concrete numbers.

These are the guardrail values that **replace the human confirm**. They were drafted from the design,
then **adversarially stress-tested by four independent lenses** (ruin/Kelly math, correlated co-move,
dispute-lockup freeze, fee/min-size viability) and reconciled. The verification **cut** the first-draft
caps roughly in half on the dimensions that matter (see "Verification" below).

---

## 1. Signing path — RESOLVED ✅
Build the signer + order-construction core on the **official Rust client `Polymarket/rs-clob-client-v2`**,
exposed to the Python ERS as a sandboxed sidecar/subprocess. Plain EOA (sigtype-0) is rejected; the
Python/TS V2 SDKs have open deposit-wallet auth bugs (`py #70/#77/#53/#56`, `ts #64`). Full record in
[`VERIFICATION-2026-06-24.md`](VERIFICATION-2026-06-24.md). **Still required (POL-4):** empirically place +
cancel ONE real min-size order via `rs-clob-client-v2` before building on it.

## 2. Box topology — RESOLVED ✅ (single hardened VPS)
ERS + keys + Hermes co-located on **one clean dedicated Linux VPS**. **Keys never touch the
Windows/WSL box** (documented cracked-game malware vector). Hardening checklist (built/verified in S4):
- Separate Linux users: `ers` (key-holder) and `hermes` (no sudo, cannot shell into / read `ers`; key file `0600` owned by `ers`, home `0700`).
- ERS as a **systemd system-level service** (`/etc/systemd/system`, **not** `--user` — WSL reboot lesson) with `ProtectHome`, `NoNewPrivileges`, `PrivateTmp`, restricted `SystemCallFilter`.
- **Egress allowlist** (Polymarket CLOB/Gamma/Data API + Polygon RPC + Telegram only).
- **Non-MAX ERC-20 allowances** — approve pUSD to spenders in ~$50 increments, topped as needed; never `MAX_UINT`.
- **Tiny hot balance** (≤ $300) + **auto-sweep** realized balance above $330 to a **cold wallet the ERS cannot spend from** (cold key not on the VPS).
- **Hash-pinned deps + lockfiles** (`Cargo.lock` + hashed Python lockfile); startup self-test (verify pUSD `0xC011a7E1…E82DFB`, contract/struct/domain hashes, signed caps, allowances, sign-canary) must pass or refuse to start.
- **Out-of-band kill**: separate supervisor process + independent `cancelAll()` credential + external dead-man's-switch.
- ⚠ **Accepted residual:** co-location keeps a non-zero blast radius. Revisit two-host (Host A keys / Host B Hermes) **before scaling capital past v1**.

## 3. Primary strategy — RESOLVED ✅
**Maker-rewards first**, with honest net-of-adverse-selection accounting (maker profit marked net of
mark-to-resolution of inventory, real time). **Bonding / hold-to-resolution reclassified as tail-risky →
AVOID** (UMA dispute/lockup). Directional / copy / insider are thin, must-be-proven tilts only. Base case
accepted as **break-even-to-negative**.

## 4. Capital & caps — RESOLVED ✅ (verified envelope) — NAV = $300

> All caps are enforced by the ERS as **worst-case mark-to-resolution loss** (= notional for longs;
> shares × (1 − entry) for the losing side). One capital band: **deployed (= worst-case risk) ≤ $60;
> reserve = NAV − deployed ≥ $240; locked is a sub-budget inside deployed** (no triple-counting).

| # | Control | Value (NAV = $300) |
|---|---|---|
| Bankroll (NAV) | Starting hot wallet | **$300** (pUSD on Polygon) |
| Hot-balance ceiling | Wallet-level; auto-sweep > $330 → cold | **$300** |
| **Absolute non-loosenable AT-RISK ceiling** | Simultaneous worst-case loss; no calibration may widen at S0 | **$60 (20% NAV)** |
| Kelly fraction | `frac_eff = 0.25 · min(1, calib_score)` | **¼** |
| Cold-start gate | Paper-only until ≥150–200 resolved/category; sample is a **GO/NO-GO gate, not a live-size dial at $300** | gate |
| **Per-trade worst-case loss** | typical maker clip $10–12; must clear live `min_order_size` | **≤ $12 (~4% NAV)** |
| Kelly-vs-floor rule | If ¼-Kelly < `max($5 dust, min_order_size×price, tick)` → **SKIP** (never round up) | — |
| **Total-open-risk** | Σ worst-case loss; the last-line backstop | **≤ $60 (20% NAV)** |
| **Max concurrent positions** | 4 × $12 = $48 < $60 (slack for exit fees) | **≤ 4** |
| Matrix-cold concurrency sub-cap | positions whose pairwise co-move corr is UNKNOWN | **≤ 3** |
| Fail-closed cluster assignment | unassignable / matrix-cold intents → catch-all + global **macro-risk-on/off super-cluster** (directional macro/crypto/rates/FX join by default until decorrelated by ≥N resolved obs) | rule |
| Per-event UNION cap | over the **union** of all positions any single driver touches (cluster ∪ UMA source/criteria ∪ headline); take max across cut definitions, not sum | **≤ $24 (8% NAV)** |
| Per-market | | **≤ $18 (6% NAV)** |
| Per-NegRisk-event (legs netted) | | **≤ $18 (6% NAV)** |
| Per-resolution-source OPEN notional | | **≤ $30 (10% NAV)** |
| Per-resolution-source LOCKED+effective | smaller than total locked budget | **≤ $18 (6% NAV)** |
| Liquidity cap | maker / marketable-limit only | ≤ 10% resting depth AND ≤ 1¢ impact |
| Max locked-to-resolution (intentional + **effective**) | `locked_effective` = maker inventory within T-48h of resolution OR disputed OR exit-slippage-to-flat > per-trade OR D1-toxicity pull-quotes; re-evaluated each cycle | **≤ $36 (12% NAV)** |
| Liquidity-reserve floor (unencumbered) | = NAV − deployed, continuous | **≥ $240 (80% NAV)** |
| Always-on venue-side GTD exit brackets | placed AT ENTRY, sized so aggregate standing-exit ≤ total-open; + stale-mark watchdog | rule |
| Forced-taker-exit cost in hurdle | `H += P(forced taker exit) × taker_fee(category)`; refuse entries failing the round-trip; auto-throttle net-negative categories; bias to free geopolitics | rule |
| New-positions rate cap | budget-independent | ≤ 2/hour, ≤ 6/day |
| **Daily pending-worst-case ceiling** | halt-new when realized + open worst-case would cross threshold (evaluated each cycle, not booked-only) | **$24 (8% NAV)** |
| Weekly-loss halt | realized → halt + human review | **$36 (12% NAV)** |
| Consecutive-loss pause | 3 in a row **OR** $24 pending-worst-case, whichever first | pause + alert |
| Real-time unrealized breaker (L7) | (a) FLOOR freeze-adds > **$18 (6%)** any speed · (b) **FLATTEN** > **$30 (10%)** · (c) velocity > $18 in <15 min | 3 triggers |
| Frozen-position flag | disputed/frozen excluded from L7 + consecutive counters (non-actionable) but still count vs total-open + locked/reserve | rule |
| Dispute-freeze stress invariant (ramp gate) | assert `NAV − encumbered_effective − worst_case_markdown_on_frozen ≥ reserve_floor` under a simulated 6-day correlated freeze of the largest source cluster + 100%-adverse co-move | ramp gate |
| Min-position floor | | ≥ `max(min_order_size×price, $5, tick)` |

## 5. Data budget — RESOLVED ✅
**News-only for v1** (X/Twitter skipped). Curated **primary-source allowlist** (AP/Reuters wires,
agency/court/econ release pages, league feeds) + calendar pre-stager; slow-path aggregator + GDELT for
discovery/backtest only (never a trade trigger). **Free Polygon RPC tier.** **No third-party historical
backfill — cold-start with trading disabled**; self-snapshot market data live from day one (it cannot be
backfilled later).

## 6. Calibration bar & shadow length — RESOLVED ✅
**≥150–200 resolved forecasts/category**, rolling **Brier beats the market-mid baseline** (not just 0.5),
reliability slope ≈ 1, and **simulated net PnL positive with margin**, out-of-sample / walk-forward with
multiple-comparisons discipline, **before any real sizing**. At $300 the sample is a **binary GO/NO-GO
gate**, not a continuous live-size dial.

## 7. Insider / copy policy — RESOLVED ✅
**Detect + notify always; FOLLOW off for v1.** Legal/ToS review required before auto-follow is ever
enabled (active CFTC/Chainalysis enforcement).

## 8. Ramp authority — RESOLVED ✅
**Ramp-UP is human-gated** and requires a **survived-tail sample** (≥ some resolved *disputed* markets +
≥1 correlated-stress episode + passing the §4 dispute-freeze stress invariant). **Ramp-DOWN is automatic**
on any daily-loss halt, calibration regression, or maker-net regression. **No real money until S4's kill
path is tested against a deliberately-wedged process AND S9 shadow proves a calibrated, net-positive,
out-of-sample edge. If nothing clears its bar → DO NOT DEPLOY.**

---

## Verification (how these numbers were hardened)
Candidate envelope → 4 independent adversarial lenses → reconciliation (2026-06-25). Defects caught and fixed:
1. **Inverted breaker ordering** — first-draft daily halt ($15) sat *below* per-trade max loss ($25). Fixed: per-trade ≤ $12 < $24 daily ceiling.
2. **Zero-slack caps** — 6 × $25 = $150 = total-open exactly. Fixed: 4 × $12 = $48 < $60.
3. **50%-NAV taxonomy-blind single stroke** — total-open cut 50% → **20% NAV**; fail-closed cluster assignment + matrix-cold sub-cap added.
4. **"$300 non-loosenable ceiling" was an identity** (= NAV). Redefined as a binding **$60 at-risk** cap; $300 kept only as the wallet/sweep line.
5. **Maker inventory not counted as locked** — `locked_effective` added; reserve floor now measured against the right denominator.
6. **Unmodeled forced-taker exits** — folded into the entry hurdle; categories auto-throttled if net-negative after exit fees.
7. **L7 velocity-only bypass** — upgraded to 3 triggers (absolute floor + flatten + velocity) and now **flattens**, not just freezes.

## Residual risks (accepted for v1)
- At $300 with per-trade ≤ $12 + $5 dust floor, only edges where ¼-Kelly ≥ $5 (≈ ≥4¢) are expressable. If proven shadow edges are mostly sub-4¢, **$300 is too small** and the ramp gate should require a larger starting bankroll before live. *(bankroll-adequacy, not ruin)*
- Fail-closed max-correlation default **over-couples** while the matrix is cold → the bot may barely trade at S0. Accepted trade for "don't blow up" (and S0 is shadow/paper anyway).
- GTD brackets mitigate but don't eliminate true gap risk (instantaneous UMA flip with no fillable level).
- Breakers depend on a fresh mark; a 3am WS disconnect + wide book can starve them → stale-mark watchdog is the backstop (own tuning risk).
- Single-VPS co-location is a deliberate blast-radius acceptance; revisit two-host before scaling.

## What this unblocks
S0 decisions are **complete**. The signing-path choice (§1) unblocks **[POL-4](https://mysigner.youtrack.cloud/issue/POL-4) (S2)**;
the signed caps config (§4) is consumed by **[POL-5](https://mysigner.youtrack.cloud/issue/POL-5) (S3, ERS)**
and the safety envelope **[POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4)**. The only outstanding
S0 acceptance item — a **working tiny-order proof** — is delivered empirically by POL-4.
