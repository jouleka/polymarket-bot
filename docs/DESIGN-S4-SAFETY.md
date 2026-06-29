# DESIGN — S4 / POL-6: Autonomous Safety Envelope (kill path, supervisor, reconciliation, Telegram)

**Date:** 2026-06-29 · **Ticket:** [POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4) ·
**Status:** DESIGN (brainstorm complete, awaiting operator review → writing-plans).
**Depends on:** S1 (ingestion health signals + EventStore + on-chain/Data-API read seams), S3 (the
`process_pending` chokepoint loop + the L7 `DrawdownBreaker` + `RiskCaps` + `IntentStore` audit), S5/S6
(the loop the kill-path gate sits *above*). **Runs SHADOW-ONLY on the `PaperSigner`** — POL-4 (live signing)
is blocked on a funded clean-box wallet.

> Read the master design [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
> §5 (L0–L8 safety envelope) + the "Three-way continuous reconciliation" para + §6 case-catalog
> (Operational/Security/Technical), and [`DECISIONS-S0.md`](DECISIONS-S0.md) §2 (box topology) / §4 (the halt
> numbers) / §8 (ramp authority) first. This doc resolves the open forks and decomposes S4.

---

## 0. TL;DR — what S4 is, the scope of THIS effort, and the resolved forks

S4 is the **autonomous safety envelope that replaces the human confirm** — the deterministic guardrails that
let the bot run 24/7 without a confirm-loop, and the **out-of-band kill path that must work when the trading
loop is wedged.** The master design's #1 build rule: *test the kill path against a deliberately-wedged process
**before** any live capital.* Almost all of S4 is **net-new** (grep-confirmed: zero existing code for
supervisor / watchdog / dead-man / heartbeat / reconcile / telegram / hmac / nonce / operational-state /
clock-skew / `cancel_all`). What exists and is **reused**: the in-band L7 `DrawdownBreaker`, the
`process_pending` `block_reason` short-circuit, the `PaperSigner` seam, `RiskCaps`, the `IntentStore` audit,
and the three reconciliation *data* legs (Data-API, Polygon on-chain, internal ledger).

**This effort builds the KILL PATH (S4.1–S4.3)** — the headline acceptance gate. **S4.4–S4.7 are specified
here at contract level** and built in follow-on sub-slices. The whole envelope is decomposed into 7 sub-slices
(§3), each its own strict-TDD slice + a pinned-Opus review, ordered so the acceptance gate lands first.

**Resolved forks (operator-confirmed 2026-06-29):**

| # | Fork | Decision |
|---|---|---|
| 1 | Supervisor: separate process vs in-process thread | **Separate OS process.** The kill path must not share fate with the trading loop, and "kill a *wedged* process" is meaningless if the supervisor shares the wedged interpreter. Build the supervisor *decision logic* as an isolated pure unit (clock-injected, fast TDD) **+ ONE subprocess-backed integration test** that spawns a genuinely-wedged ERS child and asserts the parent hard-kills it. The file-heartbeat transport is the same mechanism a separate-host dead-man's-switch uses later. |
| 2 | "cancelAll with its own credential" + dead-man substrate in shadow | **Two distinct signer instances behind a `Signer` Protocol; a FILE-based `Heartbeat`.** The ERS holds `signer_A`; the supervisor holds `signer_B`; the gate asserts `signer_B` de-risks while `signer_A` is wedged (object separation now; real credential separation deferred to POL-4). The heartbeat is a file (fate-isolated — survives a wedged interpreter, readable out-of-process), NOT the in-process `EventStore`/`MonotonicStamper` (they die with the process). |
| 3 | 3-way reconciler now vs defer | **Build the pure `ThreeWayReconciler` NOW over the three existing read seams** (wallet address injected, `None` → dormant, like the cold comove/calibration machinery); the injected-divergence test is a stated acceptance criterion needing no live data. A **settle-window tolerance** is required (on-chain `published_at` is block-height; CLOB-fill vs on-chain timing differs — else it false-positives on every in-flight fill). *(Sub-slice S4.5.)* |
| 4 | How the op-state gates the loop + where state is persisted | **A `SafetyController` consulted at the TOP of `process_pending`** (new `controller=` kwarg, same additive seam as `breaker=`/`pipeline=`), setting `block_reason` BEFORE the L7 breaker so KILL/op-FLATTEN dominate. A long-lived **`ERSController` runloop** owns it and drives the cadence (L7 evaluate, heartbeat beat, signing-canary, reconcile). Durable state (op-state, rate/loss counters, fills) persists in the **`IntentStore` SQLite** (new append-only tables). `process_pending` stays per-call pure (reads state, doesn't own it). |
| 5 | lower-caps / ramp-DOWN | **A NEW re-verified frozen `RiskCaps`, tighten-only.** A ratchet constructs a fresh `RiskCaps` (re-runs `_verify`, new `content_hash` = the audit record), atomically swapped by the `SafetyController`; a guard permits only TIGHTENING. Never mutate; never loosen past the signed $60 ceiling. *(Sub-slice S4.7.)* |
| 6 | One slice vs decomposed | **Decomposed into 7 sub-slices** (§3), each TDD'd + Opus-reviewed, kill-path-first. One monster slice would defeat the per-slice adversarial review that has caught a CRITICAL every slice. |

**The unifying principle: fate isolation.** The trading loop can wedge, bleed, or be confused; the kill path
must survive all three. Everything in S4 fails **closed** (default under ambiguity = *DO NOT TRADE + ALERT*),
and the out-of-band supervisor shares **nothing** with the loop it guards — not the process, not the signer,
not the heartbeat substrate.

---

## 1. Goal & non-goals

**Goal (this effort, S4.1–S4.3):** stand up the operational kill path in shadow and prove it against a
deliberately-wedged process. Concretely: an operational-state machine that can `KILL/PAUSE/HALT/FLATTEN` the
loop ahead of the L7 breaker; the de-risk primitives on the signer seam (`cancel_all`, pre-staged GTD exit
brackets, signing-canary) + the new `RiskCaps` fields + a refuse-to-start self-test; and a **separate-process
out-of-band supervisor** with a file heartbeat and its own signer that hard-kills a wedged ERS and de-risks —
**the acceptance gate.**

**Non-goals (deferred — see §3/§7):** live `cancelAll`/GTD/CLOB-fills (POL-4); a deployed box / systemd /
separate-host supervisor / real NTP / real Telegram transport / real ERC-20 allowances (deploy-time); the L5
AnomalyMonitor (S4.4), the 3-way reconciler + restart-reconcile (S4.5), Telegram (S4.6), and the
realized-loss breakers + ramp-DOWN (S4.7) — all specified here, built in follow-on sub-slices. **No
modification to `evaluate_intent`/the validator** (S4 gates the loop *around* the pure validator, exactly as
S6 did).

---

## 2. Architecture

```
                         ┌──────────────────────────────────────────────────────────┐
                         │  ERSController  (NET-NEW runloop / cadence driver)          │
                         │  starts HALTED → RUNNING only after a clean restart-reconcile│
                         │  each cycle: heartbeat.beat() · breaker.evaluate ·           │
                         │              (S4.4) anomaly.evaluate · (S4.2) canary ·       │
                         │              (S4.5) reconcile · then process_pending(...)    │
                         │   holds ─────────────┐                                       │
                         └──────────────────────┼───────────────────────────────────────┘
                                                ▼
   ┌──────────────────────────── SafetyController (NET-NEW) ────────────────────────────┐
   │  op-state: RUNNING | PAUSED | HALTED | FLATTENING     active_caps: RiskCaps (swappable) │
   │  durable: op-state, rate-counters, loss-streak, fills  (persisted → IntentStore db) │
   │  verdict(portfolio, signer) -> (block_reason | None, derisk_action)                  │
   └───────────────────────────────────────────┬─────────────────────────────────────────┘
                                                │ consulted FIRST (new controller= kwarg)
                                                ▼
   ┌──────────────────────────── process_pending (EXISTING, extended) ───────────────────┐
   │  block_reason precedence:  KILL > op-FLATTEN > L7-FLATTEN > FREEZE_ADDS > NONE         │
   │   1. controller.verdict(...) → may set block_reason (kill/pause/op_flatten) +          │
   │      call signer.cancel_all / signer.flatten   ◀── NET-NEW, ahead of the breaker       │
   │   2. breaker.evaluate(...) → l7_flatten / l7_freeze   (EXISTING, unchanged)            │
   │   3. per intent (FIFO): block_reason ? REJECT(reason) : … _process_intent_pipeline …   │
   │      ACCEPT → signer.place(intent,decision) → signer.place_gtd_bracket(...) → _fold     │
   └─────────────────────────────────────────────────────────────────────────────────────┘
                         signer = signer_A (the ERS's PaperSigner)

   ┌──────────── OutOfBandSupervisor (NET-NEW, SEPARATE PROCESS) ─────────────┐
   │  watches  Heartbeat (FILE: beat/last_beat_age/is_alive)  ── fate-isolated │
   │  decision (pure): dead-man timeout / stale heartbeat → FLATTEN-or-protect │
   │  on WEDGE: (a) hard-kill the ERS process, (b) signer_B.cancel_all() +      │
   │            signer_B.flatten(open) on its OWN signer, (c) pre-staged GTD    │
   │            exit brackets remain standing (passive backstop)                │
   │  signer_B  ≠  signer_A  (distinct instance / own credential)               │
   └───────────────────────────────────────────────────────────────────────────┘

   Hermes boundary PRESERVED: ProposeOnlyFacade exposes NO kill/pause/cancel/op-state surface.
   L8 Telegram (S4.6) + L6 supervisor are SEPARATE authority paths, not reachable via the facade.
```

**Persistence.** S4 introduces genuinely stateful concepts (op-state, rate counters over time windows,
consecutive-loss streaks, and — the critical gap — *venue fills*, since today the store records the ERS
*decision* not the *outcome*). These persist in new **append-only tables in the `IntentStore` SQLite**
(`AUTOINCREMENT` + the shared `MonotonicStamper.stamp()`, mirroring `intent_audit`): an **op/kill/heartbeat
audit table** and a **fill/exposure table**. `process_pending` reads this state via the `SafetyController`; it
does not own it (its per-call purity is preserved).

**Signer Protocol.** Formalize a `Signer` Protocol/ABC so `signer_A` (ERS) and `signer_B` (supervisor) are
structurally-distinct injected dependencies — the type system documents that the supervisor's signer is *not*
the wedged ERS's. `PaperSigner` is the shadow implementation; the real Rust signer (POL-4) is a future
implementation behind the same Protocol.

---

## 3. Sub-slice decomposition (kill-path-first)

Build order front-loads the acceptance gate. **S4.1–S4.3 are this effort.** Each sub-slice: failing-test-first
→ minimal code → a pinned-`model:opus` `superpowers:code-reviewer` pass → re-review after any safety fix.

### S4.1 — `SafetyController` op-state machine + the loop gate *(foundation; BUILD NOW)*
- `OpState` enum/constants `RUNNING | PAUSED | HALTED | FLATTENING` (a NET-NEW frozen-dataclass `OpVerdict`
  mirroring `BreakerState`: `action` + `reason` + `triggers`). **No `Decision`/validator change.**
- `SafetyController` — holds op-state + the active-caps reference + a handle to the durable counters; exposes
  `verdict(portfolio, signer) -> (block_reason: str|None, derisk: str|None)` consulted at the **top** of
  `process_pending` via a new `controller=None` kwarg. Precedence: a KILL/PAUSE/op-FLATTEN verdict sets
  `block_reason` (and, for op-FLATTEN, calls `signer.flatten` / `signer.cancel_all`) BEFORE the L7 breaker, so
  it dominates. `controller=None` → exactly today's behavior (the 448 tests stay green).
- NET-NEW module-level `REASON_*` constants: `l8_kill`, `l8_paused`, `op_flatten`, `unclean_restart`
  (+ the L5/recon ones land in S4.4/S4.5). Free-form `Decision.reason` strings — no schema change.
- The `ERSController` **scaffold** — the long-lived runloop/driver (none exists today) that owns the
  `SafetyController`, wraps `process_pending`, and exposes the cadence hooks (`beat`, `evaluate`, `canary`,
  `reconcile`) extended in later sub-slices. Starts **HALTED**.
- New append-only **op/kill audit table** in `IntentStore` (every op-state transition + kill/pause/flatten
  event, `AUTOINCREMENT` + shared stamper).
- **Extend `test_ers_facade.py`'s structural sweep** to prove `ProposeOnlyFacade` exposes NO
  kill/pause/cancel/cancel_all/op-state-mutation surface — preserve the Hermes-confused-deputy invariant
  against the new S4 control surfaces.

### S4.2 — Signer-seam de-risk primitives + GTD brackets + `RiskCaps` fields + startup self-test *(BUILD NOW)*
- `Signer` Protocol/ABC. Extend `PaperSigner` with: `cancel_all()` (the kill primitive — shadow-records
  `cancelled_all`), `place_gtd_bracket(position, *, exit_price, expiry)` (shadow-records `gtd_exits`),
  `run_canary()` (sign+place+cancel a min-size order — shadow no-op now, real at POL-4; NEVER blind-retry).
- **GTD bracket staging** (`ers/gtd.py`): pure bracket derivation from the accepted `Decision` at entry, sized
  so aggregate standing-exit ≤ `total_open_risk` ($60); called on ACCEPT right after `signer.place` in the
  `_fold` path. *(Cancel-vs-keep semantics: `cancel_all` cancels WORKING/unfilled ENTRY orders; the GTD EXIT
  brackets are the protective standing exits and are NOT cancelled by the kill path — they're the passive
  backstop. Flagged for the Opus review + the live POL-4 signer; see §9.)*
- **New `RiskCaps` fields** (DOC-ONLY in DECISIONS-S0 §4 today; add as frozen `_verify`-checked fields,
  auto-covered by `content_hash`): `weekly_loss_halt=36`, `consecutive_loss=3`, `new_positions_rate` (≤2/hr
  ≤6/day), `gtd_bracket_aggregate` sizing rule, `clock_skew_tolerance_seconds=2`, `signing_canary_interval`,
  `dead_man_switch_timeout`, `reconcile_tolerance`. Extend `_verify` ordering invariants (e.g.
  `consecutive_loss_$ ≤ daily_pending_ceiling ≤ weekly_loss_halt`). Build the `daily_pending_ceiling`
  ($24) halt-new check as a **pure tested predicate** (`would_cross_daily_pending_ceiling`); its actual
  *consumption* (the SafetyController emitting the halt-new block_reason) is **S4.7** (which adds the durable
  per-day pending total it needs) — staged here but dormant until then.
- **Startup self-test** (`ers/startup_selftest.py`): promote `content_hash()` to a real refuse-to-start gate —
  verify the signed `RiskCaps` `content_hash`, the pUSD address `0xC011a7E1…E82DFB`, and the
  contract/struct/domain hashes. (ERC-20 allowance + real sign-canary checks are seams → POL-4/deploy.)

### S4.3 — L6 out-of-band supervisor + Heartbeat + the wedged-process acceptance gate *(THE headline; BUILD NOW)*
- **File-based `Heartbeat`** (`ers/heartbeat.py`): `beat()` writes mtime/counter to a file;
  `last_beat_age(now)` / `is_alive(now, timeout)` read it out-of-process. Fate-isolated (survives a wedged
  interpreter). The `ERSController` calls `beat()` each cycle.
- **`OutOfBandSupervisor`** (`ers/supervisor.py`): the **pure decision unit** — `decide(heartbeat_age, now)`
  → `{OK | FLATTEN_AND_KILL}` (dead-man timeout / stale heartbeat → FLATTEN-or-protect, never merely
  halt-new), clock-injected for fast deterministic TDD. Holds its OWN signer (`signer_B`, a distinct
  `PaperSigner` behind the `Signer` Protocol). On a wedge verdict: hard-kill the ERS process,
  `signer_B.cancel_all()` + `signer_B.flatten(open_positions)`, leaving the pre-staged GTD brackets standing.
- **`WedgedSigner` test double** + the **subprocess-backed acceptance test** (`tests/test_ers_supervisor_kill.py`):
  spawn a real ERS child process whose loop is genuinely wedged (a blocking `WedgedSigner` / a stopped
  `Heartbeat.beat`); the parent supervisor detects the stale file heartbeat, **hard-kills the child**, fires
  `signer_B.cancel_all`/`flatten` on its OWN signer, and asserts the pre-staged GTD brackets survive. This is
  the v1 acceptance gate (only the *live* cancelAll proof is POL-4-deferred).

### S4.4 — L5 `AnomalyMonitor` *(contract-level; FOLLOW-ON)*
Fail-closed clock-injected `evaluate() -> AnomalyState` (mirrors `DrawdownBreaker`), wired into the controller
ahead of the loop. Triggers: abnormal book (crossed/locked = `LocalBook.midpoint()==None`; depth-collapse via
`top_of_book` / `SyntheticDetector`; midpoint-jump = NET-NEW prev-mid state), WS disconnect (a NON-consuming
`MarketStream` health accessor), API 5xx/auth storm (injected status), **clock skew >2s** (a SEPARATE
`ClockSkewSentinel` over injected `wall_clock`+`ntp_ref` — do NOT touch `MonotonicStamper`; **halts SIGNING**,
not just new), fill-recon mismatch (from S4.5), signing-canary failure (scheduler + halt, never blind-retry).
**UMA dispute/MOOV2 watch is DEFERRED** (no dispute-ingestion source exists; stub the seam → sets
`OpenPosition.frozen=True`).

### S4.5 — `ThreeWayReconciler` + restart-reconcile (crash=HOLD) *(contract-level; FOLLOW-ON)*
Pure `reconcile(internal_ledger, clob_fills, onchain_balances) -> ReconResult` over the three EXISTING read
seams (internal = `IntentStore.audit_log` + `Portfolio` + fills table; CLOB = Data-API `/trades`+`/positions`
Envelopes; on-chain = `PolygonLogWatcher.decode_log` ERC-1155 — the auto-redeem-robust truth). Balance-folding
per `token_id`, cross-leg join, divergence-beyond-`reconcile_tolerance` (with a **settle-window** for
block-height-vs-unix-ts lag) → halt+alert; exposure caps computed against the **on-chain-confirmed set**.
Wallet address injected, `None` → dormant. `RestartReconciler`: on boot replay `EventStore` → rebuild ledger
→ rebuild `Portfolio` from the on-chain-confirmed set + ACCEPTED rows → reconcile → only-then RUNNING; any
orphan/divergence → safe-HALT (`unclean_restart`). The **injected-divergence test** is an acceptance
criterion. Live wallet-scoped feeds DEFERRED (POL-4).

### S4.6 — L8 `TelegramController` *(contract-level; FOLLOW-ON)*
Pure command-auth (allowlisted chat-id + signed ROTATING secret + nonce-replay-reject, mirroring the
`news.Source` allowlist-first gate) + a safety-increasing-ONLY command set `{KILL, PAUSE, RESUME, FLATTEN,
LOWER_CAPS, BLACKLIST}` with **structurally NO open-trade verb** (enforced in code, mirroring the
`ProposeOnlyFacade` discipline — a compromised channel can at worst stop the bot). `notify()` is
fire-and-forget; the loop NEVER blocks on Telegram (reads in-memory op-state); alert-send failure → dead-man/
halt. The real send/recv transport is injected as a **fake** for tests; deferred behind a notifier seam.

### S4.7 — Realized-loss breakers + new-positions-rate + auto ramp-DOWN *(contract-level; FOLLOW-ON)*
NET-NEW pure breakers over the reconciled realized-PnL ledger (only the L7 *unrealized* breaker exists today):
`daily_pending_ceiling` $24 halt-new (field exists, enforcement net-new), `weekly_loss_halt` $36 → halt+human-
review, `consecutive_loss` 3-in-a-row, and the budget-independent `new_positions_rate` counter (≤2/hr ≤6/day).
Auto **ramp-DOWN** = the SafetyController swaps in a new tighter re-verified `RiskCaps` (Fork 5) on a
loss-halt / calibration-or-maker-net regression. Ramp-UP stays human-gated (DECISIONS-S0 §8).

---

## 4. Net-new units & seam extensions (build-now signatures)

Each is an isolated, independently-testable unit. Money = `Decimal`; clocks injected for deterministic TDD;
fail-closed throughout.

```python
# ers/safety.py  (S4.1)
RUNNING="RUNNING"; PAUSED="PAUSED"; HALTED="HALTED"; FLATTENING="FLATTENING"   # op-state vocab; FLATTENING is operator/L5/L6-driven, DISTINCT from breaker.py's L7 FLATTEN action
@dataclass(frozen=True)
class OpVerdict: action: str; block_reason: str | None; derisk: str | None; triggers: tuple[str, ...]
class SafetyController:
    def __init__(self, *, caps, store, clock): ...        # holds op-state + active caps + durable-state handle
    def verdict(self, portfolio, signer) -> OpVerdict       # consulted at top of process_pending
    def set_state(self, op_state, *, reason): ...           # operator/L5/L8-driven; audited
    def active_caps(self) -> RiskCaps                       # the swappable reference (ratchet, S4.7)

# ers/controller.py  (S4.1 scaffold)
class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, clock): ...                # starts HALTED
    def run_cycle(self) -> Portfolio                        # beat() · evaluate · process_pending(controller=…)

# ers/service.py  (EXISTING — extended additively)
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None): ...
#   controller=None → verbatim slice-3/S6 behavior; when set, controller.verdict(...) runs FIRST and may
#   set block_reason (KILL/PAUSE/op_flatten) ahead of the L7 breaker.

# ers/service.py PaperSigner  +  a Signer Protocol  (S4.2)
class Signer(Protocol):
    def place(self, intent, decision) -> None: ...
    def flatten(self, positions) -> None: ...
    def cancel_all(self) -> None: ...
    def place_gtd_bracket(self, position, *, exit_price, expiry) -> None: ...
    def run_canary(self) -> bool: ...
#   PaperSigner gains cancel_all()/place_gtd_bracket()/run_canary() with shadow lists cancelled_all/gtd_exits.

# ers/gtd.py  (S4.2)
def derive_bracket(decision, *, caps) -> Bracket            # exit_price + expiry; aggregate ≤ total_open_risk

# ers/startup_selftest.py  (S4.2)
def verify_or_refuse(caps, *, expected_caps_hash, pusd_address, struct_hashes) -> None   # raises → refuse to start

# ers/heartbeat.py  (S4.3)
class Heartbeat:
    def __init__(self, path): ...
    def beat(self) -> None                                  # write mtime/counter (fate-isolated file)
    def last_beat_age(self, now) -> float
    def is_alive(self, now, *, timeout) -> bool

# ers/supervisor.py  (S4.3)
class OutOfBandSupervisor:
    def __init__(self, *, signer, heartbeat, caps, clock): ...   # signer is signer_B — a DISTINCT instance
    def decide(self, now) -> str                            # "OK" | "FLATTEN_AND_KILL"
    def on_wedge(self, ers_pid, open_positions) -> None     # hard-kill + signer_B.cancel_all/flatten
```

New `IntentStore` append-only tables (S4.1/§2): an **op/kill/heartbeat audit table** and a **fill/exposure
table** (`AUTOINCREMENT` + shared `stamper.stamp()`, mirroring `intent_audit`). `propose_trade`'s INSERT-only
chokepoint + `evaluate_intent`/the validator are **UNCHANGED**; `RiskCaps` is extended additively + re-verified.

---

## 5. The acceptance gate (S4.3 — the headline)

A subprocess-backed test (`tests/test_ers_supervisor_kill.py`) that proves fate isolation:
1. **Stage:** start a real ERS child process running an `ERSController` cycle that has ACCEPTED ≥1 position
   (so a pre-staged GTD bracket exists on `signer_A.gtd_exits`) and beats a file `Heartbeat`.
2. **Wedge:** the child's loop hangs (a blocking `WedgedSigner`, or `beat()` stops) — the heartbeat goes stale.
3. **Assert:** the parent `OutOfBandSupervisor` (a) detects `is_alive == False` past `dead_man_switch_timeout`,
   (b) **hard-kills the child PID**, (c) fires `signer_B.cancel_all()` + `signer_B.flatten(open)` on its OWN
   distinct signer (`signer_B is not signer_A`), and (d) the pre-staged GTD exit brackets remain recorded
   (the passive backstop survives the wedge).
This runs on the `PaperSigner` NOW; only the *live* `cancelAll`/GTD proof is POL-4-deferred. Plus the
fast-TDD pure-unit tests for `OutOfBandSupervisor.decide` (dead-man timing) + `Heartbeat` (staleness) with an
injected clock.

---

## 6. Safety invariants & new reason codes

- **Fate isolation:** the supervisor is a separate process, holds a distinct signer, and watches a file
  heartbeat — it shares nothing with the loop it guards.
- **Precedence:** `KILL > op-FLATTEN > L7-FLATTEN > FREEZE_ADDS > NONE`; the `SafetyController` verdict is read
  BEFORE the breaker. Crash/restart starts **HALTED**; RUNNING only after a clean reconcile (S4.5).
- **Fail-closed everywhere; default under ambiguity = DO NOT TRADE + ALERT.** The startup self-test
  **refuses to start** on any signed-caps / address / struct-hash mismatch.
- **New `Decision.reason` codes** (free-form strings, no validator change): `l8_kill`, `l8_paused`,
  `op_flatten`, `unclean_restart` (S4.1); `l5_clock_skew`, `l5_abnormal_book`, `l5_recon_mismatch`,
  `l5_api_storm`, `l5_ws_down`, `l5_canary_fail` (S4.4/S4.5).
- **Hermes boundary preserved:** the L6 kill path and L8 Telegram control are SEPARATE authority paths, NOT
  reachable through `ProposeOnlyFacade` (structural-sweep test extended in S4.1).
- **UNCHANGED:** `evaluate_intent` + the validator + `propose_trade`'s INSERT-only chokepoint. **Additively
  extended:** `RiskCaps` (new frozen `_verify`-checked fields, re-`content_hash`ed), `IntentStore` (new
  append-only tables), `process_pending` (new `controller=` kwarg, `controller=None` == today), `PaperSigner`
  (new de-risk methods).

---

## 7. Built-now vs deferred

| Capability | Built now (PaperSigner/seams) | Deferred |
|---|---|---|
| Op-state machine + loop gate (S4.1) | ✅ full | — |
| Signer de-risk primitives `cancel_all`/GTD/`run_canary` (S4.2) | ✅ shadow records | live cancelAll/GTD/canary (POL-4) |
| `RiskCaps` new fields + startup self-test (S4.2) | ✅ caps + hash/address/struct checks | ERC-20 allowance + real sign-canary checks (POL-4/deploy) |
| Out-of-band supervisor + heartbeat + wedged-process gate (S4.3) | ✅ separate process, distinct signer | separate-HOST supervisor; live de-risk (deploy/POL-4) |
| L5 AnomalyMonitor (S4.4) | ✅ logic over injected/live-ingestion seams | real NTP/chrony; UMA dispute watch |
| 3-way reconciler + restart-reconcile (S4.5) | ✅ pure reconciler + injected-divergence test | live wallet-scoped CLOB/on-chain feeds (POL-4) |
| Telegram control + NOTIFY (S4.6) | ✅ auth/nonce/command-restriction over a fake transport | real bot send/recv (deploy) |
| Realized-loss breakers + ramp-DOWN (S4.7) | ✅ pure breakers over the ledger | — |
| L0 box hardening | startup self-test + (S4.7) allowance/auto-sweep logic | separate Linux users / systemd / egress allowlist / offsite seed backup (deploy) |

---

## 8. Acceptance criteria (this effort, S4.1–S4.3)

1. `./.venv/bin/pytest` green; the **existing 448 tests still pass** (`controller=None` == today; additive seams).
2. New unit tests (TDD, RED→GREEN) per sub-slice, incl.: a KILL/PAUSE op-state short-circuits the loop ahead
   of the L7 breaker; op-FLATTEN calls `signer.flatten`/`cancel_all`; the precedence ordering; the new
   `RiskCaps` fields fail `_verify` on a loosening/inconsistent value; the startup self-test refuses to start
   on a `content_hash`/pUSD-address mismatch; a GTD bracket is staged for every ACCEPT sized ≤ total-open;
   the facade structural sweep proves no kill/cancel/op-state surface leaked in.
3. **The wedged-process acceptance gate (§5)** passes: the out-of-band supervisor hard-kills a genuinely-wedged
   ERS child, de-risks via its OWN signer, and the pre-staged GTD brackets survive.
4. Two pinned-`opus` `superpowers:code-reviewer` passes across the kill-path sub-slices (at minimum after S4.3,
   ideally per sub-slice); re-review after any safety-critical fix.
5. `docs/HANDOFF.md` + memory updated; a POL-6 progress comment; branch `pol-6-safety-envelope`; merge
   `--no-ff` with the verification status; **confirm before pushing**.

---

## 9. Open risks / for the Opus review to probe

- **cancel_all vs the GTD exits.** The kill path must cancel WORKING/unfilled ENTRY orders but KEEP the
  protective GTD EXIT brackets (the passive backstop). On the shadow `PaperSigner` this is a modeling choice;
  the **live POL-4 signer must implement the entry-vs-exit distinction correctly** — a `cancelAll` that also
  kills the protective exits would *increase* risk on a wedge. Probe the shadow semantics + flag the live
  requirement.
- **Subprocess test fidelity / flakiness.** The wedged-process gate spawns a real child; probe for races
  (heartbeat write/read ordering), OS-specific `kill` semantics under WSL, and that the test asserts on the
  supervisor's OWN signer (not the wedged one). It must be deterministic (injected clock for the timeout).
- **op-FLATTEN vs L7-FLATTEN naming/precedence.** Two distinct `FLATTEN` concepts (operator/L5/L6-driven vs
  drawdown-driven). Probe that the precedence is unambiguous and the loop can't conflate them.
- **Persistence correctness.** The new fill/op tables must be append-only + crash-consistent (the restart
  reconcile, S4.5, depends on them). Probe the single-shared-stamper ordering + that a half-written fill can't
  corrupt the op-state read.
- **`daily_pending_ceiling` wiring.** It exists in `RiskCaps` but is unenforced; wiring it into the halt-new
  check must not double-count vs the L7 breaker or the validator's existing caps.
- **Startup self-test scope.** Confirm the refuse-to-start checks that are codeable now (caps hash, pUSD
  address, struct hashes) genuinely gate startup, and that the deferred checks (allowances, real canary) are
  clearly seams, not silently skipped.
