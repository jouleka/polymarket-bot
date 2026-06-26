# S7 — Smart-money + insider/informed-flow detectors (POL-9)

**Ticket:** [POL-9](https://mysigner.youtrack.cloud/issue/POL-9) · **Depends on:** S1 (the on-chain Polygon
watcher + the Data-API poller, ✅). Read the master spec §3.4 (smart-money), §3.5 (insider detection), and
§5 (the FOLLOW/AVOID/FLAG policy) first.

**Status:** designed 2026-06-27 (operator-approved); ONE combined slice, built as cleanly-isolated pure
units. A **defensive** analytics layer: find *genuinely* sharp wallets (not lucky), score informed/insider
flow, and act defensively (AVOID + FLAG + feed D1 toxicity to the maker pull-quote seam). **FOLLOW is
hard-disabled in code.**

> **Legal/EV stance (from the ticket):** observing public on-chain flow and copying it is NOT securities
> insider trading (the crime is the leaker's). The genuine trap is downstream **adverse selection** — you may
> be late, baited (wash/spoof), or the wallet may simply be wrong. So the one clearly +EV use is DEFENSIVE
> (D1 toxicity → widen/pull maker quotes); FOLLOW stays OFF until precision is empirically proven.

> **Honesty / deferred wiring.** Like S5, the machinery is real, correct, and fully unit-tested now, but the
> **live-feed wiring is deferred**: the detectors are pure functions over defined input shapes; parsing the
> live `/activity` topic + on-chain CTF events into those shapes, the real S8 maker module, and Hermes's
> catalyst timeline (D3) all land later. New package `src/polybot/detectors/`.

## Operator decisions resolved (the two forks)
1. **Luck filter = binomial-z + deterministic normal-CI + single-event-dominance** (no RNG bootstrap at v1).
2. **Composite = weighted sum + single-Critical override** — any one detector in its Critical band escalates
   the overall band to ≥High, so one strong defensive signal isn't diluted by quiet detectors.

## Units
| Unit | File | Pure? |
|---|---|---|
| `DetectorConfig` (knobs + construction-time checks) | `detectors/config.py` | — |
| PnL reconstruction (cash-flow ledger) | `detectors/pnl.py` | pure, Decimal |
| Luck filter (the skill gate) | `detectors/luck.py` | pure, float stats |
| Wallet classification | `detectors/classify.py` | pure |
| Sybil clustering (funder graph) | `detectors/sybil.py` | pure |
| D1 toxicity (+ pull-quote seam) | `detectors/toxicity.py` | pure |
| D2/D3/D5/D6 + D4 sub-scores | `detectors/signals.py` | pure |
| Composite 0–10 + bands | `detectors/composite.py` | pure |
| Decision policy (FOLLOW off) | `detectors/policy.py` | pure |

## PnL reconstruction (`pnl.py`) — exact Decimal
Input: a wallet's `CashFlow(kind, condition_id, usd)` events, `kind ∈ {BUY,SELL,SPLIT,MERGE,REDEEM,REWARD}`,
plus `current_market_value` per still-open conditionId. **Realized PnL** =
`ΣSELL + ΣREDEEM + ΣMERGE + ΣREWARD − ΣBUY − ΣSPLIT + Σ current_market_value`, with per-conditionId
bucketing. **NEVER** `/leaderboard` PnL (mark-to-market; auto-redemption deletes winners from `/positions`).
Acceptance: matches a hand-computed known wallet.

## Luck filter (`luck.py`) — the skill gate
Input: a wallet's RESOLVED bets, each `(entry_price, outcome ∈ {0,1})`. Per-bet **edge = outcome − entry_price**
(realized excess vs the price-implied baseline). Gates (all must hold → pass; else weight 0):
1. `n_resolved ≥ min_resolved` (50).
2. **Binomial-z (wins beat the price-implied baseline).** Null: each bet wins with prob `entry_price`
   (Poisson-binomial). `μ = Σ entry_price`, `σ² = Σ entry_price·(1−entry_price)`; one-sided
   `z = (Σ outcome − μ)/σ` must exceed the `win_significance` (p<0.001 ⇒ z≈3.09, via `statistics.NormalDist`).
3. **Normal-CI on mean edge excludes 0.** One-sided lower bound at `edge_ci_confidence` (99%):
   `mean_edge − z*·(stdev_edge/√n) > 0`.
4. **Not single-event-dominated.** No single bet's edge > `max_event_dominance` (0.5) of the total positive
   edge.

Returns `WalletEdge(n, mean_edge, win_z, edge_ci_low, max_share, passes)`. `weight = 1 if passes else 0`
(binary at v1, consistent with the calibration `k`; FOLLOW is off so the weight only informs D6/composite,
never sizing). Acceptance: rejects small-sample / lucky / single-event-dominated.

## Classification (`classify.py`)
`classify(stats) → {SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE}`:
- **MARKET_MAKER first** (excluded from copy — edge = uncopyable rebates): `trade_count ≥ mm_min_trades` AND
  two-sided balance `min(buy_vol, sell_vol)/max(buy_vol, sell_vol) ≥ mm_balance_min` (0.4).
- else if the luck filter **passes**: `INSIDER_LIKE` when the insider composite is High/Critical (suspicious
  timing/concentration), else `SHARP`.
- else: `LUCKY` (positive raw edge but fails the significance/dominance gate) else `NOISE`.

## Sybil clustering (`sybil.py`)
Union-find connected components over funding edges `(wallet, funder)`: wallets sharing a common Polygon
funder collapse to one cluster (coordination, NOT guilt). Used by classification + D4.

## D1 toxicity (`toxicity.py`) — the +EV defensive signal
Input: a trade-flow window (`Σ buy_size`, `Σ sell_size`) + the market's own rolling baseline `(mean, std)` of
the imbalance. **one-sided ratio** `= |Σbuy − Σsell| / (Σbuy + Σsell)`; **toxic** iff `ratio ≥ toxicity_ratio_min`
(0.75) AND `z = (ratio − baseline_mean)/baseline_std ≥ toxicity_z_min` (2.0). Sub-score 0–1 scaled past the
thresholds; `pull_quotes = toxic` is the seam the S8 maker module will consume. (A simplified VPIN; full
volume-bucketed VPIN deferred.)

## D2/D3/D4/D5/D6 (`signals.py`) — all 0–1 sub-scores
- **D2 conviction** = `clamp(size/wallet_value)·(1−entry)·recency` (recency ∈ [0,1], fresh wallet → 1).
- **D3 abnormal move** = a z-score of the odds/volume move scaled to 0–1, **× (1 − catalyst_present)** so a
  known public catalyst cancels it. `catalyst_present` is an input flag (Hermes supplies the real timeline in
  S6 — defaults to False = no known catalyst).
- **D4 coordinated entry** = fraction of a market's recent entries coming from one sybil cluster (uses
  `sybil`).
- **D5 lead-time** = `lead = public_ts − trade_ts`; positive (traded BEFORE the news went public) → high
  sub-score; ≤0 → 0.
- **D6 smart-money conviction** = the FOLLOW-side score (the luck-filter `weight` × conviction). FOLLOW is OFF,
  so this is informational only.

## Composite + bands (`composite.py`)
`composite(subscores: dict, config) → CompositeScore(value, band)`. `value` = weighted sum of the present
sub-scores scaled to **0–10**. Bands by fixed cutoffs (`< low_max` Low, `< med_max` Med, `< high_max` High,
else Critical). **Single-Critical override:** any sub-score ≥ `critical_subscore` (0.8) forces the band to
≥High regardless of the weighted average.

## Decision policy (`policy.py`)
`decide(composite, classification, toxicity, config) → DetectorDecision(action ∈ {AVOID, FLAG_ONLY},
pull_quotes: bool, reasons)`. **`FOLLOW_ENABLED = False`** module constant gates the ONLY `FOLLOW` branch
(dead code at v1). Default: High/Critical composite OR INSIDER_LIKE → **AVOID** (don't trade into likely-
informed flow) + FLAG; lower → FLAG_ONLY. `pull_quotes` passes through D1 toxicity (the maker seam). A test
sweeps maximal signals and asserts the action is **never FOLLOW**.

## `DetectorConfig` (`config.py`) defaults (operator-tunable; consistency-checked)
`min_resolved=50` · `win_significance=0.001` · `edge_ci_confidence=0.99` · `max_event_dominance=0.5` ·
`mm_min_trades=100` · `mm_balance_min=0.4` · `toxicity_ratio_min=0.75` · `toxicity_z_min=2.0` ·
composite band cutoffs `2.5 / 5.0 / 7.5` (on 0–10) · `critical_subscore=0.8`. Construction checks: positives;
probabilities/fractions in range; `0 < low_max < med_max < high_max < 10`; `0 < win_significance < 0.5`;
`0 < edge_ci_confidence < 1`.

## Acceptance (POL-9) — how each is met
PnL reconstruction matches a couple of known wallets ✅ (hand-computed test) · the luck filter rejects
small-sample wallets ✅ · D1 toxicity is wired to the maker pull-quote signal ✅ (`pull_quotes` seam) · a
FOLLOW-disabled flag is enforced in code ✅ (`FOLLOW_ENABLED=False` + a sweep test).

## Testing (strict TDD, synthetic data — no network)
PnL (hand-computed wallet; per-condition; never-leaderboard) · luck filter (small-sample→fail; lucky-but-
insignificant→fail; single-event-dominated→fail; genuinely-sharp→pass; CI excludes 0) · classification (each
of the 5 classes; MM excluded) · sybil (shared-funder→same cluster) · D1 (ratio+z thresholds; pull-quote seam)
· D2/D3/D5/D6 sub-scores · D4 co-entry · composite (weighted sum; single-Critical override; bands) · **policy
(FOLLOW never emitted; AVOID+FLAG default; pull-quote on toxicity)** · config (consistency rejections). Then
independent Opus review(s); re-review safety-critical findings.

## Out of scope for this slice (deferred)
Live wiring of `/activity` + on-chain CTF reconciliation into the detectors · the real S8 maker module (D1 →
a signal seam only) · D3's Hermes-supplied catalyst timeline (built behind a `catalyst_present` flag) · full
volume-bucketed VPIN + a real bootstrap CI (deterministic approximations now) · FOLLOW itself (hard-off).
