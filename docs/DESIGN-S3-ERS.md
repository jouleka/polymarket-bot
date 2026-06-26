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
