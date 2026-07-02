# DESIGN — S4.4 / POL-6: L5 AnomalyMonitor (the anomaly kill-switch)

**Date:** 2026-07-02 · **Ticket:** [POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4 sub-slice 4 of 7) ·
**Status:** DESIGN (forks operator-resolved 2026-07-02; awaiting operator spec review → writing-plans).
**Depends on:** S4.1 (`SafetyController`/`ERSController`), S4.2 (`Signer`/`PaperSigner.run_canary`, caps fields),
S4.3 (heartbeat/supervisor — untouched), S4.5 (`ThreeWayReconciler`/`ReconResult`, the fills ledger).
**Runs SHADOW-ONLY on the `PaperSigner`.** Contract-level parent: `DESIGN-S4-SAFETY.md` §3 S4.4.

> Master design §5 L5: *"Immediate `cancelAll()` + halt-new on: UMA dispute on a held market; abnormal book
> (crossed/locked, midpoint jump, depth collapse); API error/5xx/auth storm; clock skew >2s; WS disconnect or
> fill-reconciliation mismatch; signing-canary failure (never blind-retry)."* This doc turns that into
> isolated, clock-injected, fail-closed units wired into `ERSController.run_cycle` AHEAD of `process_pending`.

---

## 0. TL;DR + resolved forks

S4.4 builds the **L5 anomaly kill-switch**: a `DrawdownBreaker`-shaped `AnomalyMonitor` whose six triggers are
each an isolated, injectable sentinel seam. On a NEW anomaly while the loop is live, the controller fires
`signer.cancel_all()` ONCE (working entries only — the protective GTD exits survive, exactly the S4.2
semantics) and sets op-state `HALTED` with the specific `l5_*` reason. `process_pending`, `evaluate_intent`,
the validator, and the `propose_trade` chokepoint are **byte-for-byte untouched** — the whole slice is a new
module + additive `None`-defaulting seams on `ERSController`/`MarketStream`.

**Resolved forks (operator-confirmed 2026-07-02):**

| # | Fork | Decision |
|---|---|---|
| 1 | Recovery semantics | **ALL L5 halts are STICKY.** No auto-resume; recovery = operator RESUME (S4.6 Telegram later; restart + clean boot-reconcile or console now). Preserves the load-bearing invariant that the ONLY automatic `HALTED→RUNNING` is `RestartReconciler`'s clean boot-reconcile. Transient blips are already absorbed BELOW L5 (stale books → validator `book_stale` rejects + breaker `stale_mark` freeze), so L5 fires only on sustained/integrity anomalies — which deserve a human. |
| 2 | Threshold values | **Conservative defaults accepted** as new tighten-only, content-hashed `RiskCaps` fields (§5): midpoint-jump $0.15/cycle, depth-collapse ≥80% of prev top-of-book depth (≥1000-share noise floor), WS staleness 30s, API storm ≥5 5xx/60s or ≥2 auth-fails/60s. |

**Baked (safety-forced, not asked):** every L5 trigger de-risks via `cancel_all` + `HALTED` (master design
verbatim; never FLATTEN — L5 is *cancel + halt-new*, flatten stays L6/L7/op territory); clock skew maps to
`HALTED` (halts signing — stronger than halt-new; there is no weaker state that still blocks the canary);
recon `DIVERGED`→`HALTED(l5_recon_mismatch)` while `OK`/`DORMANT`/`SETTLING`→no action (the settle window
exists precisely so in-flight fills don't false-halt); abnormal-book checks run on NON-stale books only;
canary failure halts and is NEVER retried; a sentinel that RAISES is itself an anomaly (fail closed → HALT).

---

## 1. Goal & non-goals

**Goal:** the running-cadence anomaly gate. Concretely: (a) the pure `AnomalyMonitor` + six sentinel seams,
(b) the additive `anomaly=` consult in `ERSController.run_cycle` ahead of `process_pending` with
edge-triggered `set_state` + one-shot `cancel_all` + audit, (c) the per-cycle reconcile cadence that S4.5
deliberately deferred here (consumes `ReconResult`; routes `DIVERGED` to `REASON_L5_RECON_MISMATCH`),
(d) the signing-canary scheduler on `caps.signing_canary_interval_seconds`, and (e) the new anomaly caps.

**Non-goals (deferred; §7):** real NTP/chrony (the `ClockSkewSentinel` runs over injected refs); live API/WS
health feeds (deploy-time wiring — the sentinels are TDD'd over injected state); the UMA dispute/MOOV2 watch
(no dispute-ingestion source exists — a stub seam that sets `OpenPosition.frozen=True` documents the shape);
per-market cancel scoping (live/POL-4 refinement — v1 cancels all working entries, globally conservative);
narrowing the per-cycle `event_store.all()` scan (moot until a POL-4 wallet exists — the shadow path
short-circuits to DORMANT without scanning); S4.6 RESUME and S4.7 breakers/ratchet.

---

## 2. Architecture

```
ERSController.run_cycle (extended additively; anomaly=None == today byte-for-byte)
  1. heartbeat.beat()                      (unchanged — liveness first)
  2. if anomaly is not None:
       state = anomaly.evaluate(portfolio.positions, book_for)   # ALWAYS evaluated (keeps prev-mid warm)
       if state.action == HALT and controller.state() in {RUNNING, PAUSED}:   # EDGE-triggered
           controller.set_state(HALTED, reason=state.triggers[0])  # gate closes FIRST (audited by set_state)
           try:                                                  # then de-risk BEST-EFFORT, once
               signer.cancel_all()                               # GTD exits survive (S4.2)
               store.record_op_event(kind="cancel_all", reason=state.triggers[0], detail=",".join(state.triggers))
           except Exception as exc:                              # a raising signer must NOT unwind the halt
               store.record_op_event(kind="cancel_all", reason=state.triggers[0], detail=f"FAILED: {exc}")
  3. process_pending(...)                  (unchanged call — the HALTED verdict now blocks everything)
```

- **Edge-triggered:** the monitor evaluates every cycle, but the controller ACTS only when the op-state is
  `RUNNING`/`PAUSED` — never re-fires on an existing `HALTED` (no audit spam, no repeated `cancel_all`) and
  never preempts `FLATTENING` (a stronger de-risk already in flight; it settles `HALTED` on its own).
- **Sticky (Fork 1):** nothing in S4.4 ever calls `set_state(RUNNING, ...)`. Grep-provable.
- **Purity/shape:** `AnomalyMonitor` mirrors `DrawdownBreaker` — constructed with `caps` + `clock` + the
  sentinel seams, `evaluate(positions, book_for) -> AnomalyState`, internal per-token state allowed
  (prev-mid/prev-depth, like the breaker's velocity `deque`). Every seam defaults `None` = that trigger
  dormant (the data-gated pattern) — so a bare `AnomalyMonitor(caps, clock=...)` never fires.
- **Fail closed:** a sentinel that raises inside `evaluate` → that trigger fires (`HALT`), it never masks.
  This includes the monitor-internal abnormal-book block (no seam kwarg, but wrapped as a whole at its
  `evaluate` call site): a raising `book_for`/book object IS an abnormal-book anomaly and fires
  `l5_abnormal_book` — an unwrapped block could otherwise void triggers already collected earlier in the
  same evaluate (e.g. a skew halt lost because a broken book crashed the cycle into the L6 SIGKILL path).
- **Clock domains (the S4.5 lesson — pinned):** the monitor's `clock=` is **float monotonic SECONDS**
  (`time.monotonic` in prod), used by the canary scheduler, API-storm window, and WS-staleness compare.
  The reconcile provider takes its own `clock_ns=` in the **`MonotonicStamper` monotonic-ns domain**
  (`time.monotonic_ns` in prod) because `ReconResult`'s settle window lives there. `MarketStream` frame
  stamps are stamper-domain ns; the WS sentinel converts `age_s = now_s - last_frame_at_ns / 1e9` — valid
  because `time.monotonic_ns` IS `time.monotonic` in ns (same clock; the stamper's +1ns uniqueness nudges
  are negligible at 30s tolerances). Tests inject both clocks explicitly.
- **Hermes boundary:** nothing here is reachable via `ProposeOnlyFacade` (no new facade surface; the S4.1
  structural sweep still passes untouched).

## 3. The six triggers (each its own seam)

| # | Trigger | Sentinel / seam | Fires (`HALT` + reason) when | Dormant when |
|---|---|---|---|---|
| 1 | Abnormal book | internal to the monitor, over `positions` + `book_for` | on a HELD token with a NON-stale book: (a) `midpoint() is None` while `is_stale()` is False (crossed/locked/empty side); (b) top-of-book depth (bid_size+ask_size) drops ≥ `depth_collapse_fraction` vs the monitor's prev observation AND prev depth ≥ `depth_collapse_min_prev_shares`; (c) \|mid − prev_mid\| ≥ `midpoint_jump_halt`. Prev state is NET-NEW per-token memory inside the monitor; first observation of a token never fires (b)/(c). Reason `l5_abnormal_book`. | no positions / book absent (the breaker's `stale_mark` + validator `no_book` own those) |
| 2 | WS disconnect | `ws_last_frame_at=` seam: 0-arg callable → last-frame stamper-ns or `None` | age > `ws_staleness_halt_seconds`; a WIRED callable returning `None` (never saw a frame) = +inf age = fires (mirrors heartbeat's +inf fail-closed). Reason `l5_ws_down`. | seam is `None` |
| 3 | API 5xx/auth storm | `ApiStormSentinel` (injected; `record(status, now)` + windowed counts) | ≥ `api_5xx_storm_count` statuses ≥500 in `api_storm_window_seconds`, OR ≥ `api_auth_storm_count` of {401,403} in the window. Live feed = deploy-time; tests inject statuses. Reason `l5_api_storm`. | seam is `None` |
| 4 | Clock skew | **separate `ClockSkewSentinel`** over injected `wall_clock` + `ntp_ref` (real NTP/chrony = deploy) | \|wall − ntp\| > `caps.clock_skew_tolerance_seconds` (=2, exists). Halts SIGNING via the same sticky `HALTED` (there is no weaker signing-only state; halt-everything ⊇ halt-signing). **`MonotonicStamper` untouched.** Reason `l5_clock_skew`. | seam is `None` |
| 5 | Fill-recon mismatch | `recon_provider=` seam: 0-arg callable → `ReconResult \| None` (§4 `make_recon_provider`) | `status == DIVERGED` → reason `l5_recon_mismatch` (`REASON_L5_RECON_MISMATCH`, already defined). `OK`/`DORMANT`/`SETTLING` → no action. Shadow (wallet=None) short-circuits to DORMANT without scanning. | seam is `None` |
| 6 | Signing canary | scheduler inside the monitor on `caps.signing_canary_interval_seconds` (=300, exists) + `canary=` seam: 0-arg callable → bool (controller assembly binds `signer.run_canary`) | due-time reached (first evaluate = due) and the callable returns falsy OR raises. NEVER blind-retried: one failure halts; sticky-HALTED means it cannot re-run until operator resume. Reason `l5_canary_fail`. | seam is `None` |

**UMA dispute/MOOV2 watch — DEFERRED stub:** `dispute_flagger=` seam documented on the monitor (callable
`token_id -> bool`); when wired (future slice, needs a dispute-ingestion source) a flagged HELD token sets
`OpenPosition.frozen=True` (freeze — L7/counters exclusion) rather than halting. S4.4 ships the seam
signature + a test that the default (`None`) is inert. No dispute source exists today; building the flagger
without one would be dead code past the seam.

## 4. Net-new units & seam extensions (the pinned contract block)

```python
# ers/anomaly.py  (NET-NEW module — mirrors breaker.py's single-file shape)
NONE = "NONE"; HALT = "HALT"                                   # AnomalyState.action vocab

@dataclass(frozen=True)
class AnomalyState:
    action: str            # NONE | HALT
    triggers: tuple        # the l5_* reason strings that fired, most-severe-first; () when NONE

class ClockSkewSentinel:
    def __init__(self, *, wall_clock, ntp_ref, caps): ...      # both 0-arg callables -> float unix-seconds
    def skewed(self) -> bool                                   # |wall-ntp| > caps.clock_skew_tolerance_seconds

class ApiStormSentinel:
    def __init__(self, caps): ...                              # windowed deque of (now_s, status)
    def record(self, status, *, now) -> None                   # injected by the (deploy-time) API caller
    def storming(self, now) -> bool                            # 5xx>=count OR auth>=count within window

class AnomalyMonitor:
    def __init__(self, caps, *, clock,                         # clock: 0-arg -> float monotonic SECONDS
                 ws_last_frame_at=None, api_sentinel=None, skew_sentinel=None,
                 recon_provider=None, canary=None, dispute_flagger=None): ...
    def evaluate(self, positions, book_for) -> AnomalyState    # pure-ish; per-token prev-mid/depth memory

# ers/reconcile.py  (EXTENDED additively)
def make_recon_provider(store, event_store, reconciler, *, wallet, clock_ns):  # -> 0-arg callable -> ReconResult
#   wallet None -> reconciler.reconcile({}, {}, None, wallet=None, now=clock_ns()) WITHOUT scanning the
#   event store (cheap DORMANT short-circuit); else internal_balances(store.fills_log(), in_session=True)
#   + clob/onchain legs from event_store.all().  clock_ns: 0-arg -> MonotonicStamper-domain ns.

# ers/safety.py  (EXTENDED additively — 5 new REASON_* constants; l5_recon_mismatch already exists)
REASON_L5_CLOCK_SKEW = "l5_clock_skew"; REASON_L5_ABNORMAL_BOOK = "l5_abnormal_book"
REASON_L5_API_STORM = "l5_api_storm";   REASON_L5_WS_DOWN = "l5_ws_down"
REASON_L5_CANARY_FAIL = "l5_canary_fail"

# ers/controller.py  (EXTENDED additively)
class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, clock): ...
    # run_cycle: beat -> anomaly consult (§2 pseudocode: edge-triggered cancel_all + audit + set_state)
    #            -> process_pending(...)  (call site unchanged; anomaly=None == today byte-for-byte)

# ingestion/market_stream.py + ingestion/sharding.py  (EXTENDED additively — NON-consuming health reads)
class MarketStream:
    def last_frame_at(self) -> int | None                      # stamper-ns of the last dispatched frame;
                                                               # None = no frame yet. Does NOT consume
                                                               # (consume_resync_request/_clean_progress untouched)
class ShardedMarketCollector:
    def last_frame_at(self) -> int | None                      # MIN across shards (one dead shard = stale =
                                                               # fail-closed); None if ANY shard has no frame
```

**op_audit:** the one-shot de-risk writes `kind="cancel_all"` (new kind, additive to the docstring's set)
with `reason=triggers[0]`, `detail=<all triggers>`; the state change itself is audited by `set_state` as
today. **UNCHANGED:** `process_pending`'s signature/decision flow, `evaluate_intent`/the validator,
`propose_trade`, `IntentStore` schemas, `MonotonicStamper`, heartbeat/supervisor, `SafetyController`'s API.

## 5. New `RiskCaps` fields (tighten-only, `_verify`-checked, auto-content-hashed)

| field | default | `_verify` |
|---|---|---|
| `midpoint_jump_halt` | `Decimal("0.15")` | `0 < x < 1` (a mid is a probability) |
| `depth_collapse_fraction` | `Decimal("0.8")` | `0 < x <= 1` |
| `depth_collapse_min_prev_shares` | `Decimal("1000")` | `> 0` |
| `ws_staleness_halt_seconds` | `30` | strictly-positive int |
| `api_5xx_storm_count` | `5` | strictly-positive int |
| `api_auth_storm_count` | `2` | strictly-positive int |
| `api_storm_window_seconds` | `60` | strictly-positive int |

All seven join the existing strictly-positive loops / explicit range checks in `_verify` and are covered by
`content_hash()` automatically (`asdict` serialization). Tighten-only enforcement is the S4.7 ratchet, as
with every other hashed field.

## 6. Safety invariants

1. **Sticky:** S4.4 never sets `RUNNING`. The only automatic `HALTED→RUNNING` remains
   `RestartReconciler.reconcile_on_boot` — pinned by a grep-style structural test on `ers/anomaly.py` +
   the existing restart tests.
2. **Edge-triggered, once, halt-first:** on a NEW anomaly from `RUNNING`/`PAUSED`: `set_state(HALTED)`
   FIRST, then exactly one best-effort `cancel_all` (+ one `kind="cancel_all"` audit row; a raising signer
   is audited as `FAILED: ...` and never unwinds the halt or kills the cycle — the GTD exits are the
   backstop); an already-`HALTED`/`FLATTENING` loop is never re-de-risked (mutation-test targets: drop the
   edge guard, swap the halt/cancel order, let the exception propagate).
3. **GTD exits survive:** `cancel_all` keeps `gtd_exits` (S4.2 semantics, re-asserted here e2e).
4. **Fail closed:** a raising sentinel/seam fires its trigger (never masks); a wired-but-silent WS feed
   (`None` frame stamp) is +inf age = down; unknown `ReconResult.status` values → treated as DIVERGED.
5. **Dormant-by-default:** `anomaly=None` == today byte-for-byte (the 556 stay green); a monitor with all
   seams `None` never fires (pinned).
6. **Precedence preserved:** the L5 halt manifests through the EXISTING `SafetyController.verdict` HALTED
   path — `KILL > op-FLATTEN > L7 > FREEZE` ordering and `process_pending`'s `block_reason` precedence are
   untouched (`Decision.reason` for blocked intents = the specific `l5_*` string via the controller's
   stored reason).
7. **No new Hermes surface:** facade structural sweep unchanged/green.

## 7. Built-now vs deferred

| Capability | Built now | Deferred (why safe) |
|---|---|---|
| Monitor + 6 sentinel seams + caps | ✅ full, clock-injected TDD | — |
| run_cycle consult + one-shot cancel_all + audit | ✅ | — |
| Per-cycle reconcile cadence | ✅ `make_recon_provider` + DIVERGED→HALT | live wallet feeds (POL-4; wallet=None ⇒ DORMANT short-circuit, proven unable to mask — S4.5) |
| WS health | ✅ additive non-consuming `last_frame_at` + sentinel | wiring a LIVE collector into a deployed controller (S9 assembly; seam `None` until then) |
| API storm | ✅ sentinel over injected statuses | live recorder on real API callers (deploy; no ERS-side API caller exists yet) |
| Clock skew | ✅ sentinel over injected refs | real NTP/chrony ref (deploy; injected-ref logic is the whole decision surface) |
| Signing canary | ✅ scheduler + halt-on-fail (PaperSigner: True) | a REAL canary (POL-4 Rust signer) |
| UMA dispute watch | seam signature + inert-default test | the flagger itself (no dispute-ingestion source exists; freeze-not-halt shape documented §3) |

## 8. Acceptance criteria

1. `./.venv/bin/pytest` green; the existing **556 stay green** (`anomaly=None` == today).
2. New TDD tests (RED→GREEN observed) per unit, incl. at minimum: each sentinel's fire/no-fire boundary
   pair; first-observation never fires jump/collapse; non-stale-crossed fires while stale does not;
   sticky-after-clear (anomaly clears → still HALTED); edge-triggered-once (invariant 2); all-seams-None
   inert; canary due-schedule + no-retry; DIVERGED halts while SETTLING/DORMANT/OK do not; recon provider
   shadow short-circuit; caps `_verify` rejects each out-of-range new field; reason-constant existence.
3. **The e2e:** a RUNNING controller with a wired monitor, on an injected anomaly mid-run: cancel_all fired
   once, GTD exits intact, op-audit rows exact, every subsequent intent REJECTed with the `l5_*` reason,
   and state still HALTED after the anomaly clears.
4. Two-stage review per sub-slice (spec-compliance then pinned-opus `superpowers:code-reviewer` with
   mutation-testing of the safety-critical tests); re-review after any safety fix; tree clean + no stray
   MUTATION markers before accept.
5. `docs/HANDOFF.md` + memory + POL-6 comment updated; branch `pol-6-s4.4-anomaly`; merge `--no-ff` with
   verification status; **confirm before push**.

## 9. Sub-slice decomposition (build order)

| # | Sub-slice | Contents |
|---|---|---|
| S4.4a | **The spine** | `AnomalyState` + `AnomalyMonitor` core (a fake injected sentinel drives it) + the 5 new `REASON_*` constants + `ERSController(anomaly=)` seam + edge-triggered one-shot cancel_all/audit/set_state + sticky semantics + all-seams-None inert + e2e skeleton. |
| S4.4b | **Caps + pure sentinels** | the 7 new `RiskCaps` fields (+`_verify`) + `ClockSkewSentinel` + `ApiStormSentinel` + their monitor wiring. |
| S4.4c | **Abnormal book** | crossed/locked + depth-collapse + midpoint-jump inside the monitor (per-token prev-state memory); boundary pairs on all three thresholds. |
| S4.4d | **WS health** | additive `MarketStream.last_frame_at()` + `ShardedMarketCollector.last_frame_at()` (min-across-shards) + the WS sentinel path + clock-domain conversion tests. |
| S4.4e | **Recon cadence + canary + stub** | `make_recon_provider` (+ shadow short-circuit) + DIVERGED→`l5_recon_mismatch` + canary scheduler/no-retry + the inert `dispute_flagger` seam + the full §8.3 e2e. |

Each sub-slice: strict TDD (observe the RED), then the two-stage review, serial on `pol-6-s4.4-anomaly`.
