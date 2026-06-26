# S3 — Execution & Risk Service (ERS) design

**Ticket:** [POL-5](https://mysigner.youtrack.cloud/issue/POL-5) · **Depends on:** S0 decisions (✅), S1 ingestion (✅);
the live sign+submit depends on S2/[POL-4](https://mysigner.youtrack.cloud/issue/POL-4) (blocked on a funded wallet).
Read [`DECISIONS-S0.md`](DECISIONS-S0.md) §4 (the risk envelope) and
[`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
§2/§4/§5 first; this resolves the ERS into buildable slices.

The ERS is the deterministic **hands + sole key-holder**: *"Hermes proposes; the ERS disposes."* It treats
every proposed field as an untrusted hint, re-fetches live state, recomputes size itself, runs every
guardrail, and either signs+submits or vetoes with a reason code. The guardrails **are** the judgment that
replaces the human; they live here, fail **closed**, and Hermes can never override them.

## Decomposition (each slice = its own TDD + Opus review + merge)

1. **Risk-engine validator (this slice).** A PURE function `evaluate_intent(intent, book, portfolio, caps)
   → Decision`: re-price off the live book, size ¼-Kelly on the executable price, clamp by every S0 cap,
   fail closed. Zero persistence/network/keys → fully testable against the §4 envelope with synthetic
   intents + books. This is the heart of "the ERS disposes."
2. **Chokepoint + ERS loop.** `Intent` model, mutable `pending_intents` store (status lifecycle
   `PROPOSED → ACCEPTED|REJECTED|DOWNSIZED|...`), `propose_trade(...)` INSERT-only + idempotency, an
   append-only audit log, the poll-loop that runs the validator + transitions status, and the **signer
   seam** (an abstract `Signer` — stubbed until S2/POL-4 lands).
3. **Later (some overlap S4):** learned co-move matrix (replaces the fail-closed cluster default), L7
   real-time unrealized-drawdown breaker, L4/L5 novelty/anomaly vetoes, daily/weekly loss breakers,
   3-way reconciliation, full multi-level book-walk slippage, the calibration `k` multiplier (S5).

**S6 obligation (chokepoint, from the slice-2 review):** the chokepoint invariant currently holds *as
wired* — Hermes is intended to get ONLY the `propose_trade` MCP tool; `IntentStore.record_decision` + the
signer are ERS-only *by convention* (same object, not a separate type). When S6 actually wires Hermes's MCP
tools, expose a **propose-only facade** (just `propose_trade`, no `record_decision`/signer reachable) so the
"Hermes can at worst enqueue" guarantee is load-bearing in code, surviving careless future wiring.

---

## Slice 1 — the validator

### Contract
`evaluate_intent(intent: TradeIntent, book: LocalBook, portfolio: Portfolio, caps: RiskCaps) → Decision`
— pure, deterministic, no side effects. The intent's numeric fields are UNTRUSTED hints; the book is the
re-fetched live truth; the portfolio is the ERS's own confirmed open-position state; the caps are the
signed S0 envelope.

### Models
```
TradeIntent (frozen):
  token_id           str      # the outcome token to BUY (a Yes leg, or a No leg = short-Yes)
  condition_id       str      # market            -> per-market cap
  event_id           str      # event             -> per-event UNION cap (NegRisk legs share it)
  resolution_source  str      # UMA source        -> per-source cap   (ERS-populated, NOT Hermes-trusted)
  cluster_id         str      # latent-driver cluster (fail-closed: matrix-cold intents share one)
  p                  Decimal  # Hermes P(this token resolves YES=$1), in [0,1] -- UNTRUSTED
  max_price          Decimal  # Hermes's price limit; we re-price off the live book and never pay above it
  size_usd_suggestion Decimal # requested size -- capped, NEVER trusted upward
  matrix_cold        bool     # is this intent's cluster correlation still UNKNOWN (-> +1 fail-closed)

OpenPosition (frozen): condition_id, event_id, resolution_source, cluster_id, worst_case_risk(Decimal),
                       matrix_cold(bool)         # worst_case_risk = notional for a long

Portfolio (frozen): nav(Decimal), positions(tuple[OpenPosition])   # all derived sums computed here

Decision (frozen): verdict("ACCEPT"|"REJECT"|"SKIP"), stake_usd(Decimal|None),
                   price_exec(Decimal|None), reason(str)           # reason = code; on ACCEPT = binding cap
```

### Algorithm (fail closed at every step — any ambiguity → REJECT/SKIP + reason)
1. **Re-price (touch).** `price_exec` = the side we'd pay = `book.best_ask()` (we always BUY an outcome
   token). If the book is stale, has no ask, or is crossed (`midpoint()` is `None`) → `REJECT(book_stale)`.
   If `price_exec > intent.max_price` → `SKIP(price_above_limit)` (the market moved away).
   *(Deferred: full multi-level book-walk to the intended size; slice 1 prices at the touch + a
   touch-depth liquidity cap.)*
2. **Edge.** `f_full = (p − price_exec)/(1 − price_exec)`. If `f_full ≤ 0` → `SKIP(no_edge)`.
   *(Deferred: the full stacked hurdle H — fees + slippage + calibration margin + adverse-resolution
   premium, §4.2. Slice 1 requires only a positive Kelly fraction.)*
3. **¼-Kelly stake.** `frac_eff = caps.kelly_fraction · min(1, calib_score)`;
   `kelly_stake = frac_eff · f_full · nav`. (calib_score is an input; ¼ at S0.)
4. **Clamp by caps** (all measured as worst-case mark-to-resolution loss = **notional for a long**). The
   stake (notional) must fit the REMAINING headroom under each, taking the min:
   - `per_trade` (≤ $12)
   - `per_market` headroom (≤ $18 − current market risk)
   - `per_event` UNION headroom (≤ $24 − current event risk)
   - `per_resolution_source` headroom (≤ $30 − current source risk)
   - `total_open` headroom (≤ $60 − current total) — equivalently the reserve floor (≥ $240)
   - `size_usd_suggestion` (Hermes's request — capped, NEVER trusted upward; an upper clamp only)
   - `liquidity` cap (≤ 10% of resting touch depth AND ≤ 1¢ impact) — *touch depth in slice 1*
   The binding cap is recorded in `Decision.reason`.
5. **Concurrency / fail-closed cluster gate.** If opening this position would exceed `max_concurrent`
   (≤ 4) → `REJECT(max_concurrent)`; or, when `intent.matrix_cold`, exceed `matrix_cold_concurrent`
   (≤ 3) → `REJECT(matrix_cold_concurrent)`. **This count cap IS the slice-1 cluster gate** (unknown
   correlation = +1): rather than invent a per-cluster dollar cap not in §4, matrix-cold positions are
   bounded by this ≤3 count + the global `total_open` cap. A learned co-move matrix with real per-cluster
   dollar caps is slice 3. (Accepted residual per DECISIONS-S0: this over-couples while the matrix is cold,
   so the bot may barely trade at S0 — fine for "don't blow up".)
6. **Min-floor / SKIP-don't-round.** If the clamped stake `< floor = max($5, min_order_size·price, tick)`
   → `SKIP(below_min_floor)` — NEVER round up to meet a cap.
7. **Result.** `ACCEPT(stake_usd, price_exec, reason=binding_cap)` else the `REJECT|SKIP(reason)` above.

### Reason codes
Fail-closed input guards: `book_stale · degenerate_price (price∉(0,1)) · bad_probability (p∉(0,1)) ·
bad_calibration (calib∉[0,1]) · price_above_limit`. Then: `no_edge · max_concurrent ·
matrix_cold_concurrent · below_min_floor`. Caps: `per_trade_cap · per_market_cap · per_event_cap ·
per_source_cap · total_open_cap · size_suggestion · liquidity_cap` (and on ACCEPT, the binding cap name
or `kelly` if Kelly itself bound).

### RiskCaps (the signed S0 envelope — DECISIONS-S0 §4, NAV = $300)
A frozen dataclass carrying the numbers, with **construction-time internal-consistency verification** that
fails LOUD on an inconsistent envelope (the invariants the S0 verification established), plus a **content
hash** for tamper-evidence (the seed of the "signed caps config"; a real signature + startup self-test is
S4). Invariants asserted at construction:
- `per_trade < daily_pending_ceiling < total_open_risk` (breaker ordering — the inverted-ordering bug §Verification #1)
- `max_concurrent · per_trade ≤ total_open_risk` (no zero-slack — §Verification #2)
- `reserve_floor == nav − total_open_risk` (one capital band, no triple-counting — §4)
- `total_open_risk ≤ 0.20 · nav` (the 20%-NAV taxonomy fix — §Verification #3)
- `min_position_floor ≥ 5`; `0 < kelly_fraction ≤ 0.5`; all caps > 0.

Default values: nav 300 · total_open 60 · per_trade 12 · max_concurrent 4 · matrix_cold_concurrent 3 ·
per_event_union 24 · per_market 18 · per_negrisk_event 18 · per_source_open 30 · per_source_locked 18 ·
max_locked_to_resolution 36 · reserve_floor 240 · daily_pending_ceiling 24 · kelly_fraction 0.25 ·
min_position_floor 5 · liquidity_depth_frac 0.10 · liquidity_impact_cents 1.

### Out of scope for slice 1 (deferred, tracked above)
Full book-walk slippage · the stacked hurdle H (fees/slippage/calibration/adverse-premium) · the learned
co-move matrix · the calibration `k` multiplier · locked-to-resolution / dispute-freeze accounting · the
L7 breaker · persistence / the chokepoint / the signer (slice 2).

### Testing
Pure-function TDD against the §4 envelope: the sizing math, the edge/no-edge boundary, the staleness/
crossed-book reject, the price-above-limit skip, EACH cap (an intent that would breach cap X → clamp or
reject with reason X), the matrix-cold sub-cap, the min-floor SKIP-don't-round, and the RiskCaps
inconsistency rejections. No network, no persistence.

---

## Slice 3 — learned co-move matrix + per-cluster cap + L7 breaker

**Status:** designed 2026-06-26 (operator-approved); ONE combined slice (both subsystems, one merge),
built as cleanly-isolated units. Replaces the fail-closed `matrix_cold=True / unknown-corr=+1` cluster
default in `evaluate_intent` with a *learned per-cluster dollar cap*, and adds the L7 real-time
unrealized-drawdown breaker. Both fail closed; the validator stays **provably bounded by the §4 envelope**.

> **Data-gated honesty:** in PRODUCTION the matrix stays **cold (≡ slice-1 behavior)** until enough
> price bars accrue per pair (needs continuous ingestion uptime). The machinery + EventStore data path are
> real, correct, point-in-time, and fully tested with synthetic series — just dormant until data exists.
> No live signer yet, so FLATTEN/FREEZE emit signals+alerts through the seam; real venue de-risking (GTD
> brackets / `cancelAll`) is S4/POL-4.

### Operator decisions resolved (the genuinely-open forks §4 did not fix)
1. **Co-move signal = price-snapshot co-movement** — pairwise Pearson on per-market midpoint returns from
   Market-Memory snapshots; a pair turns *warm* once ≥N paired bars accrue. (Resolution-outcome co-movement
   deferred — it warms in weeks, not hours.)
2. **Per-cluster dollar cap = correlation-scaled (earned relaxation):**
   `cluster_cap(ρ) = per_trade + (1 − ρ)·(total_open_risk − per_trade)`, clamped `[per_trade, total_open]`,
   ρ = MAX pairwise corr in the cluster. ρ→1 ⇒ $12 (cluster ≡ one bet); ρ→0 ⇒ $60 (only the global ceiling
   binds). A PROVEN-independent cluster earns the right to hold >3 positions, still under `total_open`.
3. **L7 unrealized mark = mark-to-mid** (stable; a thin-book bid must not spuriously fire the drastic
   FLATTEN; pairs with the existing `midpoint()==None ⇒ stale ⇒ fail-closed` guard).

### Units
| Unit | File | Pure? |
|---|---|---|
| Co-move estimator + `ClusterModel` + cap formula | `ers/comove.py` | pure |
| EventStore → midpoint-bar adapter (single forward pass; reuses `replay` incremental reconstruction) | `ers/comove.py` | reads store |
| `DrawdownBreaker` (L7, stateful) | `ers/breaker.py` | stateful |
| Validator integration (`ClusterView` + `per_cluster_cap` + `Portfolio.cluster_risk`) | `ers/validator.py` | pure |
| `OpenPosition` + `RiskCaps` extensions | `ers/validator.py`, `ers/caps.py` | — |
| Service wiring (breaker-first, per-intent `ClusterView`, richer `_fold`, FLATTEN/FREEZE seam) | `ers/service.py` | — |

### Co-move estimator + cluster cap
- **ρ:** Pearson correlation of simple midpoint returns (Δmid per bar) over aligned bars. A *pair* is **warm**
  iff it has ≥ `comove_min_observations` paired bars within the rolling `comove_window`; otherwise its ρ is
  fail-closed to **1.0** (unknown correlation = +1).
- **Cluster warmth:** a cluster is warm iff *every* member pair (the intent vs each existing same-cluster
  position) is warm. Any unknown pair ⇒ cluster **cold**.
- **ρ_cluster = max pairwise** (the tightest cap).
- **Estimation params** (`comove_min_observations`=30, `comove_bar_seconds`=60, `comove_window`≈240 bars)
  live in `ClusterModel` config — operator-tunable like the synthetic thresholds. They are NOT in the signed
  `RiskCaps` (which holds only risk *limits*). The cap *formula* uses `caps.per_trade` + `caps.total_open_risk`.

### Validator integration
- New input `cluster: ClusterView(warm: bool, rho: Decimal | None)`; default `ClusterView(False, None)` =
  fail-closed cold.
- **Cold** (`warm=False`): unchanged slice-1 path — `matrix_cold=True`, the ≤3 count gate, NO dollar cluster
  cap.
- **Warm** (`warm=True`): `matrix_cold=False` (leaves the count gate); add one `min()` headroom candidate
  `(cluster_cap(rho) − portfolio.cluster_risk(cluster_id), "per_cluster_cap")`. `warm=True` with `rho is None`
  ⇒ wiring error ⇒ **fail closed** (`REJECT`).
- New `Portfolio.cluster_risk(cluster_id)` (mirrors `market_risk`). New ACCEPT binding-reason
  `per_cluster_cap`. The engine stays provably bounded: the new term can only *tighten* (a `min()` candidate);
  warm only ever *relaxes the count gate* while every dollar cap (incl. `total_open` $60) still binds.

### L7 `DrawdownBreaker`
- Each cycle, for each **non-frozen** open position: `shares = worst_case_risk / entry_price`;
  `mid = book_for(token_id).midpoint()`; unrealized P&L = `shares·(mid − entry_price)`. Portfolio
  **drawdown = −Σ P&L** (positive when losing; net — "total open unrealized exposure").
- **Triggers** (strongest action wins), thresholds from `RiskCaps` (NAV $300, §4 L7):
  drawdown > **$30** ⇒ `FLATTEN`; > **$18** ⇒ `FREEZE_ADDS`; **velocity** (drawdown rose > **$18** within a
  trailing **15 min** window, inclusive edge) ⇒ `FREEZE_ADDS` + alert; any non-frozen position
  **un-markable** (stale/None mid) ⇒ `FREEZE_ADDS` + `stale_mark` alert (the stale-mark watchdog — NEVER
  FLATTEN blind); and (review M1) any **single** non-frozen position whose unrealized loss exceeds the
  freeze floor ⇒ `FREEZE_ADDS` + `position_loss`, so the NET drawdown can't mask one catastrophic position
  behind another's paper gain (dormant at v1's $12 per-trade cap, load-bearing as caps scale).
- **Frozen** positions (disputed/frozen): excluded from drawdown + velocity, but still count toward
  `total_open` in the validator (§4). Holds a bounded `(clock_ts, drawdown)` deque for the velocity window;
  clock injected for deterministic tests.

### Model / caps / service changes
- `OpenPosition` += `token_id: str`, `entry_price: Decimal`, `frozen: bool = False` (`shares` derived).
  `service._fold` populates `token_id` + `entry_price = decision.price_exec`.
- `RiskCaps` += `l7_freeze_floor=18`, `l7_flatten_floor=30`, `l7_velocity_delta=18`,
  `l7_velocity_window_seconds=900`, with construction-time checks (`0 < freeze < flatten ≤ total_open`;
  `velocity_delta > 0`; `window > 0`). `content_hash` covers them automatically.
- `service.process_pending` += optional `cluster_model=None` (None ⇒ all cold, fail-closed back-compat) and
  `breaker`/`clock`. **Order per cycle:** run the breaker FIRST → if `FLATTEN`, signal flatten via the seam +
  reject all new intents (`l7_flatten`); if `FREEZE_ADDS`, reject all new intents (`l7_freeze`) but hold
  existing; else process intents, building each `ClusterView` from `cluster_model`. `PaperSigner` gains a
  shadow `flatten(positions)`.

### Reason / action codes (added)
Validator ACCEPT binding: `per_cluster_cap`. Service REJECT: `l7_freeze`, `l7_flatten`. Breaker triggers:
`freeze_floor`, `flatten_floor`, `velocity`, `stale_mark`.

### Testing (strict TDD, synthetic data — no network/keys)
Estimator (ρ=±1/0, <N⇒cold, window/recency) · cap math (ρ=1→$12, ρ=0→$60, monotone, clamped) · validator
warm path (low-ρ admits a 4th position the cold ≤3 would block, still clamps at `cluster_cap` & `total_open`;
high-ρ clamps aggregate ~$12 with reason `per_cluster_cap`; warm-no-ρ fails closed; **all 21 existing
cold-path validator tests stay green**) · L7 (each trigger, frozen excluded, stale⇒freeze, profit⇒NONE,
strongest-wins, velocity window) · service (FREEZE/FLATTEN block new ACCEPTs, `cluster_model=None`
back-compat, `_fold` populates marks) · caps (new-field consistency rejections). **Two Opus reviews:** an
interim pass after the validator+breaker core (the safety-critical heart), a closing pass at the end.

### Out of scope for slice 3 (deferred)
Resolution-outcome co-movement · a dedicated live minute-bar snapshotter (vs replay-reconstruction; perf
optimization) · **a real latent-cluster assignment** (review M2: `cluster_id` is the `event_id` placeholder
today, so the per-cluster cap aliases the per-event cap and only over-couples within an event — cross-event
latent drivers need a real assignment, itself a natural consumer of this co-move matrix) · the ≤1¢ liquidity
**impact term** (`liquidity_impact_cents` carried but only `liquidity_depth_frac` enforced) · the calibration
`k` multiplier (S5) · full book-walk slippage / the stacked hurdle H · locked-to-resolution / dispute-freeze
accounting beyond the `frozen` flag · 3-way reconciliation · the real signer + actual venue
FLATTEN/cancelAll (S2/POL-4, S4).

### Note: where `cluster_cap` lives
The per-cluster cap formula is a `RiskCaps.cluster_cap(rho)` METHOD (caps own the envelope math); `comove.py`
owns correlation/warmth, and the validator composes them with no import cycle.
