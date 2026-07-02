# DESIGN — S4.7 / POL-6: Realized-loss breakers + new-positions rate + auto ramp-DOWN (the tighten-only caps ratchet)

**Date:** 2026-07-02 · **Ticket:** [POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4 sub-slice 7 of 7 in build order; S4.6 remains) ·
**Status:** DESIGN (forks operator-resolved 2026-07-02; awaiting operator spec review → writing-plans).
**Depends on:** S4.1 (`SafetyController`/`ERSController`/op-audit), S4.2 (the dormant
`would_cross_daily_pending_ceiling` predicate + the caps fields), S4.5 (the durable `fills` ledger +
`fill_sink` seam), S4.4 (the run_cycle consult pattern + sticky-halt doctrine).
**Runs SHADOW-ONLY on the `PaperSigner`.** Contract-level parent: `DESIGN-S4-SAFETY.md` §3 S4.7 + §0 Fork 5.

> DECISIONS-S0 §4 rows 69–74 (rate cap, daily pending ceiling, weekly-loss halt, consecutive-loss pause,
> frozen exclusion) + §8 (ramp-DOWN automatic, ramp-UP human-gated) are the requirements this doc turns
> into isolated units. All five relevant caps fields already exist (`daily_pending_ceiling=24`,
> `weekly_loss_halt=36`, `consecutive_loss=3`, `new_positions_per_hour=2`, `new_positions_per_day=6`) —
> **S4.7 adds NO new caps fields**; it adds the durable counters, the enforcement, and the ratchet.

---

## 0. TL;DR + resolved forks

S4.7 is two things. (1) **The flow/loss breakers**: a durable dual-stamped `flow_journal` (monotonic `at`
for ordering + injected wall-clock `wall_at` for windowing — stored monotonic stamps are NOT comparable
across restarts, so restart-surviving hour/day/week windows require the wall column) feeding a per-cycle
**flow gate** (rate caps + daily pending ceiling; blocks WITHOUT touching op-state, so it auto-slides with
the window — no new auto-resume path) and the **realized-loss breakers** (weekly halt, consecutive-loss
pause — pure units that are DATA-GATED today: fills are BUY-only, zero realized outcomes exist in shadow).
(2) **The ramp-DOWN ratchet**: `swap_caps` on the `SafetyController` — a NEW re-verified frozen `RiskCaps`,
guarded tighten-only across ALL 38 fields, audited by content-hash, with two operator-signed
trigger-specific steps. Includes the **required re-plumb**: `ERSController.run_cycle` must read
`controller.active_caps()` (today it passes its own construction-captured `self._caps`, so a swap landing
in the SafetyController would bite nothing).

**Resolved forks (operator-confirmed 2026-07-02; "trigger-specific steps" confirmed 2026-06-30):**

| # | Fork | Decision |
|---|---|---|
| 1 | Ramp step sizes | **Daily-halt step:** `per_trade` 12→**9**, `total_open_risk` 60→**45** (⇒ `reserve_floor` 255, `gtd_bracket_aggregate` 45). **Weekly-halt step (deeper):** `per_trade` 12→**6**, `total_open_risk` 60→**30** (⇒ reserve 270, gtd 30). Steps compose tighten-only via per-field `min()`; both provably constructible against every `_verify` invariant; neither touches a construction-captured field (L7/anomaly thresholds untouched). |
| 2 | Window semantics | **Rolling windows** — 3600s / 86400s / 604800s sliding over the injected wall clock. Restart-safe, timezone-free, strictly conservative (no midnight-reset exploit). |
| 3 | Weekly-halt de-risk | **Yes**: halt-FIRST then ONE best-effort `cancel_all` (the S4.4 pattern verbatim — GTD exits survive; a raising signer is audited `FAILED:` and never unwinds the halt). Resting maker entries must not keep adding exposure after a loss halt. |
| 4 | Consecutive-loss recovery | **Sticky until operator RESUME** (the streak COUNTER resets on a win; the PAUSED op-state does not). Preserves the only-boot-reconcile-auto-resumes invariant. |

**Baked (safety/DECISIONS-forced):** daily ceiling = a per-cycle gate consulted inside
`SafetyController.verdict`'s RUNNING branch (DECISIONS row 70: "evaluated each cycle, not booked-only") —
enforced conservatively with `new_worst_case = caps.per_trade` through the existing dormant predicate (no
intent can cross; some smaller intents block early — the fail-closed direction; per-intent exactness would
require touching the sacred loop); weekly halt → sticky `HALTED` (+ human review = S4.6 alert later);
consecutive-loss (streak ≥3 **OR** pending > $24, whichever first, row 72) → sticky `PAUSED`; frozen
positions excluded from realized/consecutive counters but still count vs total-open (row 74 — the
exclusion is a `frozen_tokens` filter fed from the live Portfolio); ramp swaps are idempotent (a no-op
swap — identical hash — writes NO audit row) and evaluated regardless of op-state (tighten-only makes
re-application harmless), while set_state/cancel_all stay edge-triggered from RUNNING/PAUSED exactly as
S4.4; calibration-regression and maker-net-regression ramp triggers (DECISIONS §8) are DOCUMENTED SEAMS —
no calibration/maker source exists yet, they will call the same `swap_caps` with their own steps.

---

## 1. Goal & non-goals

**Goal:** (a) the durable `flow_journal` + its recorder (composed onto the existing `fill_sink` seam — no
`service.py` change); (b) the pure window helpers + the flow gate (rate caps + daily ceiling) consulted
per-cycle in `verdict`'s RUNNING branch via the one-shot `wire_flow_gate` binder; (c) the realized-loss breakers
(weekly HALT + consecutive/pending PAUSE) consulted in `run_cycle` after the L5 anomaly block; (d) the
tighten-only ratchet: direction map over all 38 fields, `assert_tighten_only`, the two step factories,
`SafetyController.swap_caps` (audited, no-op-safe), and the `active_caps()` re-plumb so swaps actually
bite the validator + GTD next cycle.

**Non-goals (deferred; §7):** recording realized outcomes (POL-4/S9 write `kind="realized"` rows when
exits/resolutions exist — today the loss breakers are dormant over an empty set); the
calibration/maker-regression triggers (seams); ramp-UP (human-gated, DECISIONS §8 — no code path);
rebuilding construction-captured consumers (breaker/anomaly sentinels) on a swap — v1 steps never touch
their fields, and the guard still validates those fields as unchanged; the S4.6 L8 `LOWER_CAPS` command
(it will drive this same `swap_caps`); per-cycle `flow_log()` scan narrowing (shadow-scale is fine).

---

## 2. Architecture

```
ERSController.run_cycle (extended additively; every new seam None-defaults == today)
  1. heartbeat.beat()                          (unchanged)
  2. L5 anomaly consult                        (unchanged, S4.4)
  3. if lossbreakers is not None:              (NET-NEW, S4.7)
       ls = lossbreakers.evaluate(frozen_tokens={p.token_id for p in portfolio if p.frozen})
       apply ramp swaps for any (trigger, step) in ls.ramp_steps    # idempotent, any op-state
       if ls.action == HALT  and controller.state() in (RUNNING, PAUSED):
           controller.set_state(HALTED, reason=ls.triggers[0]); one best-effort cancel_all (S4.4 pattern)
       elif ls.action == PAUSE and controller.state() == RUNNING:
           controller.set_state(PAUSED, reason=ls.triggers[0])     # sticky; no de-risk
  4. caps = controller.active_caps()           (NET-NEW re-plumb — THE swap point)
     process_pending(..., caps=caps, ...)      (call site otherwise unchanged)

SafetyController.verdict RUNNING branch (extended additively):
     if self._flow_gate is not None:
         reason = self._flow_gate()            # None | a REASON_* string; raising => "flow_gate_error"
         if reason: return OpVerdict(RUNNING, reason, None, (reason,))   # blocks; op-state UNCHANGED
     return OpVerdict(RUNNING, None, None, ())                            # today's path
```

- **Durability:** `flow_journal` rows carry BOTH stamps. `at` (MonotonicStamper) preserves total ordering
  with every other table; `wall_at` (injected 0-arg `wall_clock` → float epoch seconds, `time.time` in
  prod) is what windows are computed over — the ONLY cross-restart-comparable time in the store, tolerable
  because hour/day/week windows don't care about NTP-level skew (and L5 halts on >2s skew anyway).
- **The gate blocks, states stick:** the flow gate returns a block_reason from a RUNNING controller
  without any `set_state` — when the window slides the block evaporates. Op-state transitions (weekly
  HALT, consecutive PAUSE) are sticky and edge-triggered, exactly the S4.4 doctrine. The invariant "the
  only automatic HALTED→RUNNING is the clean boot-reconcile" is untouched.
- **The ratchet actually bites:** `run_cycle` reads `controller.active_caps()` per cycle; the validator
  and `derive_bracket` receive caps per-call, so a swap lands next cycle. Construction-captured consumers
  (DrawdownBreaker, AnomalyMonitor + sentinels, initial `Portfolio.nav`) are STALE-COPY BOUNDARIES: the
  v1 steps touch only `per_trade`/`total_open_risk`/`reserve_floor`/`gtd_bracket_aggregate` — all
  per-call-consumed — and the tighten-only guard requires every other field EQUAL. A future step that
  tightens a captured field must also rebuild its consumer (documented, not built).
- **Data-gating:** counting ACCEPT flow works TODAY (the recorder composes onto `fill_sink`); realized
  rows don't exist until POL-4/S9 → the weekly/consecutive breakers evaluate an empty set → `NONE`
  forever in shadow (the cold-comove/calibration pattern). Injected rows in tests prove the machinery.

## 3. Enforcement points

| # | Control (DECISIONS-S0 §4) | Where | Fires | Recovery |
|---|---|---|---|---|
| 1 | Rate cap ≤2/hr ≤6/day (row 69) | flow gate (verdict, RUNNING branch) | ACCEPT count in rolling 3600s ≥ `new_positions_per_hour` → `rate_cap_hourly`; in 86400s ≥ `new_positions_per_day` → `rate_cap_daily` (hourly checked first) | auto — the window slides |
| 2 | Daily pending ceiling $24 (row 70) | flow gate | `would_cross_daily_pending_ceiling(pending_today, new_worst_case=caps.per_trade, caps)` where `pending_today` = Σ accept `worst_case` + Σ \|realized losses\| in rolling 86400s → `daily_ceiling`; ALSO trips ramp step A | auto — the window slides (the step-A tightening persists) |
| 3 | Weekly-loss halt $36 (row 71) | loss breakers (run_cycle) | Σ \|realized losses\| (frozen-excluded) in rolling 604800s > `weekly_loss_halt` → sticky `HALTED(weekly_loss_halt)` + ONE best-effort `cancel_all` + ramp step B | operator RESUME / restart+clean-reconcile |
| 4 | Consecutive-loss pause (row 72) | loss breakers | streak of realized losses (frozen-excluded, most-recent-first) ≥ `consecutive_loss` → sticky `PAUSED(consecutive_loss)`; OR `pending_today` > `daily_pending_ceiling` → sticky `PAUSED(daily_pending_pause)` — whichever first | operator RESUME (the streak counter itself resets on a win; the op-state does not) |
| 5 | Ramp-DOWN (§8) | `swap_caps` | step A on a daily-ceiling breach; step B on a weekly halt; calibration/maker-regression = future seams calling the same swap | ramp-UP is human-gated — out of scope |

Severity/precedence: HALT > PAUSE within the loss breakers (`triggers` most-severe-first, `triggers[0]`
is the reason); the loss consult never downgrades (edge guards: HALT from RUNNING/PAUSED, PAUSE from
RUNNING only); an L5 anomaly halt earlier in the same cycle wins (the loss consult sees HALTED and only
its idempotent swaps still apply).

**Rows 70 vs 72 interplay (deliberate, not a contradiction):** the flow gate's conservative
per_trade-headroom block fires BEFORE any accept-flow crossing, so pure trade flow can never push
`pending_today` over the ceiling — the sticky `daily_pending_pause` (row 72) is reachable only when
REALIZED LOSSES push pending over (losses join the pending sum without passing through the gate). Gate =
the pre-crossing belt (auto-slides); pause = the post-crossing suspenders (sticky). Step A is applied via
the breaker's pending arm (`ramp_steps`), not by the gate — the gate only ever returns a block reason.

## 4. Net-new units & seam extensions (the pinned contract block)

```python
# ers/intent_store.py  (EXTENDED additively — one new append-only table, op_audit-style)
# CREATE TABLE IF NOT EXISTS flow_journal (
#     flow_id INTEGER PRIMARY KEY AUTOINCREMENT,
#     at      INTEGER NOT NULL,   -- MonotonicStamper.stamp() (ordering; NOT cross-restart comparable)
#     wall_at REAL    NOT NULL,   -- caller-supplied wall clock, epoch seconds (windowing; cross-restart)
#     kind    TEXT    NOT NULL,   -- "accept" | "realized"
#     token_id TEXT   NOT NULL,
#     amount  TEXT    NOT NULL )  -- Decimal str: accept => worst_case_risk (+); realized => signed PnL (+win/-loss)
def record_flow_event(self, *, kind, token_id, amount, wall_at): ...   # at = stamper.stamp(); commit per write
def flow_log(self): ...   # ORDER BY flow_id -> [{"at","wall_at","kind","token_id","amount": Decimal}]

# ers/flow.py  (NET-NEW module — the journal recorder, window helpers, and the flow gate)
def make_flow_recorder(store, *, wall_clock): ...
#   returns a fill_sink-shaped callable (intent, decision, position) recording kind="accept",
#   amount=position.worst_case_risk, wall_at=wall_clock().  NO service.py change:
def compose_sinks(*sinks): ...            # one fill_sink fanning out to many (fills + flow)
def accepts_in_window(rows, *, wall_now, window_seconds) -> int: ...        # kind=="accept" count
def pending_in_window(rows, *, wall_now, window_seconds=86400) -> Decimal: ...
#   Σ accept.amount + Σ abs(realized.amount) where amount < 0, wall_at in (wall_now-window, wall_now];
#   wins NEVER offset (conservative). A malformed row in OUR OWN journal is corruption, never skipped:
#   the helpers RAISE, and each consumer converts the raise into its fail-closed action (the gate BLOCKS
#   with REASON_FLOW_GATE_ERROR; the breakers HALT with REASON_FLOW_DATA_ERROR).
def make_flow_gate(store, caps_provider, *, wall_clock): ...
#   returns a 0-arg callable -> str|None consulted by SafetyController.verdict (RUNNING branch only):
#   hourly rate -> REASON_RATE_HOURLY; daily rate -> REASON_RATE_DAILY; then
#   would_cross_daily_pending_ceiling(pending_today=pending_in_window(...), new_worst_case=caps.per_trade,
#   caps=caps_provider()) -> REASON_DAILY_CEILING; else None.
#   caps_provider = 0-arg -> RiskCaps (assembly binds controller.active_caps — the gate follows the ratchet).

# ers/lossbreaker.py  (NET-NEW module — the realized-loss breakers; AnomalyMonitor-shaped)
NONE = "NONE"; PAUSE = "PAUSE"; HALT = "HALT"
@dataclass(frozen=True)
class LossState:
    action: str            # NONE | PAUSE | HALT  (HALT beats PAUSE)
    triggers: tuple        # reason strings, most-severe-first; () when NONE
    ramp_steps: tuple      # ("daily",) / ("weekly",) / both — consumed by run_cycle for swap_caps
    # __post_init__ pins HALT/PAUSE => non-empty triggers (unrepresentable otherwise — the S4.4
    # AnomalyState lesson: the consumer indexes triggers[0] in the halt path).
class LossBreakers:
    def __init__(self, *, store, caps_provider, wall_clock): ...
    def evaluate(self, *, frozen_tokens=frozenset()) -> LossState: ...
#   weekly: Σ|realized losses| (token not in frozen_tokens) in rolling 604800s > caps.weekly_loss_halt
#     -> HALT, trigger REASON_WEEKLY_LOSS, ramp step "weekly"
#   consecutive: trailing streak of realized losses (frozen-excluded) >= caps.consecutive_loss
#     -> PAUSE, REASON_CONSECUTIVE_LOSS
#   pending arm: pending_in_window(24h) > caps.daily_pending_ceiling -> PAUSE, REASON_DAILY_PENDING_PAUSE
#     AND ramp step "daily" (a daily breach IS the daily-loss halt of DECISIONS §8)
#   store/flow_log() raising or malformed -> fail closed: HALT with REASON_FLOW_DATA_ERROR (never silent)

# ers/ramp.py  (NET-NEW module — the tighten-only ratchet)
TIGHTEN_DIRECTION: dict  # EVERY RiskCaps field -> "down" | "up" (reserve_floor) | "fixed"
#   "fixed" (change rejected in v1): nav, min_position_floor, l7_velocity_window_seconds,
#   api_storm_window_seconds (direction genuinely ambiguous for the two windows — longer accumulates more).
def assert_tighten_only(old, new): ...    # every field equal-or-tighter per the map; raises ValueError
def step_daily(caps) -> RiskCaps: ...     # replace(caps, per_trade=min(.,9), total_open_risk=min(.,45),
def step_weekly(caps) -> RiskCaps: ...    #   reserve_floor=nav-T', gtd_bracket_aggregate=T')  -- composes

# ers/safety.py  (EXTENDED additively)
class SafetyController:
    # ctor UNCHANGED. The gate needs caps_provider=controller.active_caps, so it cannot exist before the
    # controller does — the seam is a ONE-SHOT late binder instead of a ctor kwarg:
    def wire_flow_gate(self, gate): ...   # second call raises; unwired == today byte-for-byte
    def swap_caps(self, new_caps, *, reason) -> bool: ...
#   assert_tighten_only(self._caps, new_caps); identical content_hash -> return False (NO audit row);
#   else record_op_event(kind="caps_swap", reason=reason, detail=f"{old_hash[:16]}->{new_hash[:16]}")
#   THEN self._caps = new_caps (audit-before-mutate); return True.
REASON_RATE_HOURLY = "rate_cap_hourly"; REASON_RATE_DAILY = "rate_cap_daily"
REASON_DAILY_CEILING = "daily_ceiling"; REASON_DAILY_PENDING_PAUSE = "daily_pending_pause"
REASON_WEEKLY_LOSS = "weekly_loss_halt"; REASON_CONSECUTIVE_LOSS = "consecutive_loss"
REASON_RAMP_DOWN = "ramp_down"; REASON_FLOW_GATE_ERROR = "flow_gate_error"
REASON_FLOW_DATA_ERROR = "flow_data_error"
#   verdict RUNNING branch: consult flow_gate (raising gate -> block with REASON_FLOW_GATE_ERROR;
#   fail closed, op-state unchanged). All other branches untouched.

# ers/controller.py  (EXTENDED additively)
class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None, clock): ...
#   run_cycle: beat -> anomaly (unchanged) -> lossbreakers consult (§2 pseudocode: swaps first
#   [swap_caps(step_daily/step_weekly(controller.active_caps()), reason=REASON_RAMP_DOWN)], then the
#   edge-guarded set_state + weekly one-shot best-effort cancel_all reusing the S4.4 audit pattern)
#   -> process_pending(..., caps=self._controller.active_caps(), ...)   # THE re-plumb
#   lossbreakers=None AND an unwired flow_gate == today byte-for-byte.
```

**op_audit:** new kind `caps_swap` (docstring set grows). **UNCHANGED:** `evaluate_intent`/the validator,
`propose_trade`'s chokepoint, `process_pending`'s signature + decision flow (the caps ARG VALUE now comes
from `active_caps()` — same object today unless a swap happened), `MonotonicStamper`, heartbeat,
supervisor, breaker, anomaly.

## 5. New `RiskCaps` fields

**None.** All five enforcement fields exist (S4.2). The ratchet's `TIGHTEN_DIRECTION` map must cover all
38 current fields exactly (a structural test pins map-keys == dataclass-fields, so a future field addition
fails loudly until classified).

## 6. Safety invariants

1. **Tighten-only, provably:** `swap_caps` rejects any non-tightening field change (per the direction
   map; "fixed" fields must be byte-equal); `_verify` re-runs on construction; the new hash is the audit
   record. Transitively nothing can ever loosen past the signed $60 envelope.
2. **No new auto-resume:** the flow gate blocks without touching op-state; weekly/consecutive transitions
   are sticky; the only automatic `HALTED→RUNNING` remains the boot-reconcile (structural pin extended to
   `flow.py`/`lossbreaker.py`/`ramp.py`: none may contain `set_state` or `RUNNING`).
3. **Halt-first, once, best-effort** (weekly): `set_state(HALTED)` before ONE `cancel_all`; raising
   signer audited `FAILED:`; never re-fires from HALTED/FLATTENING (mutation targets mirror S4.4).
4. **Fail closed on our own data:** a raising/malformed `flow_journal` read → the gate BLOCKS
   (`flow_gate_error`) and the breakers HALT (`flow_data_error`) — corruption in the safety ledger is
   never silently ignored.
5. **Frozen exclusion (row 74):** realized/streak counters exclude `frozen_tokens`; the validator's
   total-open accounting (which includes frozen) is untouched.
6. **Conservative daily gate:** `new_worst_case = caps.per_trade` — no intent can cross the ceiling;
   blocking early is the accepted direction (documented; per-intent exactness would touch the sacred loop).
7. **Idempotent swaps:** re-applying a step is a hash-identical no-op (no audit spam); swaps apply in any
   op-state (tightening while halted is harmless and desirable).
8. **Windows are wall-clock rolling** (3600/86400/604800s) over `wall_at`; monotonic `at` is never used
   for windowing (cross-restart incomparable — the S4.5 lesson, re-pinned here).
9. **Hermes boundary:** no new facade surface (structural sweep unchanged); `swap_caps` is reachable only
   from the ERS side (and S4.6's LOWER_CAPS later) — never via `ProposeOnlyFacade`.

## 7. Built-now vs deferred

| Capability | Built now | Deferred (why safe) |
|---|---|---|
| flow_journal + recorder + compose_sinks | ✅ durable, dual-stamped | live assembly binds wall_clock=time.time (S9/POL-4; tests inject) |
| Rate caps + daily ceiling gate | ✅ full (counts ACCEPTs that flow today) | — |
| Weekly + consecutive breakers | ✅ pure units + run_cycle wiring | REALIZED rows (POL-4/S9 record kind="realized" on exits/resolutions; empty set ⇒ NONE in shadow — injected rows prove the machinery) |
| Ramp-DOWN ratchet + steps + re-plumb | ✅ full, audited, idempotent | calibration/maker-regression triggers (no sources; same swap_caps seam) · rebuilding construction-captured consumers (v1 steps never touch their fields) |
| Ramp-UP | — | human-gated (DECISIONS §8); S4.6 LOWER_CAPS drives swap_caps; ramp-UP has NO code path |
| Frozen exclusion | ✅ frozen_tokens filter from the live Portfolio | a real dispute source (the S4.4 dispute_flagger seam feeds `frozen` later) |

## 8. Acceptance criteria

1. Full suite green; the existing **660 stay green** (`lossbreakers=None` + an unwired flow gate == today;
   the re-plumbed caps arg is the same object absent a swap).
2. New TDD tests (RED→GREEN observed) incl. at minimum: journal round-trip + dual-stamp domains; window
   boundary pairs (at-window-edge in/out for 1h/24h/7d); rate-cap boundary pairs (2nd accept in the hour
   blocks the 3rd; hourly-before-daily ordering); the conservative daily gate (pending 12.01 + per_trade
   would cross ⇒ blocks, at-exactly-24 does not); gate auto-slides (wall clock advances ⇒ unblocked, state
   never left RUNNING); wins never offset pending; weekly HALT boundary pair + one-shot cancel_all +
   sticky; streak boundary (2 losses no, 3 yes; a win resets the streak; frozen-token losses excluded);
   pending-arm PAUSE; HALT-beats-PAUSE ordering; raising store ⇒ flow_gate_error block / flow_data_error
   HALT; `assert_tighten_only` accept/reject pairs per direction class (down/up/fixed) + map-covers-all-38
   structural pin; step factories produce `_verify`-constructible caps + compose (weekly∘daily == weekly);
   no-op swap writes no audit row; a real swap audits `caps_swap` with both hashes and the NEXT cycle's
   validator clamps to the tightened per_trade (the re-plumb bites); structural no-set_state pin on the
   three new modules.
3. **The e2e:** a RUNNING loop accepts intents → the 3rd accept inside the hour is REJECTed
   `rate_cap_hourly` while state stays RUNNING → wall clock advances, flow resumes → injected realized
   losses cross $36 → sticky `HALTED(weekly_loss_halt)`, exactly one cancel_all, step-B swap audited, and
   the next cycle's intent REJECTs against per_trade=6 sizing even though the loop is halted-blocked.
4. Two-stage review per sub-slice (spec-compliance + pinned-opus with mutation batteries; pycache sweep
   after each mutation revert); re-review after any safety-critical fix; final whole-slice review.
5. HANDOFF/memory/POL-6 updated; branch `pol-6-s4.7-breakers`; merge `--no-ff` with verification status;
   **confirm before push**.

## 9. Sub-slice decomposition (build order)

| # | Sub-slice | Contents |
|---|---|---|
| S4.7a | **The journal** | `flow_journal` table + `record_flow_event`/`flow_log` + `make_flow_recorder` + `compose_sinks` + `accepts_in_window`/`pending_in_window` (boundary pairs, wins-never-offset, dual-stamp domains). |
| S4.7b | **The ratchet** | `ers/ramp.py` (direction map + all-38 structural pin + `assert_tighten_only` + `step_daily`/`step_weekly`) + `SafetyController.swap_caps` (audit, no-op semantics) + the `active_caps()` re-plumb in `run_cycle` + the swap-bites-next-cycle test. |
| S4.7c | **The flow gate** | `make_flow_gate` + `SafetyController.wire_flow_gate` (one-shot; consulted in the RUNNING branch, fail-closed raise path) + the new REASON constants + auto-slide tests. |
| S4.7d | **The loss breakers + e2e** | `ers/lossbreaker.py` (`LossState`/`LossBreakers`, weekly/streak/pending arms, frozen exclusion, fail-closed data error) + the `run_cycle` consult (swaps → edge-guarded set_state → weekly one-shot cancel_all) + the §8.3 e2e. |

Each sub-slice: strict TDD (observe the RED), then the two-stage review, serial on `pol-6-s4.7-breakers`.
