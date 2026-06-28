# DESIGN — S6 / POL-8: Hermes integration + signal fusion + citation truth-gate

**Date:** 2026-06-28 · **Ticket:** [POL-8](https://mysigner.youtrack.cloud/issue/POL-8) (S6) ·
**Status:** DESIGN (brainstorm complete, awaiting operator review → writing-plans).
**Depends on:** S1 (ingestion/EventStore/LocalBook), S3 (ERS validator/intent_store/service/comove/breaker),
S5 (calibration: gate/anchor/tracker/prior/ledger), S7 (detectors). **Runs end-to-end on `PaperSigner`** —
no keys, no real signing (that is S2/POL-4, blocked on a funded clean-box wallet).

> Read [`CONTEXT.md`](CONTEXT.md), [`DECISIONS-S0.md`](DECISIONS-S0.md) §4/§6, the master design
> [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
> §2/§3.5/§4.1/§4.2/§5, and [`DESIGN-S3-ERS.md`](DESIGN-S3-ERS.md) / [`DESIGN-S5-CALIBRATION.md`](DESIGN-S5-CALIBRATION.md)
> / [`DESIGN-S7-DETECTORS.md`](DESIGN-S7-DETECTORS.md) first. This doc resolves S6's open forks and decomposes it.

---

## 0. TL;DR — what S6 is, and the scope decisions

S6 is **~80% integration of already-tested standalone units** (S3 validator, S5 calibration, S7 detectors,
S3 comove/breaker) **+ 3 net-new pure modules** (signal fusion, citation truth-gate, the propose-only facade)
**+ 1 small ingestion extension** (publisher-group independence) **+ 1 net-new sidecar** (per-signal component
log) **+ 1 reviewed config artifact** (`~/.hermes/config.yaml`). It makes "Hermes proposes; the ERS disposes"
real, end-to-end, in shadow. **`validator.evaluate_intent` is NOT modified — S6 wires *around* the pure
validator, never into it.**

**Resolved forks (operator-confirmed 2026-06-28):**

| # | Fork | Decision |
|---|---|---|
| 1 | How much §4.1 fusion now | **Structured fixed-weight v1.** Build the log-odds fold as a pure `FusionEngine` with market-mid prior, FIXED bootstrap weights, `recalibrate()` as a typed identity stub behind a seam. **DEFER** the adaptive EMA/auto-zero weights + isotonic recalibrator (data-gated — needs a warm ledger). |
| 2 | Fusion Bot-side or Hermes hands one `p` | **Bot-side, unambiguously.** Hermes's `p` enters ONLY as `p_news`, hard-capped `w ≤ 0.25`; the Bot computes `p_base/p_micro/p_flow` and folds with market-mid as prior. |
| 3 | Facade transport | **In-process Python type + reviewed `config.yaml` artifact.** DEFER the live MCP server transport + the real-Hermes injection probe (no VPS exists). |
| 4 | Truth-gate location | **ERS-side, post-INSERT, in `process_pending`** — keeps `propose_trade` a dumb idempotent INSERT; verification lives in the trusted re-derivation path with the live book in hand; refusals are *audited REJECTs*. |
| 5 | Source independence model | **Add a `publisher_group` field to `Source`** (default = registrable domain); independence = distinct group. Closes the confirmed `fed-press`/`fed-monetary` same-domain bypass AND wire syndication. Operator-reviewed alongside the allowlist. |
| 6 | Where category / `question_text` come from | **Stub at MVP** (single `"unknown"` category bucket → `k=0` → paper-only by design; `question_text` from the proposal's `resolution_summary`; `seconds_to_resolution` a clearly-marked seam). DEFER the real `MarketRegistry` (Gamma metadata) to the calibration-warming slice. Do **NOT** collapse the `k_for` and `prior_for` keyspaces. |
| 7 | §4.2 stacked edge hurdle H | **DEFER entirely** (out of POL-8 scope; deferred through S3 slice 3; paper-only `k=0` makes realistic ACCEPTs moot now). Pair H with the `MarketRegistry` later. |
| 8 | Real cross-event latent clusters | **Inherit the `cluster_id == event_id` placeholder** (fails *safe* — over-couples within an event) and just construct+inject the existing `ClusterModel`. Add a **one-line pluggable `cluster_id` hook** in `_cluster_view` so the real latent-cluster slice never re-edits the loop. |
| 1b | `w_news` bootstrap | **Small bounded, corroboration-gated.** `w_news = 0.20` (under the 0.25 cap) **iff** the truth-gate returns `corroborated` (≥2 independent primaries); else `w_news = 0` (Hermes informational-only). Log per-signal components so the deferred per-signal calibration can grade Hermes on S6-era data (substrate cannot be backfilled). |

**The unifying insight:** **corroboration is the single key that unlocks the brain.** `≥2 independent
allowlisted primary sources` is what simultaneously (a) lets `w_news` go nonzero in fusion and (b) widens the
Anchor band (`max_shift_corroborated 2.5` vs `_uncorroborated 1.0`). An uncorroborated Hermes proposal is
informational only: the trade reduces to *market-mid nudged toward the base-rate prior*, inside a tight anchor
band. This bounds a prompt-injected catalyst by construction — it cannot earn weight or widen the band without
independent corroboration that the truth-gate verifies against the sanitized store.

---

## 1. Goal & non-goals

**Goal.** Stand up the full propose→dispose pipeline in shadow: a propose-only facade Hermes can only enqueue
through → the ERS loop independently re-derives everything (truth-gate, fusion, anchor clamp, calibration `k`,
detector veto, cluster cap, drawdown breaker) → sizes via the unchanged validator → records the decision and
the forecast → `PaperSigner`. Prove an indirect-prompt-injection proposal is blocked by the truth-gate, and
that the ERS never trusts a Hermes field upward.

**Non-goals (explicitly deferred — see §8).** A live Hermes/VPS; the MCP server transport; adaptive
per-signal calibration & weight learning; the real `MarketRegistry` metadata feed; the resolution-feedback
wiring that warms `k`; the §4.2 edge hurdle H; real cross-event latent clustering; live `/activity`+on-chain
detector inputs. None block the build; all are data-gated or need a deployed box.

---

## 2. Architecture & data flow

```
HERMES (frozen-model harness, NO KEYS, undeployed in S6)
  read tools: get_market, get_book, get_ledger, get_flags
  ONE write tool: propose_trade(...)  ── INSERT-only ──┐
                                                        ▼
                              ┌──────────────────────────────────────────────┐
                              │ ProposeOnlyFacade  (net-new, ers/facade.py)    │
                              │  exposes EXACTLY {propose_trade, get,          │
                              │  audit_log} + read tools; place/flatten/       │
                              │  record_decision/pending UNREACHABLE in code   │
                              └───────────────┬────────────────────────────────┘
                                              │ INSERT PROPOSED row
                                              ▼
                                   pending_intents (IntentStore)
                                              │ ERS polls (trusted side)
                                              ▼
   ┌─────────────────────────── process_pending  (extended) ───────────────────────────┐
   │ 0. breaker.evaluate(...) FIRST → FLATTEN/FREEZE_ADDS block_reason  (unchanged)       │
   │ per intent (FIFO), inside the per-intent try/except (queue can't wedge):             │
   │   1. book = book_for(token_id)   ── single live re-fetch, shared downstream ──       │
   │      None → REJECT no_book                                                            │
   │   2. DetectorOrchestrator verdict (defensive). AVOID → REJECT detector_avoid          │
   │   3. CitationTruthGate.verify(citations, EventStore, book)                            │
   │        refuse → REJECT {truth_gate_refuse | same_source_collusion}                    │
   │        → corroborated: bool                                                           │
   │   4. market_mid = book.midpoint(); None → REJECT book_stale                           │
   │   5. FusionEngine.fuse(mid, p_news=intent.p, p_base, p_micro, p_flow, corroborated)   │
   │        → p_final  (w_news=0.20 iff corroborated else 0; w_base small; w_micro/flow=0) │
   │   6. clamp_p(p_final, mid, question_text, seconds_to_resolution, corroborated)         │
   │        wrapped in try/except → REJECT anchor_error  (distinct, NOT internal_error)     │
   │        → p_clamped                                                                     │
   │   7. ForecastLedger.record_forecast(id, category, condition_id, p=p_clamped, mid)      │
   │        + ComponentLog.record(id, {p_news,p_base,p_micro,p_flow,corroborated})          │
   │   8. k = CalibrationGate.k_for(category)         (Decimal {0,1}; ==0 paper-only MVP)   │
   │   9. trade_intent = _to_trade_intent(intent, matrix_cold, p_override=p_clamped)        │
   │  10. cluster = _cluster_view(cluster_model, intent, portfolio)                         │
   │  11. evaluate_intent(trade_intent, book, portfolio, caps, calib_score=k, cluster) ◀ UNCHANGED │
   │  record_decision; on ACCEPT → signer.place(intent, decision) THEN _fold(...)           │
   └──────────────────────────────────────────────────────────────────────────────────────┘
                                              ▼
                                   PaperSigner (shadow; the only signer)
```

**Single book re-fetch.** The live book is fetched once per intent (step 1) and shared by the truth-gate
(thin-book check), fusion (`market_mid`), the anchor clamp (`market_mid`), and `evaluate_intent` (re-price off
`best_ask`). This honors "always re-fetch the live book before sizing" with one consistent snapshot per intent.

**Forecast is recorded for every genuine estimate, independent of the trade outcome.** Steps 7 happens after
a clean clamp and *before* `evaluate_intent`, so a forecast is logged even when the trade later SKIPs
(`k=0` → `frac_eff=0` → below floor) or is capped/rejected for portfolio reasons. Calibration grades the
*estimate*, not whether we could afford to act on it. Rejections at steps 2/3/6 (bad evidence / injection /
non-finite) do **not** record a forecast — those are not genuine estimates.

---

## 3. The integration contract (`ers/service.py`)

**Confirmed current signature (do not break existing call sites / the 377 tests):**
```python
def process_pending(store, *, book_for, portfolio, caps, signer,
                    calib_score=Decimal(1), cluster_model=None, breaker=None):  # -> Portfolio
```

**S6 extension — one optional context object, defaulting `None` (additive seam, mirrors the
`cluster_model`/`breaker` pattern).** When `pipeline is None`, behavior is exactly slice-3 (existing tests
stay green). When provided, steps 2–8 above engage.

```python
@dataclass(frozen=True)
class HermesPipeline:
    calib_gate: CalibrationGate
    fusion: FusionEngine
    truth_gate: CitationTruthGate
    detectors: DetectorOrchestrator
    forecast_ledger: ForecastLedger
    component_log: ComponentLog
    market_meta: MarketMeta          # STUB at MVP (single bucket; question_text from proposal; secs seam)

def process_pending(store, *, book_for, portfolio, caps, signer,
                    calib_score=Decimal(1), cluster_model=None, breaker=None,
                    pipeline=None):  # -> Portfolio
```
`calib_score` (the batch scalar) is **retained with its default for back-compat** — existing call sites and
the 377 tests keep passing it and exercise the slice-3 path. When `pipeline` is supplied it is **ignored** in
favor of the per-intent `k = calib_gate.k_for(category)` computed inside the loop. The param is never removed
(removing it would break those call sites); it is simply superseded when the pipeline is active.

**Invariants S6 MUST preserve (verified in `service.py`):**
1. `breaker.evaluate(...)` runs **first**, before the intent loop; FLATTEN → `signer.flatten` + `l7_flatten`,
   FREEZE_ADDS → `l7_freeze`.
2. The per-intent `except Exception → REJECT("internal_error")` stays — but the `clamp_p` raise is now caught
   **explicitly first** and mapped to `anchor_error` (a *distinct* reason; obligation #3 is about the reason
   code, not adding the try/except).
3. `record_decision` after every Decision.
4. On ACCEPT: `signer.place(intent, decision)` **then** `_fold(...)` — fold-before-next-intent serialization
   (or two intents sum past `total_open`). Any loop restructuring keeps this ordering.
5. `evaluate_intent`, `validator.py`'s dataclasses, `intent_store.propose_trade`'s INSERT-only shape, and the
   caps envelope are **untouched**.

**`_to_trade_intent` change.** Add a keyword `p_override=None`; when set, substitute it for `intent.p` (the
anchored posterior replaces Hermes's raw `p` before the validator sizes). Risk keys remain ERS-derived.

**`_cluster_view` change (pluggable hook, Fork 8C).** Extract the `cluster_id` derivation
(`intent.event_id` today) into a one-line injectable `cluster_id_of(intent)` defaulting to `intent.event_id`,
so the real latent-cluster slice swaps the function without touching the loop.

---

## 4. Net-new pure units

Each is an isolated, independently-testable unit (the team pattern). Money math = `Decimal`; the only `float`
is inside the log-odds fold and anchor (already the case in `anchor.py`), re-quantized at the boundary.

### 4.1 `FusionEngine` — `src/polybot/fusion/engine.py`
The §4.1 weighted-log-odds fold with market mid as prior.
```python
@dataclass(frozen=True)
class FusionConfig:          # consistency-checked at construction; fails loud
    w_news: float            # 0.20 bootstrap (applied iff corroborated)
    w_base: float            # ~0.30 bootstrap
    w_micro: float           # 0.0 v1 (computed + logged, not weighted)
    w_flow: float            # 0.0 v1
    clip_logodds: float      # per-signal |Δ logit| clamp, e.g. 2.0
    # HARD invariant asserted: w_news <= 0.25 (the spec cap)

@dataclass(frozen=True)
class FusionResult:
    p_final: Decimal
    components: Mapping[str, Decimal]    # {p_news,p_base,p_micro,p_flow} for the ComponentLog
    w_news_effective: float

def fuse(mid: Decimal, *, p_news, p_base, p_micro, p_flow, corroborated: bool,
         config: FusionConfig) -> FusionResult
```
- `L = logit(mid) + Σ w_i · clip(logit(p_i) − logit(mid), ±clip_logodds)`; `p_final = recalibrate(sigmoid(L))`.
- `w_news_effective = w_news if corroborated else 0.0` (the corroboration key).
- `recalibrate(x) -> Decimal` is a **typed identity stub** behind a clean seam (the deferred adaptive slice
  replaces it). Documented as identity.
- **Fail-closed:** `mid`/any `p_i` not strictly in (0,1) → use mid for that signal's delta = 0 (no nudge), or
  raise `FusionError` if `mid` itself is degenerate (caller already guards `midpoint() is None` upstream).
- **Tests (RED→GREEN):** corroborated vs not flips `w_news_effective`; `w_news>0.25` config → construction
  raises; a huge `p_news` is clipped (bounded delta); `p_base` pulls toward prior; all-mid inputs → `p_final≈mid`;
  components returned for logging; identity `recalibrate` is a no-op.

### 4.2 `CitationTruthGate` — `src/polybot/truthgate/gate.py`
ERS-side verification of a proposal's citations. **Pure** over `(citations, EventStore envelopes, live LocalBook)`.
```python
@dataclass(frozen=True)
class TruthVerdict:
    refused: bool
    reason: str | None        # 'truth_gate_refuse' | 'same_source_collusion' | None
    corroborated: bool        # >=2 INDEPENDENT allowlisted PRIMARY sources
    primary_groups: tuple[str, ...]

def verify(citations, *, event_store, book, allowlist, now_ns,
           config: TruthGateConfig) -> TruthVerdict
```
- Resolve each citation to an `Envelope`; keep only `tier==PRIMARY` **and** allowlisted; collapse by
  `publisher_group`; `corroborated = (#distinct groups >= 2)`. `DISCOVERY` tier never counts and never triggers.
- **Same-source/injection refusal:** if the proposal's evidence (the `p`-moving citations) AND a thin-book mid
  move (`book.midpoint()` shifted on thin `top_of_book` depth) both trace to **one fresh** source/timestamp →
  `refused=True, reason='same_source_collusion'`. (The "injection + pre-position" signature, master §5 / case
  catalog Security.)
- If zero allowlisted primaries → `refused=True, reason='truth_gate_refuse'` (news-only with no corroboration
  is L4 refuse-and-alert; uncorroborated-but-present is **not** refused — it just yields `corroborated=False`
  → `w_news=0`, informational-only).
- **Untrusted-data discipline:** citation strings/URLs are matched, never fetched or executed here; the gate
  reads only the already-sanitized `EventStore`.
- **Tests:** 2 distinct groups → corroborated; 2 feeds same `publisher_group` (fed-press+fed-monetary) → NOT
  corroborated (the confirmed-defect regression test); discovery-tier ignored; same fresh source + thin-book
  move → refused; non-allowlisted citation dropped; empty citations → corroborated False, not refused.

### 4.3 `ProposeOnlyFacade` — `src/polybot/ers/facade.py`
The load-bearing safety boundary. **Composes** `IntentStore` (does not subclass — no inherited `record_decision`/`pending`).
```python
class ProposeOnlyFacade:
    def __init__(self, store: IntentStore, *, market_reader, book_reader, ledger_reader, flags_reader): ...
    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()) -> bool   # delegates INSERT-only
    def get(self, intent_id): ...          # read own proposal
    def audit_log(self): ...               # read-only audit
    def get_market(self, ...): ...         # read tool
    def get_book(self, ...): ...           # read tool (respects EventStore/QueuedEventWriter read-safety)
    def get_ledger(self, ...): ...         # read tool (resolved-market history / outcomes)
    def get_flags(self, ...): ...          # read tool (detector flags, AVOID/FLAG)
```
- **Structural guarantee test (the heart of S6's safety claim):** assert the facade instance exposes EXACTLY
  the allowed names and has **no** `place`, `flatten`, `record_decision`, `pending`, or `_store` attribute
  reachable for mutation — a `dir()`/attribute sweep + a "cannot reach the signer" test. "Hermes can at worst
  enqueue" becomes load-bearing **in code**, surviving careless future wiring.
- Read tools respect the EventStore-vs-`QueuedEventWriter` constraint (read-after-close or a separate
  read-only connection; never read the live store from another thread while the writer thread is up).

### 4.4 `DetectorOrchestrator` — `src/polybot/detectors/orchestrator.py`
Composes the S7 pure detectors into one defensive verdict the loop consumes.
```python
@dataclass(frozen=True)
class DetectorVerdict:
    action: str          # 'AVOID' | 'FLAG_ONLY'
    pull_quotes: bool
    p_flow: Decimal      # smart-money confirmation signal for fusion (0 weight in v1, logged)
    reasons: tuple[str, ...]

def evaluate(intent, *, inputs, config: DetectorConfig) -> DetectorVerdict
```
- `toxicity → d2..d6 → composite() → policy.decide()`. **Catch `toxicity()`'s `ValueError`-on-negative-size**
  in the per-intent guard. `FOLLOW_ENABLED` stays `False` (dead branch).
- At S6, fed **placeholder/zero** sub-scores — live `/activity`+on-chain input parsing is POL-9-deferred. The
  orchestrator + the AVOID→REJECT wiring are real and tested; the inputs are zeros until POL-9.
- `d3_abnormal_move(catalyst_present=)` / `d5_lead_time(public_ts=)` get the Hermes catalyst flag **behind a
  seam** (deferred plumbing; the flag must itself trace to allowlisted primaries via the truth-gate).

### 4.5 `publisher_group` extension — `src/polybot/ingestion/news.py` + `allowlist.py`
Add `publisher_group: str` to `Source` (default = registrable domain derived from `url`). Independence in the
truth-gate = distinct `publisher_group`. Operator-reviewed group assignments alongside the allowlist (same
tamper-reviewed trust surface). **Regression test:** `fed-press` and `fed-monetary` share a group → not independent.

### 4.6 `ComponentLog` — `src/polybot/fusion/component_log.py` (sidecar; does NOT touch `ForecastLedger`)
Append-only per-signal breakdown keyed by `forecast_id` (= `intent_id`): `{p_news, p_base, p_micro, p_flow,
w_news_effective, corroborated, mid}`. Preserves the un-backfillable substrate the deferred per-signal
calibration needs, **without modifying POL-7's tested `ForecastLedger`** (isolation). Shares the one
`MonotonicStamper`. Self-snapshot from day one.

---

## 5. Construction & injection (already-tested units S6 actually builds)

- `CalibrationGate(ForecastLedger, PriorEngine, CalibrationConfig)` — `k_for(category)` feeds `calib_score`;
  `clamp_p(...)` per intent in a try/except → `anchor_error`. `PriorEngine` priors require the **operator-review
  gate** before they inform non-paper sizing (prior.py docstring).
- `ClusterModel(build_bar_series(EventStore, bar_ns=...))` injected as `cluster_model` — stays cold in prod
  until bars accrue (placeholder `cluster_id=event_id`, fails safe).
- `DrawdownBreaker(caps, clock=<seconds>)` injected as `breaker` — runs first.
- **One shared `MonotonicStamper`** across `IntentStore`, `ForecastLedger`, `ComponentLog`, `EventStore`
  writer, `MarketStream` (the global total-order contract, `core/clock.py`).
- `MarketMeta` **stub**: `category="unknown"` (→ `k=0`), `question_text=intent.resolution_summary`,
  `seconds_to_resolution=` a sentinel past `prior_decay_window` (so the prior anchor stays active). Documented
  as the `MarketRegistry` seam.

---

## 6. Safety invariants & new reason codes

- Hermes never computes size, never holds a key, never reaches the signer or `record_decision` (facade
  structural test). Its `p` is one capped, corroboration-gated signal; the ERS independently re-derives price,
  size, caps, and clamps the posterior.
- Fail-closed everywhere: degenerate price/mid, non-finite anchor, negative detector size, stale book, no
  corroboration → REJECT/SKIP + audit, never a silent pass.
- **New `Decision.reason` codes** (free-form string field, no validator change): `detector_avoid`,
  `truth_gate_refuse`, `same_source_collusion`, `anchor_error`. Existing codes unchanged.
- `record_forecast` / `ComponentLog` reject non-finite / out-of-range (substrate integrity); recorded p is the
  in-range `p_clamped`, so ordering (clamp → record) is safe.

---

## 7. Obligations → units (the 15 deferred-to-S6 items, all covered)

| Obligation (abridged) | Unit |
|---|---|
| 1 Propose-only facade load-bearing in code | §4.3 `ProposeOnlyFacade` |
| 2 Per-intent `k = k_for(category)` | §3 loop step 8 + `HermesPipeline` |
| 3 `clamp_p` try/except w/ distinct reason | §3 invariant 2 → `anchor_error` |
| 4 Substitute fused+clamped `p` into TradeIntent | §3 `_to_trade_intent(p_override=)` |
| 5 `record_forecast` on real proposals | §3 step 7 + §4.6 |
| 6 Supply `corroborated` from the truth-gate | §4.2 → §3 step 5/6 |
| 7 Construct+inject `ClusterModel` + `DrawdownBreaker` | §5 |
| 8 Detectors as defensive pre-gate (FOLLOW off) | §4.4 → §3 step 2 |
| 9 Hermes catalyst → d3/d5 (behind a seam) | §4.4 (deferred plumbing) |
| 10 §4.1 weighted-log-odds fusion | §4.1 |
| 11 Citation truth-gate (allowlist + N-of-M + book cross-check + same-source refuse) | §4.2 |
| 12 Author `~/.hermes/config.yaml` | §8 artifact (in-repo, reviewed) |
| 13 Run end-to-end on PaperSigner + injection probe | §9 acceptance |
| 14 One shared `MonotonicStamper` + EventStore read-safety | §5 + §4.3 |
| 15 Operator-review gate on priors | §5 (flagged, enforced before non-paper sizing) |

---

## 8. Deferred (with why each is safe to defer)

| Deferred | Why safe / when |
|---|---|
| Adaptive fusion (EMA `w_i∝1/Brier`, auto-zero, isotonic recal) | Needs a warm `ForecastLedger`; cold at MVP. Fixed weights + identity `recalibrate` stub behind a seam. ComponentLog preserves the substrate so it can be built later on S6-era data. |
| Live MCP server transport + real-Hermes injection probe | No VPS/Hermes deployed. The facade's safety is a Python type guarantee, fully tested now. `config.yaml` shipped as a reviewed artifact. |
| `MarketRegistry` (condition_id → category/question/seconds) | Paper-only `k=0` makes it moot now; stubbed. Built with the calibration-warming slice. |
| Resolution feedback (`record_resolution`) | Needs a resolution watcher; without it `k` stays 0 (paper-only — the intended MVP state). |
| §4.2 edge hurdle H | Out of POL-8 scope; needs the MarketRegistry (category→fee). |
| Real cross-event latent clusters | Research-grade; placeholder fails safe. Pluggable hook left in place. |
| Live D1–D6 inputs (`/activity` + on-chain) | POL-9-deferred; orchestrator fed zeros, wiring is real+tested. |

The `~/.hermes/config.yaml` artifact (committed under `deploy/` or `config/`) declares `tools.include` =
exactly the 4 read tools + `propose_trade`; documents Hermes-as-own-Linux-user, no shell into the ERS, no
keys, and that Hermes may rewrite only its own `SKILL.md` (trust/trade rules + secrets forbidden in
model-mutable text). It is a reviewed, version-controlled artifact, not a running server in S6.

---

## 9. Acceptance criteria

1. `./.venv/bin/pytest` green; **377 existing tests still pass** (additive seam, `pipeline=None` = slice-3).
2. New unit tests (TDD, RED→GREEN) for each §4 unit, incl. the facade structural sweep, the fed-press
   independence regression, the corroboration→`w_news` flip, the `w_news>0.25` construction guard, the
   same-source injection refusal, and the `anchor_error` distinct-reason path.
3. An **end-to-end shadow test**: synthetic Hermes proposals through `ProposeOnlyFacade.propose_trade` →
   `process_pending(pipeline=…)` on `PaperSigner`, asserting: (a) a clean corroborated proposal flows through
   fusion→clamp→record_forecast→validator and SKIPs on `k=0` (paper-only) with the forecast + components
   logged; (b) an **indirect-prompt-injection proposal** (single fresh source moving `p` + a thin-book mid) is
   **REJECTed `same_source_collusion`** and never reaches the signer; (c) an uncorroborated proposal trades
   mid+prior-only (`w_news=0`); (d) a detector-AVOID proposal is REJECTed before sizing.
4. Two independent Opus `superpowers:code-reviewer` passes (the team standard); re-review after any
   safety-critical fix.

---

## 10. Open risks / for the Opus review to probe

- **Forecast-recording predicate:** is recording-before-`evaluate_intent` (so SKIPs still log) the right call,
  or does it over-count forecasts the bot would never act on? (Chosen: log genuine estimates; calibration
  grades estimates, not execution.)
- **`w_news=0.20` bootstrap** is a judgment default, corroboration-gated + anchor-clamped; superseded by the
  adaptive slice. Probe whether 0.20 + the clip bound can still over-move a thin market within the anchor band.
- **`market_mid` from `midpoint()`** is the fusion prior AND the anchor reference; a thin/half-empty book makes
  it noisy. Guarded by `midpoint() is None → REJECT book_stale`, but probe near-degenerate mids.
- **Same-source refusal** depends on detecting "the same fresh source/timestamp" — define the freshness window
  and the thin-book threshold precisely in the plan; probe false-negatives.
- **Stub `category="unknown"`** routes everything to one calibration bucket → `k=0`. Confirm nothing can make
  `k>0` accidentally before the real resolver + resolution feedback exist (must stay paper-only).
