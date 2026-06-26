# S5 — Calibration tracker + base-rate prior + Anchor Gate (POL-7)

**Ticket:** [POL-7](https://mysigner.youtrack.cloud/issue/POL-7) · **Depends on:** S1 (Market-Memory + resolved
outcomes, ✅). Feeds the validator's existing `calib_score` (= k) seam (`evaluate_intent`:
`frac_eff = kelly_fraction · min(1, calib_score)`). Read the master spec §3.6 (base-rate prior + Anchor
Gate), §4.1 (signal fusion → calibrated probability), §5 L3/L4 (calibration breaker + novelty veto), and
[`DECISIONS-S0.md`](DECISIONS-S0.md) §6 (the calibration bar) first.

**Status:** designed 2026-06-26 (operator-approved); ONE combined slice (both halves), built as
cleanly-isolated units. This is the **L3 GO/NO-GO sizing gate** (does the bot's own forecasting beat the
market well enough to risk money) plus the **anti-overconfidence Anchor Gate** (clamp Hermes's `p` so it
can't run away into a confident-wrong narrative).

> **Honesty / data-gated.** Like the co-move matrix, the machinery is real, correct, and fully unit-tested
> now, but **dormant in production** until S6 feeds forecasts and markets resolve. At v1 every category is
> COLD (`k=0` → paper-only) until ≥`min_n` honest resolutions accrue — exactly the intended "do not deploy
> until proven" behavior. New package `src/polybot/calibration/`.

## Operator decisions resolved (the three forks the specs left open)
1. **`k` is BINARY {0,1} at v1** (DECISIONS-S0 §6: "at $300 the sample is a binary GO/NO-GO gate, not a
   continuous live-size dial"). `k=1` only when a category passes ALL go-criteria; else `k=0`. The continuous
   stats (Brier-skill, reliability) are computed and exposed in a `CalibrationReport` for scale-up.
2. **Anchor Gate = widen-but-still-bounded.** Clamp `logit(p)` into the INTERSECTION of `[logit(anchor) ±
   max_shift]` over BOTH anchors (base-rate prior + market mid). No corroboration → `max_shift_uncorroborated`;
   a corroborated catalyst (≥2 independent allowlisted primaries) → `max_shift_corroborated` (wider, still
   bounded — a single misread/laundered catalyst cannot run away). Fails closed.
3. **Curated seed priors now, empirical later.** `DEFAULT_REFERENCE_CLASSES` (incumbent-reelection,
   scheduled-Fed-hold, favorite-by-spread, …), operator-tunable, behind a pluggable interface; empirical
   priors from the Market-Memory resolved history are deferred (the resolved set is thin / just accruing).

## Units
| Unit | File | Pure? |
|---|---|---|
| `CalibrationConfig` (knobs + construction-time consistency checks) | `calibration/config.py` | — |
| Forecast→outcome ledger | `calibration/ledger.py` | SQLite (append-only, point-in-time) |
| Brier / reliability / Murphy / Brier-skill | `calibration/scoring.py` | pure |
| Calibration tracker — the `k` multiplier | `calibration/tracker.py` | pure over the ledger |
| Base-rate prior engine | `calibration/prior.py` | pure |
| Anchor Gate | `calibration/anchor.py` | pure |

## Forecast→outcome ledger (`ledger.py`)
Append-only SQLite (mirrors `IntentStore`/`EventStore` patterns; cannot be backfilled). A `forecasts` row:
`forecast_id, category, condition_id, p (the bot's forecast), market_mid (contemporaneous — the baseline),
created_at (monotonic), resolution_status (NULL until resolved), resolved_at`. APIs: `record_forecast(...)`
(INSERT, idempotent on `forecast_id`); `record_resolution(forecast_id, status)` where
status ∈ {`WON`,`LOST`,`DISPUTED_LOST`,`VOID`} (UPDATE, set `resolved_at`); `resolved(category=None)` →
resolved rows. Point-in-time + restart-stable. **The forecast source (Hermes/the ERS on a proposal) is wired
in S6; here the ledger is built + tested standalone.**

## Scoring (`scoring.py`) — pure, Decimal
- `brier(forecasts)` = mean over the SCORED set of `(p − outcome)²`, outcome ∈ {0,1} (WON=1, LOST=0).
- **Murphy decomposition** over `n_bins` reliability bins: `Brier = Reliability − Resolution + Uncertainty`,
  where `Reliability = Σ_k (n_k/N)(p̄_k − ō_k)²` (calibration error, want ≈0), `Resolution = Σ_k (n_k/N)(ō_k −
  ō)²` (discrimination, want large), `Uncertainty = ō(1 − ō)`. The identity is asserted in tests.
- `brier_skill(bot_brier, baseline_brier)` = `1 − bot_brier/baseline_brier` (>0 ⇒ beats the just-quote-the-
  market baseline). Baseline = the **market-mid's** Brier on the IDENTICAL resolved set (using the stored
  `market_mid`).
- **Excludes** `DISPUTED_LOST` + `VOID` from the scored set (a whale-captured UMA flip / a refund is not an
  honest win/loss — it must not poison Brier or `k`).

## Calibration tracker (`tracker.py`) — the `k` multiplier
`k_for(category) → Decimal` ∈ {0, 1} (binary v1). Steps over the category's cleanly-resolved forecasts:
- `n_resolved < min_n` → `k=0` (cold-start → paper-only; the validator sizes `k=0` to a SKIP via the floor).
- else compute `bot_brier`, `market_brier` (baseline), Murphy `reliability`/`resolution`.
- **GO (`k=1`) iff** `n_resolved ≥ min_n` **AND** `brier_skill > brier_skill_min` (beats baseline) **AND**
  `reliability ≤ reliability_max` (≈0) **AND** `resolution > reliability` (Murphy). Else `k=0`.
- `report_for(category) → CalibrationReport` exposes the raw stats (n, bot/market Brier, skill, reliability,
  resolution, disputed/void counts) — the continuous machinery, ready for a scale-up policy.

## Base-rate prior engine (`prior.py`) — pure
`PriorEngine(reference_classes=DEFAULT_REFERENCE_CLASSES)`; `prior_for(market) → Decimal | None`:
classify the market to a reference class (keyword/category match), return its base rate with a **longshot/
favorite shrink** applied (extreme priors shaded toward 0.5 by `longshot_lambda` — a documented, operator-
tunable placeholder for an empirically-fit favorite-longshot curve). No matching class → `None` (the Anchor
Gate then falls back to market-only anchoring). Time-decay is applied in the Anchor Gate, not here.

## Anchor Gate (`anchor.py`) — pure
`anchor_gate(p, market_mid, prior, *, seconds_to_resolution, corroborated, config) → AnchorResult(p_clamped,
shrunk: bool, reason)`:
- `max_shift = max_shift_corroborated if corroborated else max_shift_uncorroborated`.
- Inputs clamped to `(ε, 1−ε)` before `logit` (a $0/$1 anchor → ±inf otherwise; fail-closed).
- Anchors = the market mid, plus the prior UNLESS `seconds_to_resolution < prior_decay_window` (near
  resolution the market is the better anchor → drop the prior) or `prior is None`.
- Allowed band = INTERSECTION of `[logit(a) − max_shift, logit(a) + max_shift]` over the anchors. Clamp
  `logit(p)` into the band: if already inside → unchanged (`shrunk=False`); if outside → clamp to the nearest
  edge (`shrunk=True`, reason `clamped_to_<anchor>`).
- **Empty intersection** (anchors disagree by > 2·max_shift) → `p_clamped = sigmoid(mean(logit(anchors)))`
  (the midpoint anchor) + `shrunk=True`, reason `anchor_conflict` (fail closed — never trust a `p` that
  diverges from BOTH the prior and the market).
- `p_clamped` returned as `Decimal` (6dp). logit/sigmoid computed in float internally (the one log/exp
  boundary), converted at the edge — same pattern as `comove.correlation`.

## `CalibrationConfig` (`config.py`) — defaults (operator-tunable; consistency-checked at construction)
`min_n=150` (§6: 150–200) · `n_bins=10` · `reliability_max=0.03` · `brier_skill_min=0` · `longshot_lambda=0.9`
· `max_shift_uncorroborated=1.0` · `max_shift_corroborated=2.5` (log-odds) · `prior_decay_window_seconds=86400`
(24h) · `epsilon=0.001`. Construction-time checks (fail LOUD): `min_n>0`; `n_bins≥1`; `0<reliability_max<1`;
`0<max_shift_uncorroborated<max_shift_corroborated`; `0<longshot_lambda≤1`; `0<epsilon<0.5`;
`prior_decay_window_seconds≥0`.

## Integration scope (deferred wiring — operator-approved)
Deliver the calibration package **standalone + fully tested**, plus a thin `CalibrationGate` facade
(`k_for` + `clamp_p`) for S6 to plug in. **The deep ERS wiring is DEFERRED to S6**: the live inputs don't
exist yet (no Hermes `p`/citations, no real per-market category feed; "corroborated" comes from the S6
citation truth-gate). Wiring `service.py` now would only stub those. The validator's `calib_score` seam
already exists and stays at its fail-closed default (1) until S6 — note: the *eventual* default when wired is
`k_for(category)`, which is 0 for any un-proven category.

## Testing (strict TDD, synthetic resolved sets — no network/Hermes)
Ledger (record/resolve, point-in-time, restart, disputed/void tagging, idempotency) · scoring (Brier on a
known set; the Murphy identity `Brier = Reliability − Resolution + Uncertainty`; perfect-calibration →
reliability 0; skill sign vs baseline) · tracker (`k=0` below `min_n`; `k=0` when Brier worse than baseline;
`k=0` when reliability too high / resolution ≤ reliability; `k=1` only when ALL pass; disputed-lost EXCLUDED
so it can't flip `k`) · prior (reference-class lookup, longshot shrink, no-match → None) · Anchor Gate
(overconfident `p` clamped; corroboration widens; empty-intersection → midpoint + `anchor_conflict`; prior
dropped within the decay window; ε-guard at 0/1) · config (consistency rejections). Then independent Opus
review(s); re-review safety-critical findings.

## Out of scope for this slice (deferred)
Empirical-from-Market-Memory reference-class priors · the regime-shift detector (needs a live PnL-vs-expected
feed — overlaps S4) · the forward-exposure gate as a distinct per-category capital cap (cold-start→paper-only
is already expressed as `k=0`) · the deep ERS/`service.py` wiring + recording forecasts on a real proposal
(S6) · an empirically-fit favorite-longshot curve · a signed/content-hashed config (the `RiskCaps`-style
signature is an S4 concern).
