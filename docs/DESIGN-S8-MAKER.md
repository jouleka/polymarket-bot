# DESIGN — S8 / POL-10: Maker-rewards module (honest net-of-adverse-selection accounting)

**Date:** 2026-07-03 · **Ticket:** [POL-10](https://mysigner.youtrack.cloud/issue/POL-10) (S8) ·
**Status:** DESIGN (forks operator-resolved 2026-07-03; awaiting operator spec review → writing-plans).
**Depends on:** S7 (the D1 `detectors/toxicity.py` `pull_quotes` seam — a dormant `bool`), S3/S4 (the `fills`
ledger's `shares`+`price_exec` cost basis; `book_for`/`LocalBook.midpoint()`; the `RiskCaps` liquidity/locked
caps), the S5 `calibration/` package (the append-only-ledger → pure-scorer → binary-GO/NO-GO-tracker → facade
SHAPE this mirrors). **Feeds S9/POL-11** (the shadow harness ANDs S8's GO flag with the calibration `k`).
**Runs SHADOW-ONLY over simulated fills + injected marks.** Nothing quotes, signs, or sends.

> Master design §6 "Maker adverse selection" (the load-bearing requirement, verbatim): *"the 'safe' strategy
> bleeds invisibly. Break-even-adverse-move = `daily_reward/order_size` (tiny). → Account rewards **net of
> mark-to-resolution of accumulated inventory**, in real time; wire D1 toxicity to a **HARD pull-quotes**
> trigger; stop quoting a market when recent adverse move exceeds break-even; **L8 ramp requires positive
> net-of-cost PnL, never reward-gross**; do NOT default maker-only as 'safe.'"* Honest stance (DECISIONS-S0 §3):
> the base case is **break-even-to-negative**; S8's job is to MEASURE net honestly and refuse to certify a
> category until the shadow sample proves net-positive with margin.

---

## 0. TL;DR + resolved forks

S8 is a NEW self-contained shadow-analytics package `src/polybot/maker/` — the same SHAPE as `calibration/`:
a durable append-only ledger → pure exact-Decimal cost/PnL calculators → a binary GO/NO-GO tracker → a thin
facade, all data-gated (dormant until the sample proves out), plus a quote-policy unit that consumes the D1
`pull_quotes` seam. It is purely ADDITIVE (no wiring into the sacred ERS loop — like `calibration`/`detectors`,
it is consumed later, by S9). The central identity it computes, honestly and never reward-gross:

```
net = reward + rebate + spread_capture  −  adverse_selection  −  fees  −  lockup_cost  −  dispute_haircut
```

**Resolved forks (operator-confirmed 2026-07-03):**

| # | Fork | Decision |
|---|---|---|
| 1 | Scope | **Accounting + quote-policy + gate** — the full honest module: (a) a durable maker-fill/reward shadow ledger; (b) the pure net-of-cost calculator (the identity above); (c) a quote-policy unit (QUOTE / WIDEN / PULL) that CONSUMES `pull_quotes` + the `daily_reward/order_size` break-even + the `locked_effective` rule; (d) a binary GO/NO-GO gate S9 ANDs with the calibration `k`; (e) a self-verifying `MakerConfig`. Reward/fee numbers + live feeds are parameterized deferred seams. |
| 2 | Adverse-selection mark | **Mark-to-resolution/mid of accumulated inventory** — `adverse_selection = Σ shares × (fill_price − current_mark)` where `current_mark` = `LocalBook.midpoint()` live (interim) and the resolution value ($1 win / $0 lose) at settle (the doc's exact prescription; buildable from `book_for`/`build_bar_series` + the fills ledger). The `pull_quotes` toxicity seam is SEPARATE — it gates whether to keep quoting, it is NOT the mark. No missing feed. |
| 3 | Fee model | **Parameterized, dossier-corrected** — a per-category `FeeSchedule` in self-verifying config: `fee = C · feeRate · p · (1−p)^exponent`, sports active (feeRate 0.03, exp 1), other categories config-flagged (planned/inactive → fee 0), geopolitics free. Numbers are re-pullable deferred config, not hardcoded constants (the docs say "unspecified / re-pull live at build time"; the dossier is the later adversarial correction). S8 is MAKER → pays 0 fees; the fee model drives the forced-taker-exit hurdle + the 20–25% rebate. |

**Baked (doctrine-forced, not asked):** the gate is BINARY `go∈{False,True}` per category, mirroring the S5
`k∈{0,1}` (at $300 the sample is a GO/NO-GO gate, not a size dial); GO requires `n_settled ≥ min_samples` AND
`net_of_cost > net_margin_min` (positive WITH margin) AND — by construction in `netpnl.py` — the net already
subtracts adverse/fees/lockup/dispute (never reward-gross); DISPUTED/VOID settlements are counted separately and
EXCLUDED from the net-PnL sample (whale-flip immunity, mirroring calibration's DISPUTED_LOST exclusion); every
numeric is exact `Decimal` from strings; every config fails LOUD at construction; the ledger is append-only,
restart-stable, idempotent, and fails loud on non-finite / unknown status (mirroring `ForecastLedger`); a NaN/
non-finite mark fails CLOSED (conservative — counts as adverse, never as a phantom gain); the quote-policy PULLs
(fail-safe) under ANY of {pull_quotes, adverse>break_even, locked_effective over cap}.

---

## 1. Goal & non-goals

**Goal:** the honest maker-economics accounting + the quote-policy + the GO/NO-GO gate, as pure units over
defined input shapes (simulated fills, injected marks, a parameterized reward/fee model), unit-tested against
hand-computed cases, data-gated dormant. Concretely the eight units in §4.

**Non-goals (deferred; §7):** live order placement / queue-position modeling (POL-4 — no signer for maker
quotes exists); real Polymarket reward-pool data + the exact `S(v,s)=(v−s/v)²·b` → pool mapping (deploy
calibration; the numbers are documented-unspecified); the resolution feed that flips a fill to WON/LOST → $1/$0
(S6 resolution-feedback / S9); a true aggressor-signed order-flow VPIN adverse measure (needs a `/trades`/
`last_trade_price` parser that does not exist — we use mark-out instead, Fork 2); the live wiring of the
recorder into a running quoting loop (S9 harness assembly). **No change to any existing file** — S8 is a new
package; it touches nothing in `ers/`, `detectors/`, `calibration/`, `ingestion/` except by READING their
public types (it consumes `pull_quotes` as a plain bool and `book_for`/`midpoint` as injected callables).

## 2. Architecture

```
src/polybot/maker/  (NEW package — mirrors calibration/ shape; purely additive; consumed by S9)

  config.py    MakerConfig (frozen, self-verifying) + FeeSchedule (per-category feeRate/exponent/active/free)
  fees.py      taker_fee(category, p, size, schedule) -> Decimal ; rebate(taker_fee, fraction) -> Decimal
  inventory.py MakerFill (frozen) ; accumulate(fills) -> {token: net_shares, cost} ;
               adverse_selection(fills, mark_for) -> Decimal   # Σ shares × (fill_price − mark);  mark = mid | $1/$0
  reward.py    spread_score(v, s, *, b) -> Decimal  (the documented S(v,s)=(v−s/v)²·b) ;
               reward_accrual(eligible, config) -> Decimal  (modeled; exact pool mapping = deploy calibration)
  netpnl.py    net_pnl(legs) -> MakerNetPnL   # the identity: reward+rebate+spread − adverse − fees − lockup − dispute
               (a frozen breakdown dataclass: every leg + the net; NEVER exposes reward-gross as "the number")
  ledger.py    MakerLedger  (append-only SQLite; mirrors ForecastLedger) — records each shadow maker fill +
               its accrued reward + its eventual mark/resolution; the substrate the tracker scores over
  quote_policy.py  decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, config) -> QuoteAction
               # QUOTE | WIDEN | PULL  — CONSUMES the D1 pull_quotes seam; PULL fail-safe under any trigger
  gate.py      MakerTracker (binary go per category over the ledger's net-of-cost sample) + MakerGate (facade:
               go_for/report_for/net_for + decide_quote) — the SINGLE seam S9 wires into (ANDs with calibration k)
```

- **Shadow, data-gated, dormant** (the S5 pattern): the ledger accrues simulated fills + marks; `go_for(cat)`
  returns `False` until `n_settled ≥ min_samples` AND the net-of-cost margin clears. Cold (`n=0`) → all stats
  `None`, `go=False`. S9 ramps a category live ONLY when the calibration `k` AND this `go` both say GO.
- **Honest by construction:** `net_pnl` returns a `MakerNetPnL` breakdown where the `.net` field is the
  after-all-costs figure and each cost leg is a named field — there is no accessor that returns
  reward+rebate+spread alone as "the PnL." The gate reads `.net`, never a gross leg.
- **Separation of concerns:** the adverse-selection MARK (inventory × mark-delta, Fork 2) is distinct from the
  quote-PULL SIGNAL (D1 `pull_quotes`, a toxicity bool). The mark measures realized bleed; the signal decides
  whether to keep resting. `quote_policy` also PULLs when `recent_adverse > break_even` (the doc's "stop quoting
  when recent adverse move exceeds break-even").
- **Purity + injection:** every unit is pure over injected inputs — `mark_for(token) -> Decimal|None` (a
  callable, `LocalBook.midpoint` live or `build_bar_series` historical or the resolution value at settle);
  the fee schedule + reward params from `MakerConfig`. No I/O except the `MakerLedger` SQLite (which mirrors
  `ForecastLedger` exactly). Decimals from strings throughout.

## 3. The net-of-cost identity (each leg)

| Leg | Sign | Source | Notes |
|---|---|---|---|
| `reward` | + | `reward.py reward_accrual` | modeled: normalized share of the per-market pool via the quadratic `S(v,s)=(v−s/v)²·b` (v=depth, s=spread-from-mid, b=pool constant — all config/inputs; exact Polymarket mapping = deploy calibration) |
| `rebate` | + | `fees.py rebate` | 20–25% of taker fees paid back to makers (`config.rebate_fraction`, range-checked (0,0.5]) |
| `spread_capture` | + | inventory round-trips | the half-spread the maker earns entry-to-exit (from the fills ledger's `price_exec` deltas) |
| `adverse_selection` | − | `inventory.py` (Fork 2) | `Σ sgn(side)·shares·(fill_price − current_mark)` (signed, two-sided-correct); mark = `midpoint()` interim / $1\|$0 at settle; NaN/None mark → fail CLOSED (counts as adverse) |
| `fees` | − | `fees.py taker_fee` | 0 for a pure maker; nonzero only on a forced-taker exit — folds `P(forced taker exit) × taker_fee(category)` (the DECISIONS-S0 hurdle), `P` a config/modeled input |
| `lockup_cost` | − | config × time-to-resolution | the opportunity cost of capital locked to resolution (the `locked_effective` inventory within T-48h etc.); `lockup_rate` config |
| `dispute_haircut` | − | config × P(dispute) | the UMA dispute/void expected-loss haircut; DISPUTED/VOID settled fills are EXCLUDED from the sample entirely (they can't poison the net), and this leg prices the ex-ante expectation |

`net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost − dispute_haircut`. The gate's
GO condition is on `net`, per category, with margin.

## 4. Net-new units (the pinned contract block)

```python
# maker/config.py
@dataclass(frozen=True)
class FeeCategory: name: str; fee_rate: Decimal; exponent: Decimal; active: bool; free: bool
@dataclass(frozen=True)
class MakerConfig:
    fee_schedule: tuple            # tuple[FeeCategory, ...] — sports active(0.03,1); others planned(inactive); geopolitics free
    rebate_fraction: Decimal = Decimal("0.20")     # (0, 0.5]
    reward_b: Decimal = Decimal("1")               # the S(v,s) pool constant (deploy-calibrated)
    max_spread: Decimal = Decimal("0.03")          # reward eligibility: rest within this of mid (0,1)
    min_samples: int = 150                         # GO/NO-GO floor per category (mirrors calibration min_n)
    net_margin_min: Decimal = Decimal("0")         # net must exceed this (positive WITH margin) to GO
    lockup_rate: Decimal = Decimal("0")            # per-day opportunity cost of locked-to-resolution capital
    forced_taker_exit_p: Decimal = Decimal("0")    # P(forced taker exit) in the hurdle (modeled input)
    dispute_p: Decimal = Decimal("0")              # ex-ante P(dispute/void) for the haircut leg
    # __post_init__ -> _verify(): fee_rate/exponent >= 0, rebate in (0,0.5], 0<max_spread<1, min_samples>0,
    #   net_margin_min>=0, lockup_rate>=0, forced_taker_exit_p in [0,1], dispute_p in [0,1] -> ValueError else

# maker/fees.py  (pure Decimal; no fee code exists anywhere today)
def taker_fee(category, p, size, *, schedule) -> Decimal   # free/inactive -> 0; else C·feeRate·p·(1−p)^exp, C=size
def rebate(taker_fee_paid, *, fraction) -> Decimal          # fraction · taker_fee_paid

# maker/inventory.py
@dataclass(frozen=True)
class MakerFill: token_id: str; condition_id: str; category: str; side: str; shares: Decimal; price_exec: Decimal; fill_mid: Decimal
def net_inventory(fills) -> dict                          # token -> (net_shares, avg_cost) folding BUY(+)/SELL(−)
def adverse_selection(fills, mark_for) -> Decimal
#   Σ sgn(side)·shares·(price_exec − mark_for(token))  with sgn(BUY)=+1, sgn(SELL)=−1  (SIGNED so a two-sided
#   maker's ASK getting hit — a SELL — books correctly: a BUY bleeds when mark<fill, a SELL bleeds when mark>fill;
#   both yield a POSITIVE adverse cost, which the identity subtracts). A None/NaN mark fails CLOSED (that token's
#   inventory counts full adverse at its cost, never a phantom gain).

# maker/reward.py
def spread_score(v, s, *, b) -> Decimal                    # the documented S(v,s) = (v − s/v)² · b ; s/v guarded (v>0)
def reward_accrual(eligible_size, spread_from_mid, *, config) -> Decimal   # 0 if spread_from_mid > max_spread

# maker/netpnl.py
@dataclass(frozen=True)
class MakerNetPnL:
    reward: Decimal; rebate: Decimal; spread_capture: Decimal
    adverse_selection: Decimal; fees: Decimal; lockup_cost: Decimal; dispute_haircut: Decimal
    net: Decimal            # the after-ALL-costs figure; the ONLY number the gate reads (never a gross leg)
def net_pnl(*, reward, rebate, spread_capture, adverse_selection, fees, lockup_cost, dispute_haircut) -> MakerNetPnL

# maker/ledger.py  (append-only SQLite; mirrors calibration/ledger.py ForecastLedger exactly)
class MakerLedger:
    def __init__(self, path, stamper): ...                 # WAL; stamper.stamp() monotonic ts; Decimals as exact strings
    def record_fill(self, fill_id, *, token_id, condition_id, category, side, shares, price_exec, fill_mid,
                    reward_accrued) -> bool                # True new / False dup (idempotent on fill_id)
    def record_settlement(self, fill_id, *, status, resolution_value) -> None   # WON|LOST|DISPUTED|VOID; overwrites; loud on bad status/id
    def settled(self, category=None) -> list               # only fills with a status set
    def all(self) -> list

# maker/quote_policy.py
QUOTE="QUOTE"; WIDEN="WIDEN"; PULL="PULL"
def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap, config) -> str
#   PULL if pull_quotes OR recent_adverse > break_even OR locked_effective > locked_cap  (fail-safe, any trigger)
#   else WIDEN if recent_adverse > 0                        (bleeding but under break-even -> widen, don't pull)
#   else QUOTE.   break_even = daily_reward / order_size (the doc's break-even-adverse-move).

# maker/gate.py  (mirrors calibration/tracker.py + gate.py)
@dataclass(frozen=True)
class MakerReport:
    category: str; n_settled: int; n_disputed: int; n_void: int
    reward: Decimal|None; rebate: Decimal|None; spread_capture: Decimal|None
    adverse_selection: Decimal|None; fees: Decimal|None; lockup_cost: Decimal|None; dispute_haircut: Decimal|None
    net: Decimal|None; go: bool
class MakerTracker:
    def __init__(self, ledger, config): ...
    def report_for(self, category) -> MakerReport          # net-of-cost over the settled(cat) sample; DISPUTED/VOID excluded from net
    #   go = (n_settled >= config.min_samples) and (net > config.net_margin_min)   # binary; cold/below-floor -> False
class MakerGate:                                           # the thin facade — the single seam S9 wires into
    def __init__(self, ledger, config): ...
    def go_for(self, category) -> bool
    def report_for(self, category) -> MakerReport
    def decide_quote(self, **kw) -> str                    # delegates to quote_policy.decide_quote
```

**No `RiskCaps`/sacred changes. No new op-audit kinds.** S8 is a standalone package; it reads `pull_quotes`
(bool) and `mark_for` (callable) as injected inputs — it imports nothing from `ers/` at module load.

## 5. Safety / honesty invariants

1. **Never reward-gross.** `MakerNetPnL.net` is the after-all-costs figure; the gate reads ONLY `.net`. A
   structural test asserts `net == reward+rebate+spread_capture − adverse − fees − lockup − dispute` exactly,
   and that the tracker's GO reads `.net` (a mutation making GO read a gross leg is killed).
2. **Binary, data-gated, dormant.** `go` is `False` until `n_settled ≥ min_samples` AND `net > net_margin_min`;
   cold (`n=0`) → `False` with `None` stats. Mirrors calibration `k`. S9 ANDs it with `k`.
3. **DISPUTED/VOID excluded from the net sample** (whale-flip immunity); counted separately in the report.
4. **Fail CLOSED on a bad mark.** A None/NaN `mark_for(token)` → that inventory counts full adverse (never a
   phantom gain). A NaN anywhere → conservative (the calibration `clamp01`/fail-closed doctrine).
5. **Quote-policy PULLs fail-safe** under ANY trigger (toxic flow / adverse>break_even / locked over cap); the
   default is the safe action, never "keep quoting under ambiguity."
6. **Config + ledger fail LOUD** at construction / on bad status / on non-finite (the `ForecastLedger` +
   `DetectorConfig` discipline). Decimals from strings throughout.
7. **Purely additive.** `git diff` touches only the new `maker/` package + its tests. Zero change to any
   existing file (the 853 stay green trivially — nothing imports `maker` yet).

## 6. Built-now vs deferred

| Capability | Built now (pure, shadow) | Deferred (why safe) |
|---|---|---|
| Net-of-cost identity + honest breakdown | ✅ full | — |
| Adverse-selection mark (inventory × mark-delta) | ✅ over injected `mark_for` | live `book_for`/resolution feed wiring (S9) |
| Parameterized fee schedule + rebate | ✅ config-driven | the real live schedule numbers (re-pull at deploy) |
| Reward accrual (S(v,s) quadratic) | ✅ parameterized | the exact Polymarket pool→score mapping + `b` (deploy calibration) |
| Quote-policy (QUOTE/WIDEN/PULL) consuming pull_quotes | ✅ full | live wiring into a running quoting loop (S9) |
| Shadow ledger + binary GO/NO-GO gate | ✅ full, data-gated | the live fills recorder + resolution feed (S9/POL-4) |
| Lockup + dispute-haircut legs | ✅ parameterized | the real P(dispute)/time-to-resolution feeds (S6/S9) |
| Live order placement / queue position | — | POL-4 (no maker signer exists) |
| Aggressor-flow VPIN adverse measure | — | needs a `/trades` parser (Fork 2 uses mark-out instead) |

## 7. Acceptance criteria

1. Full suite green; the existing **853 stay green** (nothing imports `maker` — purely additive).
2. New TDD tests (RED→GREEN observed) per unit, incl. at minimum: the fee formula per category (active sports
   vs planned-inactive→0 vs geopolitics-free→0; the `p·(1−p)^exp` shape; a boundary at p=0.5); rebate =
   fraction×fee; `net_inventory` BUY/SELL folding; `adverse_selection` sign (mark below fill = +adverse; mark
   above = negative adverse) + the fail-closed None/NaN mark; `spread_score` = the exact `(v−s/v)²·b` +
   `reward_accrual` zero outside max_spread; the `net_pnl` identity (a hand-computed all-legs case + the
   structural "net excludes nothing" mutation); the ledger round-trip + restart-persistence + idempotent
   fill + settlement-overwrite + loud-on-bad-status (mirroring the calibration ledger tests); `decide_quote`
   all three actions + each PULL trigger in isolation + the break-even boundary; the tracker's binary GO
   (cold→False, below min_samples→False, positive-net-with-margin→True, net-at-margin→False, DISPUTED/VOID
   excluded from net); a config `_verify` rejection per out-of-range knob; `MakerConfig` self-verifies.
3. **The whole-slice e2e:** a `MakerLedger` fed a category's worth of simulated fills + marks + settlements
   (some WON, some LOST, one DISPUTED) → `report_for(cat)` shows the honest net-of-cost breakdown, DISPUTED
   excluded, `go=False` below `min_samples` and `go=True` only once the sample clears AND net>margin; a toxic
   `pull_quotes=True` cycle makes `decide_quote` return PULL; a category whose adverse-selection exceeds
   reward+rebate+spread reports `net<0` and `go=False` (the "bleeds invisibly" case caught).
4. Two-stage review per sub-slice (spec-compliance + pinned-opus with mutation batteries; pycache sweep after
   each mutation revert); re-review after any correctness fix; final whole-slice review.
5. HANDOFF/memory/POL-10 updated; branch `pol-10-s8-maker`; merge `--no-ff` with verification status;
   **confirm before push**.

## 8. Sub-slice decomposition (build order)

| # | Sub-slice | Contents |
|---|---|---|
| S8a | **Config + fees** | `maker/config.py` (`MakerConfig`/`FeeCategory`/`FeeSchedule`, self-verifying) + `maker/fees.py` (`taker_fee` per-category dossier-corrected formula + `rebate`). Pure; the fee-schedule boundary cases. |
| S8b | **Inventory + reward** | `maker/inventory.py` (`MakerFill`, `net_inventory` folding, `adverse_selection` mark-to-mark with fail-closed marks — Fork 2) + `maker/reward.py` (`spread_score` = the S(v,s) quadratic, `reward_accrual` with the max_spread gate). |
| S8c | **Net-PnL + ledger** | `maker/netpnl.py` (`MakerNetPnL` breakdown + `net_pnl` — the identity, honest-by-construction) + `maker/ledger.py` (`MakerLedger` append-only SQLite mirroring `ForecastLedger` — round-trip/restart/idempotent/loud). |
| S8d | **Quote-policy + gate + e2e** | `maker/quote_policy.py` (`decide_quote` consuming `pull_quotes` + break-even + locked_effective) + `maker/gate.py` (`MakerTracker` binary GO/NO-GO over the net sample + `MakerGate` facade) + the §7.3 whole-slice e2e. |

Each sub-slice: strict TDD (observe the RED), then the two-stage review, serial on `pol-10-s8-maker`.
