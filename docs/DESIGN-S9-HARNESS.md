# DESIGN — S9 / POL-11: Shadow harness → earn-autonomy ramp controller

**Date:** 2026-07-04 · **Ticket:** [POL-11](https://mysigner.youtrack.cloud/issue/POL-11) (S9) ·
**Status:** DESIGN (forks operator-resolved 2026-07-04 → awaiting operator spec review → writing-plans).
**Depends on:** S3 (the `RiskCaps` envelope + `Portfolio`/`OpenPosition`), S4.5 (`RestartReconciler`, the standalone
boot unit this wires in), S4.7 (`ers/ramp.py` tighten-only `step_daily`/`step_weekly` — the ramp-DOWN it delegates
to), S5 (`CalibrationGate.k_for` + `calibration/scoring.py`), S8 (`maker/` — the `net_pnl` identity, `reward_accrual`,
`taker_fee`/`rebate`, `inventory.adverse_selection`, and `MakerGate.go_for`, ALL reused as computation primitives).
**Runs SHADOW-ONLY over simulated maker fills + injected books/marks.** Nothing quotes, signs, sends, or widens a cap.

> Master design §5 "Earn-autonomy staged ramp" + §7 S9 + DECISIONS-S0 §6/§8 (the load-bearing doctrine, distilled):
> *Stage 0 SHADOW logs every intent as a simulated fill **net of corrected fees + slippage + lockup opportunity cost +
> dispute/void haircut**; accrue ≥150–200 resolved/category; prove rolling Brier **beats the market-mid baseline**,
> reliability slope ~1, and simulated net PnL **positive with margin, out-of-sample / walk-forward with
> multiple-comparisons discipline**, before any real sizing. Stage 1 TINY-LIVE / Stage 2..n RAMP widen caps by fixed
> steps ONLY while calibration holds AND no breaker tripped AND **the tail was survived** (≥ some resolved DISPUTED
> markets AND ≥1 correlated-stress episode; tie max caps to a 100%-adverse co-move stress staying within ruin limits).
> **Ramp-UP is human-gated; ramp-DOWN is automatic. An absolute non-loosenable bankroll-at-risk ceiling regardless of
> calibration.*** Honest stance (CONTEXT §1): the null hypothesis is break-even-to-negative; if nothing clears its bar
> in shadow → **DO NOT DEPLOY** (a 3am bleed is not free; inaction is).

---

## 0. TL;DR + resolved forks

S9 is a NEW self-contained package `src/polybot/harness/` mirroring `maker/`'s SHAPE — a self-verifying config → pure
exact-Decimal calculators → an append-only shadow-trade ledger → an evidence evaluator → a binary stage-machine
controller. It is the capstone that turns accrued shadow evidence into the **earn-autonomy** decision: it composes S5's
calibration `k`, S8's maker `go`, the portfolio-level **net-of-everything** paper PnL (out-of-sample), the tail-survival
sample, and a dispute-freeze stress test into a per-category `RampDecision`. It is purely ADDITIVE except ONE opt-in
`reconciler=None` seam on `ERSController` (byte-for-byte inert until wired), which finally connects the standalone
`RestartReconciler` into boot. Consumed by nobody yet — it is the operator's dashboard/gate for going live.

**The honesty spine (mirrors S8's "never reward-gross"):** the controller may recommend advancing a category ONLY on
`net_of_everything` shadow PnL that is **positive-with-margin AND out-of-sample** — never gross edge, never in-sample.

**Resolved forks (operator-confirmed 2026-07-04):**

| # | Fork | Decision |
|---|---|---|
| 1 | Scope | **One slice, sub-sliced S9a–S9d** (the S8 pipeline): one coherent `DESIGN`/`PLAN`, built harness-first then the controller on top; the pieces are tightly coupled (the controller reads the harness's ledger). |
| 2 | Shadow fill model | **Maker-primary, reuse S8.** The system is maker-biased; model resting-limit maker fills through S8's `reward_accrual` + `net_pnl` identity. The forced-taker-exit leg (taker fee) is already priced in S8's `fees` leg. No taker walk-the-book simulator (a crossed/marketable resting price fails closed to `filled=False`). |
| 3 | OOS rigor | **Walk-forward + multiple-comparisons.** Hold out the most-recent time-ordered fraction of the resolved sample as OOS; the net-positive-with-margin AND Brier-beats-mid criteria must hold in the OOS window; the required OOS margin is inflated by a family-size penalty (certifying 1-of-N categories needs a proportionally stronger edge). |
| 4 | Boot wiring | **Opt-in `reconciler=None` seam inside S9d** (the existing `anomaly=`/`telegram=` pattern) — `reconciler=None` == today byte-for-byte; the one place S9 touches an existing file, done as a safe additive seam. |

**Baked (doctrine-forced, not asked):** **ramp-UP is advisory** — `decide` emits `promote_recommended` + a reason; it
NEVER mutates caps (DECISIONS-S0 §8: ramp-up is human-gated). **ramp-DOWN is automatic** and delegates to the existing
S4.7 tighten-only ratchet (the controller only raises the `ramp_down` flag; `ERSController` already applies
`step_daily`/`step_weekly`). The **absolute $60 at-risk ceiling is structurally non-loosenable** by anything in S9 — the
controller has no cap-mutation surface at all, and any advisory envelope it reports is min()-clamped to the signed
`RiskCaps` ceiling. DISPUTED/VOID are EXCLUDED from the net sample (reusing the S8/S5 whale-flip-immunity discipline) but
**tail-survival REQUIRES resolved DISPUTED present** (you must have survived real disputes, not dodged them). Every
numeric is exact `Decimal` from strings; config + ledger fail LOUD at construction; the OOS/stress paths fail CLOSED
(insufficient sample / a raising leg → not-ready, never a phantom GO). Everything is data-gated dormant: cold → SHADOW.

---

## 1. Goal & non-goals

**Goal:** the deterministic earn-autonomy machinery — the shadow fill simulator, the net-of-everything shadow ledger +
windowed PnL, the walk-forward/multiple-comparisons evidence evaluator, the dispute-freeze stress test, and the binary
stage-machine controller — as pure units over defined input shapes (a proposed intent + a book snapshot; a resolved
shadow sample; the S5/S8 gates; the signed caps), unit-tested against hand-computed cases, data-gated dormant. Plus the
`RestartReconciler`→`ERSController` boot seam. Concretely the eight units in §4.

**Non-goals (deferred; §6):** actually RUNNING the shadow period to accrue ≥150 resolved/category (needs a DEPLOYED
Hermes feeding the propose-only facade + continuous read-only ingestion — operational, not codeable here); the live
fills-recorder that feeds the harness in production; Stage-1+ LIVE execution / real cap-widening (POL-4 signer + the
operator's human ramp-up gate); the real resolution/dispute feed that settles shadow trades → WON/LOST/DISPUTED; a true
aggressor-flow slippage measure (maker-primary uses the resting-price maker model, Fork 2); refactoring `MakerTracker`
to share its leg-fold (S9's `pnl.window_net` re-derives the same legs over a time-window to keep S8 untouched). **No
change to any existing file except the single opt-in `ERSController(reconciler=None)` seam** — S9 reads `RiskCaps`,
`Portfolio`, the two gates, and the `maker`/`calibration` primitives; it imports nothing it mutates.

## 2. Architecture

```
src/polybot/harness/  (NEW package — mirrors maker/ shape; additive but for the one ERSController seam; consumed by nobody yet)

  config.py         RampConfig (frozen, self-verifying) — Stage-0 thresholds, OOS holdout, the MC penalty,
                    tail-survival minimums, the advisory ramp step, reliability ceiling.
  fill_sim.py       simulate_fill(...) -> SimulatedFill — the resting-maker entry from an intent + a book; maker-only
                    (crossed/degenerate/stale -> filled=False fail-closed); reward via maker.reward_accrual.
  ledger.py         ShadowLedger (append-only SQLite; mirrors maker/ledger.py) — records each simulated trade + its
                    eventual resolution; the substrate the evidence evaluator windows over.
  pnl.py            window_net(rows, *, maker_config) -> Decimal — net-of-everything over a LIST of settled shadow rows
                    (a time window), reusing maker.net_pnl + the S8 leg primitives; DISPUTED/VOID excluded.
  evidence.py       evaluate_category(...) -> EvidenceReport — walk-forward OOS split + MC-penalized margin + Brier-beats-
                    mid + reliability + reads CalibrationGate.k_for + MakerGate.go_for; ready = ALL gates cleared.
  stress.py         dispute_freeze_stress(portfolio, *, caps) -> StressResult (the DECISIONS-S0 §4 invariant) +
                    tail_survived(...) -> bool (>=1 resolved DISPUTED AND >=1 stress episode).
  ramp_controller.py  RampController.decide(...) -> RampDecision — the binary stage machine: SHADOW/TINY_LIVE/RAMP +
                    advisory promote_recommended + automatic ramp_down flag; NO cap-mutation surface; ceiling-clamped.

  (+ the ers/controller.py seam: ERSController(reconciler=None); a boot() method that, when wired, calls
     reconciler.reconcile_on_boot() and adopts the rebuilt portfolio + the RUNNING/HALTED transition. reconciler=None
     == today byte-for-byte; the DORMANT wallet=None shadow path -> RUNNING.)
```

- **Shadow, data-gated, dormant** (the S5/S8 pattern): the ledger accrues simulated fills + resolutions; `decide(cat)`
  returns `stage=SHADOW, promote_recommended=False` until the sample clears every Stage-0 gate AND the tail is survived
  AND the stress test passes AND no breaker is tripped. Cold (`n=0`) → SHADOW, all evidence stats `None`.
- **Honest by construction:** the only PnL the evaluator reads is `pnl.window_net` (after ALL costs, reusing S8's
  `net_pnl`); there is no gross accessor. The OOS gate reads the OUT-OF-SAMPLE window's net, not the full-sample net.
- **Advisory ramp-UP, automatic ramp-DOWN, non-loosenable ceiling:** `RampController` has no method that widens or
  loosens a cap. `promote_recommended` is a signal the operator acts on out-of-band (the human ramp-up gate). `ramp_down`
  delegates to the existing S4.7 tighten-only ratchet. Any advisory envelope is min()-clamped to the signed ceiling.
- **Reuse, not reinvention:** `net_pnl`, `reward_accrual`, `taker_fee`/`rebate`, `inventory.adverse_selection` (S8);
  `k_for` + `brier`/`brier_skill`/`murphy` (S5); `step_daily`/`step_weekly` (S4.7); `RestartReconciler` (S4.5). S9 adds
  the walk-forward windowing, the MC penalty, the stress invariant, and the stage machine — nothing it can borrow.

## 3. What the harness records + the earn-autonomy criteria

Each simulated trade is one resting-maker entry: `(reward_accrued at fill)` + `(the resolution mark at settle)` →
its net-of-everything via the S8 identity `net = reward + rebate + spread_capture − adverse_selection − fees −
lockup_cost − dispute_haircut`. Windowed over the time-ordered resolved sample, a category is **Stage-0 ready** iff ALL:

| Criterion | Source | Gate |
|---|---|---|
| Sample size | `ShadowLedger.settled(cat)` honest count | `n_resolved ≥ ramp_config.min_resolved` (≥150) |
| OOS sample | most-recent `oos_holdout_fraction` by `settled_at` | `n_oos ≥ ramp_config.min_oos_resolved` |
| Net-of-everything OOS | `pnl.window_net(oos_rows)` | `net_oos > net_margin_min + mc_penalty·(family_size−1)` (positive, WITH margin, MC-inflated) |
| Beats the market | `calibration/scoring.brier_skill` on the OOS forecasts | `brier_skill > 0` (Brier beats the market-mid baseline) |
| Well-calibrated | `murphy(...).reliability` | `reliability ≤ reliability_max` (slope ≈ 1) |
| Calibration gate | `CalibrationGate.k_for(cat)` | `k == 1` |
| Maker gate | `MakerGate.go_for(cat)` | `go == True` |
| Tail survived | `stress.tail_survived` | `n_resolved_disputed ≥ min` AND `stress_episodes ≥ min` |
| Dispute-freeze stress | `stress.dispute_freeze_stress(portfolio)` | `survives == True` (reserve floor holds under 100%-adverse freeze) |
| No breaker | injected `breaker_tripped` | `False` |

`ready` = the AND of the first seven (the evidence gates); a **promotion** additionally requires tail-survival + stress
+ no-breaker. Ramp-DOWN fires when a previously-ready category regresses (evidence flips un-ready) OR a breaker trips.

## 4. Net-new units (the pinned contract block)

```python
# harness/config.py
@dataclass(frozen=True)
class RampConfig:
    min_resolved: int = 150                          # Stage-0 floor per category (DECISIONS-S0 §6: 150-200)
    net_margin_min: Decimal = Decimal("0")           # OOS net must EXCEED this (positive WITH margin)
    oos_holdout_fraction: Decimal = Decimal("0.30")  # most-recent fraction held out-of-sample; (0,1)
    min_oos_resolved: int = 30                        # min resolved in the OOS window before the OOS gate can pass
    mc_penalty: Decimal = Decimal("0")               # per-extra-category OOS-margin inflation; >= 0 (re-pull at deploy)
    reliability_max: Decimal = Decimal("0.03")       # Murphy reliability ceiling (slope ~1); (0, 0.1]
    min_resolved_disputed: int = 1                    # tail-survival: >=1 resolved DISPUTED in the sample; >= 0
    min_stress_episodes: int = 1                      # tail-survival: >=1 correlated-stress episode; >= 0
    ramp_step_fraction: Decimal = Decimal("0.5")     # advisory widen step per stage (reported only); (0, 1]
    # __post_init__ -> _verify(): min_resolved>0, net_margin_min>=0 & finite, 0<oos_holdout_fraction<1,
    #   min_oos_resolved>0, mc_penalty>=0 & finite, 0<reliability_max<=0.1, min_resolved_disputed>=0,
    #   min_stress_episodes>=0, 0<ramp_step_fraction<=1 -> ValueError else (is_finite BEFORE every compare).

# harness/fill_sim.py
@dataclass(frozen=True)
class SimulatedFill:
    token_id: str; condition_id: str; category: str; side: str; shares: Decimal
    fill_price: Decimal; fill_mid: Decimal; spread_from_mid: Decimal
    filled: bool; reward_accrued: Decimal
def simulate_fill(*, token_id, condition_id, category, side, shares, resting_price, book, maker_config) -> SimulatedFill
    # MAKER-ONLY (Fork 2): resting_price must be a maker price given the live book -- a BUY must rest <= best_ask-tick
    #   (not cross the ask); a SELL must rest >= best_bid+tick. If it would cross (marketable), or the book is stale/
    #   degenerate (no two-sided top, midpoint None/non-finite, crossed) -> filled=False, reward_accrued=Decimal(0)
    #   (fail-closed: we do NOT shadow taker fills; an unfillable maker order simply earns nothing).
    # fill_price = resting_price; fill_mid = book.midpoint(); spread_from_mid = abs(resting_price - fill_mid);
    # reward_accrued = maker.reward_accrual(shares, spread_from_mid, config=maker_config) when filled else 0.
    # shares finite>0, resting_price finite in (0,1) else ValueError (fail loud on a bad proposal).

# harness/ledger.py  (append-only SQLite; mirrors maker/ledger.py MakerLedger EXACTLY)
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")
@dataclass(frozen=True)
class ShadowTradeRecord:
    trade_id: str; token_id: str; condition_id: str; category: str; side: str; shares: Decimal
    fill_price: Decimal; fill_mid: Decimal; reward_accrued: Decimal; created_at: int
    status: str | None = None; resolution_value: Decimal | None = None; settled_at: int | None = None
class ShadowLedger:
    def __init__(self, path, stamper): ...            # WAL; stamper.stamp() monotonic ts; Decimals as exact strings
    def record_trade(self, trade_id, *, token_id, condition_id, category, side, shares, fill_price, fill_mid,
                     reward_accrued) -> bool           # INSERT OR IGNORE (idempotent; True new / False dup)
    def record_settlement(self, trade_id, *, status, resolution_value) -> None   # WON/LOST need finite [0,1] value;
                                                       # DISPUTED/VOID need None; overwrites; loud on bad status/id
    def settled(self, category=None) -> list           # rows with a status set, ORDER BY settled_at then rowid
    def all(self) -> list

# harness/pnl.py  (windowed net-of-everything; reuses the S8 identity + primitives)
def window_net(rows, *, maker_config) -> Decimal
    # Over a LIST of settled ShadowTradeRecords -- honest WON/LOST only (DISPUTED/VOID skipped, whale-flip immunity):
    #   reward = Σ reward_accrued; cf_i = maker.taker_fee(category, fill_price, shares, schedule=maker_config.fee_schedule);
    #   rebate = maker.rebate(Σ cf, fraction=maker_config.rebate_fraction);
    #   spread_capture = Σ sgn(side)·shares·(fill_mid − fill_price)  (BUY=+1, SELL=−1);
    #   adverse_selection = maker.inventory.adverse_selection(fills, mark_for=resolution_value per token);
    #   fees = maker_config.forced_taker_exit_p·Σcf; lockup = maker_config.lockup_rate·Σnotional;
    #   dispute = maker_config.dispute_p·Σnotional;  return maker.net_pnl(...).net.  Empty rows -> Decimal(0).
    # (Same leg derivations as MakerTracker.report_for, applied to an arbitrary time-window -- a reviewer checks they agree.)

# harness/evidence.py
@dataclass(frozen=True)
class EvidenceReport:
    category: str; n_resolved: int; n_oos: int; n_disputed: int
    net_full: Decimal | None; net_oos: Decimal | None; brier_skill: Decimal | None; reliability: Decimal | None
    k: Decimal; maker_go: bool
    required_margin: Decimal; oos_positive: bool; calibration_ok: bool; maker_ok: bool; ready: bool
def evaluate_category(category, *, shadow_ledger, forecast_ledger, calibration_gate, maker_gate,
                      ramp_config, maker_config, family_size) -> EvidenceReport
    # settled = shadow_ledger.settled(category) time-ordered by settled_at; honest WON/LOST vs DISPUTED/VOID counted.
    # OOS window = the most-recent ceil(oos_holdout_fraction · n_resolved) honest rows.
    # required_margin = ramp_config.net_margin_min + ramp_config.mc_penalty·(family_size − 1)  (family_size >= 1).
    # net_oos = pnl.window_net(oos_rows); oos_positive = (n_oos >= min_oos_resolved) and (net_oos > required_margin).
    # brier_skill/reliability from calibration/scoring over the OOS forecasts (forecast_ledger.resolved(category),
    #   the same time-window); calibration_ok = (k == 1) and (brier_skill > 0) and (reliability <= reliability_max).
    # maker_ok = maker_gate.go_for(category). ready = (n_resolved >= min_resolved) and oos_positive and calibration_ok
    #   and maker_ok.  Cold / insufficient -> None stats, ready False (fail-closed).

# harness/stress.py
@dataclass(frozen=True)
class StressResult:
    survives: bool; reserve_after: Decimal; reserve_floor: Decimal; worst_case_markdown: Decimal
def dispute_freeze_stress(portfolio, *, caps, adverse_fraction=Decimal("1")) -> StressResult
    # DECISIONS-S0 §4 dispute-freeze stress invariant: simulate a freeze of the largest resolution-source cluster +
    # a 100%-adverse co-move (adverse_fraction) across it; worst_case_markdown = Σ over the frozen cluster of
    # worst_case_risk·adverse_fraction; reserve_after = caps.nav − encumbered_effective − worst_case_markdown;
    # survives = reserve_after >= caps.reserve_floor.  Pure over the Portfolio + signed caps; fail-closed (a
    # non-finite / missing field -> survives False).
def tail_survived(*, n_resolved_disputed, stress_episodes, ramp_config) -> bool
    # n_resolved_disputed >= ramp_config.min_resolved_disputed AND stress_episodes >= ramp_config.min_stress_episodes.

# harness/ramp_controller.py
SHADOW = "SHADOW"; TINY_LIVE = "TINY_LIVE"; RAMP = "RAMP"
@dataclass(frozen=True)
class RampDecision:
    category: str; stage: str; promote_recommended: bool; ramp_down: bool; reason: str; evidence: EvidenceReport
class RampController:
    def __init__(self, *, ramp_config, caps): ...      # NO signer, NO cap-mutation surface -- structurally advisory
    def decide(self, category, *, evidence, current_stage, portfolio, n_resolved_disputed, stress_episodes,
               breaker_tripped) -> RampDecision
    #   promote_recommended (advisory ramp-UP) = evidence.ready AND tail_survived(...) AND
    #     dispute_freeze_stress(portfolio, caps=self._caps).survives AND NOT breaker_tripped.  NEVER mutates a cap.
    #   ramp_down (automatic) = breaker_tripped OR (current_stage != SHADOW AND NOT evidence.ready)  -- a regression;
    #     the flag ERSController hands to the existing S4.7 tighten-only ratchet. The controller itself tightens nothing.
    #   stage: SHADOW if not evidence.ready; else the current_stage (promotion past it is the operator's human gate).
    #   reason: a short machine string naming the gate that failed / the promotion basis.
    #   The absolute ceiling is untouchable: RampController has no cap-writing method; any advisory envelope a caller
    #   derives from ramp_step_fraction MUST be min()-clamped to caps' non-loosenable at-risk ceiling (documented seam).

# ers/controller.py  (the ONE existing-file change -- an opt-in additive seam)
class ERSController:
    def __init__(self, *, ..., reconciler=None, ...): ...   # reconciler=None (default) == today byte-for-byte
    def boot(self):
    #   if self._reconciler is not None: self._portfolio = self._reconciler.reconcile_on_boot()  (adopts the RUNNING/
    #     HALTED transition + the rebuilt portfolio). reconciler=None -> no-op (stays HALTED, empty portfolio == today).
    #   The deploy calls controller.boot() ONCE before the run loop; the DORMANT wallet=None shadow path -> RUNNING.
```

**Sacred surfaces UNCHANGED.** `evaluate_intent`/the validator, `propose_trade`'s INSERT-only chokepoint,
`process_pending`'s decision-flow, and `run_cycle`'s existing body are byte-for-byte untouched; the only edit is the
additive `reconciler=` ctor param + the new `boot()` method (both inert when `reconciler=None`).

## 5. Safety / honesty invariants

1. **Never gross, never in-sample.** The only PnL the evaluator reads is `pnl.window_net` (after ALL S8 costs), over
   the OUT-OF-SAMPLE window. A structural test asserts `window_net` equals the S8 identity and that `oos_positive`
   reads `net_oos` (the OOS window), not `net_full`; a mutation making it read a gross leg or the full sample is killed.
2. **Advisory ramp-UP, non-loosenable ceiling.** `RampController` has NO cap-mutation method — `promote_recommended` is
   a signal only. A structural sweep proves no `swap_caps`/`set_state`/signer path on the controller; the absolute
   at-risk ceiling is untouchable by S9. Ramp-UP mutating a cap is unrepresentable.
3. **Automatic ramp-DOWN.** A regression (a ready→un-ready flip, or a tripped breaker) raises `ramp_down`; the existing
   S4.7 `step_daily`/`step_weekly` ratchet applies it. S9 tightens nothing itself (no double-ratchet).
4. **Tail-survival REQUIRES disputes.** DISPUTED/VOID are excluded from the net sample (whale-flip immunity), BUT a
   promotion requires `n_resolved_disputed ≥ min` — you must have survived real disputes, not merely avoided them.
5. **Data-gated dormant.** Cold / below-floor / insufficient-OOS → `ready=False`, `stage=SHADOW`, `None` stats. Mirrors
   the calibration `k` and maker `go`. The whole engine ships inert; the operator runs the shadow period to earn a GO.
6. **Fail LOUD / fail CLOSED.** Config + ledger fail LOUD at construction / on bad status / non-finite (the S8/S5
   discipline). The OOS/stress/evidence paths fail CLOSED — insufficient sample, a `None`/non-finite mark, or a raising
   leg → not-ready / not-survives, never a phantom GO. Decimals from strings; `is_finite()` before every compare.
7. **Purely additive but for one inert seam.** `git diff` touches only the new `harness/` package + its tests + the
   `ERSController(reconciler=None)` seam (proven byte-for-byte inert when unwired). The full suite stays green trivially
   (nothing imports `harness`; `reconciler=None` == today).

## 6. Built-now vs deferred

| Capability | Built now (pure, shadow) | Deferred (why safe) |
|---|---|---|
| Maker fill simulator (resting entry + reward) | ✅ over an injected book | live book feed + the live fills-recorder (S9 deploy) |
| Net-of-everything shadow ledger + windowed PnL | ✅ full, reuses S8 identity | the real resolution/dispute feed settling trades (deploy) |
| Walk-forward OOS + MC-penalized margin | ✅ full | the calibrated `mc_penalty`/thresholds (re-pull at deploy) |
| Brier-beats-mid + reliability + calibration `k` + maker `go` | ✅ composes S5+S8 | warming them to ≥150 resolved (needs deployed Hermes) |
| Dispute-freeze stress + tail-survival | ✅ full over the portfolio | real disputed resolutions + real stress episodes accruing |
| Binary stage machine (SHADOW/TINY_LIVE/RAMP) + advisory promote + auto ramp-down | ✅ full | the human ramp-UP action + Stage-1+ LIVE execution (POL-4) |
| `RestartReconciler` → `ERSController` boot | ✅ the DORMANT shadow path (`reconciler=None` seam) | the live on-chain∩ACCEPTED rebuild (POL-4) |
| Running the shadow period to produce the GO | — | needs a DEPLOYED Hermes + continuous ingestion (operational) |
| Stage-1+ live cap-widening | — | the operator's human ramp-up gate + POL-4 signer |

## 7. Acceptance criteria

1. Full suite green; the existing tests stay green (nothing imports `harness`; `reconciler=None` == today).
2. New TDD tests (RED→GREEN observed) per unit, incl. at minimum: `simulate_fill` maker-only (a resting maker price
   fills + accrues reward; a crossing/marketable price → `filled=False` reward 0; a stale/crossed/None-mid book →
   fail-closed; bad proposal → loud); the `ShadowLedger` round-trip + restart + idempotent + settlement-overwrite +
   WON/LOST-need-value / DISPUTED/VOID-need-None + loud-on-bad-status (mirroring the maker ledger); `window_net` = the
   S8 identity over a hand-computed multi-row window + DISPUTED/VOID excluded + empty→0; `evaluate_category`
   walk-forward (the OOS window is the recent slice; `net_oos` gates, not `net_full`), the MC-penalized `required_margin`
   (family_size inflates it), each Stage-0 gate in isolation (below `min_resolved`→not-ready; `k==0`→not-ready;
   `go=False`→not-ready; OOS net at-margin→not-ready), cold→not-ready with `None` stats; `dispute_freeze_stress` (a book
   that holds the reserve floor → survives; one that breaches → not; fail-closed on a bad field) + `tail_survived`
   boundaries; `RampController.decide` — SHADOW when not ready; `promote_recommended` only when ready AND tail AND
   stress AND no-breaker; `ramp_down` on a regression / a tripped breaker; a config `_verify` rejection per knob.
3. **The whole-slice e2e:** a `ShadowLedger` fed a category's worth of simulated fills + resolutions (some WON, some
   LOST, one DISPUTED) across a time span → `evaluate_category` shows the honest OOS breakdown, DISPUTED excluded,
   `ready=False` below `min_resolved` / when the OOS net is only at-margin, and `ready=True` once the OOS sample clears
   with margin AND `k`/`go` pass; `RampController.decide` stays SHADOW until ready+tail+stress, then emits
   `promote_recommended=True`; a subsequent regression (OOS net drops / a breaker trips) emits `ramp_down=True`; a
   structural assertion proves the controller **cannot** advance on gross/in-sample edge (the OOS-vs-full + net-vs-gross
   mutations are killed) and **cannot** loosen the ceiling (no cap-mutation surface); the `ERSController(reconciler=…)`
   boot seam adopts the DORMANT→RUNNING transition, and `reconciler=None` leaves `run_cycle` byte-for-byte.
4. Two-stage review per sub-slice (spec-compliance + pinned-opus with mutation batteries; pycache sweep after each
   mutation revert); re-review after any correctness fix; final whole-slice review with a cross-cutting mutation
   (make the evidence gate read `net_full` instead of `net_oos`; give the controller a cap-widening path).
5. HANDOFF/memory/POL-11 updated; branch `pol-11-s9-harness`; merge `--no-ff` with verification status;
   **confirm before push**.

## 8. Sub-slice decomposition (build order)

| # | Sub-slice | Contents |
|---|---|---|
| S9a | **Config + fill simulator** | `harness/config.py` (`RampConfig`, self-verifying) + `harness/fill_sim.py` (`simulate_fill` maker-only, reward via S8, fail-closed marks). Pure; the fill-gate + reward boundary cases. |
| S9b | **Shadow ledger + windowed PnL** | `harness/ledger.py` (`ShadowLedger` append-only SQLite mirroring `MakerLedger` — round-trip/restart/idempotent/loud) + `harness/pnl.py` (`window_net` = the S8 net identity over a time-window; DISPUTED/VOID excluded). |
| S9c | **Evidence evaluator + stress** | `harness/evidence.py` (`evaluate_category` — walk-forward OOS split + MC-penalized margin + Brier/reliability + reads `k_for`/`go_for`) + `harness/stress.py` (`dispute_freeze_stress` DECISIONS-S0 §4 + `tail_survived`). |
| S9d | **Ramp controller + boot seam + e2e** | `harness/ramp_controller.py` (`RampController.decide` binary stage machine — advisory promote, auto ramp-down, no cap surface, ceiling-clamped) + the `ERSController(reconciler=None)` boot seam + the §7.3 whole-slice e2e. |

Each sub-slice: strict TDD (observe the RED), then the two-stage review, serial on `pol-11-s9-harness`.
