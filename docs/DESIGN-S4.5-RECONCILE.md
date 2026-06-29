# DESIGN — S4.5 / POL-6: Three-way reconciliation + restart-reconcile + the durable fills ledger

**Date:** 2026-06-29 · **Ticket:** [POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4, sub-slice S4.5) ·
**Status:** DESIGN (brainstorm complete, operator-approved forks → awaiting spec review → writing-plans).
**Depends on:** S4.1–S4.3 (the `SafetyController` op-state machine + `unclean_restart` reason + the
`ERSController` runloop + the append-only `op_audit` substrate), S1 (the `EventStore`, the Data-API
`/positions` poller, the `PolygonLogWatcher` ERC-1155 decoder), S3 (`IntentStore`, `Portfolio`/`OpenPosition`,
`RiskCaps`). **Runs SHADOW-ONLY on the `PaperSigner`** — live wallet-scoped feeds are POL-4-blocked.
**This is the prerequisite slice:** it owns the durable fills/realized ledger that S4.4's recon-mismatch
trigger and ALL of S4.7's realized-loss breakers consume, so it is built FIRST (ahead of the nominal S4.4).

> Read [`DESIGN-S4-SAFETY.md`](DESIGN-S4-SAFETY.md) §3 (S4.5 contract) + §2 (architecture / persistence) +
> §9 (open risks) and the master design
> [`specs/2026-06-24-autonomous-polymarket-bot-design.md`](specs/2026-06-24-autonomous-polymarket-bot-design.md)
> §5 (the "Three-way continuous reconciliation" paragraph) first. This doc DEEPENS the approved contract
> into an implementable spec: the ledger schema, the `ReconResult` shape, the divergence metric, the
> settle-window semantics, the restart state machine, and the shadow/DORMANT behavior.

---

## 0. TL;DR + the resolved forks

**What S4.5 is.** Continuous and at-restart, the ERS independently checks its own belief of what it holds
against two external truths, and HALTS rather than trade on a divergence it cannot explain. Three legs:

1. **Internal** — the ERS's own durable record of what it executed (NET-NEW: a `fills` table; today the
   store records only the *decision*, and `Portfolio` is in-memory and dies with the process).
2. **CLOB (Data-API)** — what Polymarket's REST API *says* we hold (`/positions` Envelopes,
   `published_at` = unix ts).
3. **On-chain (Polygon)** — the authoritative ERC-1155 CTF balance (`TransferSingle`/`TransferBatch`
   Envelopes, `published_at` = block HEIGHT). Robust to auto-redemption (which deletes winners from
   `/positions` but leaves the chain trail).

The reconciler is a **pure function** over three already-parsed per-`token_id` balance maps; the EventStore
filtering/parsing is a thin seam above it. On a divergence beyond the signed tolerance (and outside the
settle-window) → `DIVERGED` → the controller HALTS + alerts (`l5_recon_mismatch`). At boot a
`RestartReconciler` rebuilds the ledger from the append-only stores, reconciles, and flips
`HALTED → RUNNING` **only** on a clean result — crash defaults to HOLD.

**Operator-approved forks (2026-06-29):**

| # | Fork | Decision |
|---|---|---|
| 1 | Settle-window length | **A conservative fixed `reconcile_settle_window_seconds = 90` as a hashed, `_verify`-checked, tighten-only `RiskCaps` field** (alongside `reconcile_tolerance`). Part of the signed envelope; a later ratchet may only *shorten* it. |
| 2 | Settle-window KEYING | **Keyed on the ERS's OWN fill timestamp (real seconds, ERS clock) — NOT the on-chain block height.** This sidesteps the block-height-vs-unix-ts footgun entirely: we never compare a block height to a unix ts. A token whose internal fill is `< window` old is `SETTLING` (exempt from the divergence-halt), not `DIVERGED`. |
| 3 | Divergence metric | **Price-free, fail-closed: per `token_id`, `|internal_shares − onchain_shares| × $1` (the outcome-token resolution ceiling) vs `reconcile_tolerance` ($0.50).** On-chain gives shares, not dollars; valuing the share-delta at the $1 ceiling is conservative (a 1-share gap = $1 > $0.50 → halt) and needs no mark. |
| 4 | `wallet=None` behavior | **`DORMANT`, treated as shadow-clean → permits RUNNING.** In pure shadow there is no chain truth, so paper positions would otherwise always "diverge" against an empty chain and the loop could never run (blocking S9). DORMANT is the data-gated-cold state (like cold comove/calibration). Live (wallet injected) ⇒ full 3-way, crash=HOLD. |
| 5 | Fills-ledger wiring | **A NEW `fill_sink=None` keyword seam on `process_pending`** (identical pattern to the existing `gtd_for=None`). `fill_sink=None` ⇒ byte-for-byte today's behavior (the 517 stay green). On ACCEPT it records the just-folded position to the durable `fills` table. |

**The unifying principle:** the on-chain ERC-1155 balance is ground truth; the ERS trades only when its own
belief matches that truth within a signed tolerance, and **fails closed** (HOLD/HALT) on any divergence,
orphan, or unconfirmed-but-recent state it cannot explain. Default under ambiguity = DO NOT TRADE + ALERT.

---

## 1. Goal & non-goals

**Goal.** A pure `ThreeWayReconciler` + a `RestartReconciler` state machine + the durable append-only
`fills` ledger they read, all proven in shadow by an **injected-divergence acceptance test** (no live data).
Wire the restart-reconcile into `ERSController` boot (replacing `_empty_portfolio`) and expose the per-cycle
reconcile as a dormant-by-default seam.

**Non-goals (deferred behind documented seams):**
- Live wallet-scoped CLOB/on-chain feeds (POL-4 — a funded clean-box wallet; keys never touch this box).
- The continuous-cadence reconcile *polling* in a deployed runloop (deploy-time; the pure reconciler +
  the restart hook + the dormant seam are what we build).
- The `ReconResult → S4.4` fill-recon-mismatch trigger wiring (S4.4 *consumes* the `ReconResult`; S4.5 only
  *produces* it).
- Empirical verification of the ERC-1155 share-unit scaling (6 decimals, like USDC) against a real receipt
  (POL-4; pinned as a documented constant with a fail-closed guard meanwhile).
- **No change to `evaluate_intent`/the validator/`propose_trade`'s INSERT-only chokepoint.** The reconcile
  gates the loop *around* the pure validator (it sets op-state via the `SafetyController`), exactly as S4.1
  and S6 did.

---

## 2. Architecture & data flow

```
        ┌──────────────────────── append-only stores (crash-consistent) ─────────────────────────┐
        │  IntentStore.fills (NET-NEW)        IntentStore.op_audit        EventStore.events        │
        │  = the ERS's own fills              = op/kill timeline          = Data-API + Polygon      │
        └───────────────┬───────────────────────────┬───────────────────────────┬─────────────────┘
                        │ fills_log()                │ op_audit_log()            │ all()/replay_until()
                        ▼                            ▼                            ▼
   ┌──────────────── leg parsers (pure given the rows; the EventStore filter is the only seam) ────────────┐
   │  internal_balances(fills_log)      clob_balances(/positions Envelopes)    onchain_balances(Polygon, wallet) │
   │      -> {token_id: Bal}                 -> {token_id: Bal}                     -> {token_id: Bal} | DORMANT  │
   └───────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                    ▼
   ┌──────────────────── ThreeWayReconciler.reconcile(internal, clob, onchain, *, wallet, now, caps) ─────────────┐
   │  per token_id: fold -> cross-join -> divergence = |internal_shares - onchain_shares| * $1                     │
   │  settle-window exempt (internal fill age < caps.reconcile_settle_window_seconds) -> SETTLING                  │
   │  any divergence > caps.reconcile_tolerance (and not settling) -> DIVERGED; wallet None -> DORMANT             │
   │  -> ReconResult(status, divergences, onchain_confirmed_exposure, settling_tokens, triggers)                   │
   └───────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                          ┌─────────────────────────┴──────────────────────────┐
                          ▼ (boot — THIS slice)                                 ▼ (per-cycle running cadence — DEFERRED to S4.4)
   ┌──── RestartReconciler.reconcile_on_boot() ────┐         ┌──── S4.4 AnomalyMonitor (consumes ReconResult) ────────┐
   │  replay stores -> rebuild internal ledger      │         │  OK/DORMANT keep RUNNING; DIVERGED -> the AnomalyMonitor│
   │  -> rebuild Portfolio from on-chain-confirmed  │         │  sets op-state HALTED(l5_recon_mismatch). S4.5 delivers │
   │     ∩ ACCEPTED -> reconcile                     │         │  the pure reconciler the cadence will call; the cadence │
   │  OK/DORMANT -> set_state(RUNNING); else stay    │         │  SEAM + its action are S4.4's (no dead half-wire now).  │
   │  HALTED(unclean_restart). Returns the portfolio.│         └────────────────────────────────────────────────────┘
   └────────────────────────────────────────────────┘
```

**Persistence (mirrors `op_audit`).** The new `fills` table is append-only, `AUTOINCREMENT` + the single
shared `MonotonicStamper.stamp()`, exactly like `intent_audit`/`op_audit` — so the restart-reconcile replays
it crash-consistently and a half-written fill can never corrupt the op-state read. `Portfolio`/`OpenPosition`
stay in-memory; the durable truth of "what we hold" lives in `fills` + the on-chain-confirmed set.

**The join key is `token_id`** — the decimal ERC-1155 id, identical across all three legs (on-chain
`TransferSingle.token_id`, CLOB `/positions.asset`, internal `fills.token_id`). The hex `conditionId` is
deliberately NOT the join key (mixing the hex condition id with the decimal token id is the documented
triad footgun). `conditionId` is carried for provenance/grouping only.

---

## 3. Unit decomposition (build order; each its own strict-TDD cycle)

### S4.5a — the durable fills ledger (`IntentStore`) *(foundation)*
- New append-only table `fills` + `record_fill(...)` + `fills_log()`, mirroring `record_op_event` /
  `op_audit_log` (AUTOINCREMENT + shared `stamper.stamp()`). `propose_trade`'s INSERT-only chokepoint and
  `evaluate_intent`/the validator stay byte-for-byte UNCHANGED.
- Wire into `process_pending` via a NET-NEW `fill_sink=None` keyword (additive, None-defaulting — the
  `gtd_for=` pattern). On ACCEPT, after `signer.place(...)` and the fold, `fill_sink(intent, decision,
  position)` records the fill. `fill_sink=None` ⇒ today's behavior; the 517 stay green.
- `ERSController` gains a matching `fill_sink=None` pass-through.

### S4.5b — the leg parsers *(pure given the rows; the EventStore filter is the only seam)*
- `internal_balances(fills_log) -> dict[token_id, Balance]` — fold the durable `fills` rows.
- `clob_balances(envelopes) -> dict[token_id, Balance]` — fold Data-API `/positions` Envelopes
  (`source == "data-api"`, path-tagged `event_id` starting `/positions:`). Parse `content` JSON; key by
  `asset` (token_id).
- `onchain_balances(envelopes, *, wallet) -> dict[token_id, Balance] | DORMANT` — fold Polygon
  `TransferSingle`/`TransferBatch` Envelopes (`source == "polygon-chain"`): `to == wallet` credits
  `+value`, `from == wallet` debits `−value`, per `token_id`. `wallet is None` ⇒ return the DORMANT
  sentinel. Share-unit scaling (raw ERC-1155 `value` → shares via 6 decimals) is a pinned constant
  `_SHARE_DECIMALS = 6`, flagged for POL-4 verification; a non-integer/negative net is fail-closed.

### S4.5c — the pure `ThreeWayReconciler` *(the heart)*
- `reconcile(internal, clob, onchain, *, wallet, now, caps) -> ReconResult`. Algorithm in §4. CLOB is a
  CORROBORATING leg (advisory: auto-redemption makes `/positions` lossy); **on-chain is AUTHORITATIVE** —
  `onchain_confirmed_exposure` and the divergence verdict are computed against the on-chain set. A
  CLOB-only mismatch (chain agrees with internal) is recorded in `triggers` but does NOT halt (on-chain
  wins); an on-chain mismatch halts.

### S4.5d — the `RestartReconciler` + the cap field + the controller wiring *(integration)*
- `RestartReconciler.reconcile_on_boot()` — §5 state machine; replaces `ERSController._empty_portfolio` at
  boot, returns the rebuilt `Portfolio`, and drives the `HALTED→RUNNING` transition via the
  `SafetyController`.
- New `reconcile_settle_window_seconds = 90` `RiskCaps` field (§6).
- `ERSController` gains a dormant-by-default `reconciler=None` seam for the per-cycle reconcile; `None` ⇒
  the scaffold's behavior (no reconcile), so existing controller tests stay green.

---

## 4. The pure reconciler algorithm (the load-bearing detail)

```
reconcile(internal, clob, onchain, *, wallet, now, caps) -> ReconResult:
  if wallet is None or onchain is DORMANT:
      return ReconResult(DORMANT, divergences=(), onchain_confirmed_exposure=Decimal(0),
                         settling_tokens=(), triggers=("dormant_no_wallet",))
  window_ns = caps.reconcile_settle_window_seconds * 1_000_000_000     # seconds -> monotonic ns
  divergences = []; settling = []; triggers = []
  for token_id in (internal.keys() | clob.keys() | onchain.keys()):   # union: orphans on ANY leg
      i = internal.get(token_id);  o = onchain.get(token_id)           # Balance or absent(=0 shares)
      d_shares = abs(shares(i) - shares(o))
      d_dollars = d_shares * Decimal(1)                                 # $1/share resolution ceiling
      if d_dollars <= caps.reconcile_tolerance:                         # within tolerance: agree
          continue
      # Settle-window exempt: an in-SESSION fill younger than the window may simply not be on-chain
      # yet. latest_fill_at is the monotonic-ns fill stamp IN THE SAME DOMAIN as `now`; it is None for
      # replayed/pre-restart rows (a prior monotonic epoch is NOT comparable to this `now`), so a
      # pre-restart unconfirmed fill gets NO grace -> fail-closed DIVERGED.
      if i is not None and i.latest_fill_at is not None and (now - i.latest_fill_at) < window_ns:
          settling.append(token_id); triggers.append(f"settling:{token_id}"); continue   # exempt
      divergences.append(Divergence(token_id, internal=shares(i), onchain=shares(o), dollars=d_dollars))
      # CLOB cross-check is advisory (on-chain authoritative); record but don't change the verdict.
      if clob.get(token_id) is not None and shares(clob[token_id]) == shares(o):
          triggers.append(f"clob_confirms_chain:{token_id}")
  onchain_confirmed_exposure = sum(shares(b) * Decimal(1) for b in onchain.values())   # worst-case $
  if divergences:    return ReconResult(DIVERGED, tuple(divergences), onchain_confirmed_exposure, tuple(settling), tuple(triggers))
  if settling:       return ReconResult(SETTLING, (), onchain_confirmed_exposure, tuple(settling), tuple(triggers))
  return ReconResult(OK, (), onchain_confirmed_exposure, (), tuple(triggers))
```

**Why this is fail-closed & footgun-free:**
- *Union over all three legs* catches an **orphan** (a token on-chain the internal ledger never recorded, or
  vice versa) — `shares(absent) = 0`, so an orphan's full balance is the divergence.
- *Settle-window keyed on the internal fill age* (real seconds) means the block-height `published_at` is
  never used in the time arithmetic — the footgun cannot bite. A recent unconfirmed fill is `SETTLING`, not
  a false `DIVERGED`.
- *$1/share ceiling* is the worst-case value of any outcome token (it resolves to $0 or $1), so the
  dollar-tolerance comparison is conservative and price-free; it never *under*-reports a divergence.
- *On-chain authoritative*: a CLOB/`/positions` discrepancy with the chain (e.g. post-redemption) does not
  halt; only an on-chain divergence does.

`ReconResult` and `Divergence` are frozen dataclasses; `status` is the string enum `OK | DIVERGED | SETTLING
| DORMANT`. `Balance` carries `shares: Decimal` and (internal only) `latest_fill_at: int | None` — the
monotonic-ns stamp of the token's most recent IN-SESSION fill, in the same clock domain as the reconciler's
`now`, or `None` for replayed/pre-restart rows (which therefore never receive settle-window grace). The
reconciler's `now` is the injected monotonic-ns now (same domain as `MonotonicStamper.stamp()`), so the
window arithmetic is unit-consistent and deterministic under test.

---

## 5. The `RestartReconciler` state machine (crash = HOLD)

```
reconcile_on_boot():
  internal = internal_balances(store.fills_log())
  clob     = clob_balances(event_store.all() filtered source=data-api /positions)      # advisory
  onchain  = onchain_balances(event_store.all() filtered source=polygon-chain, wallet) # authoritative | DORMANT
  result   = reconciler.reconcile(internal, clob, onchain, wallet=wallet, now=clock.now(), caps=caps)
  portfolio = rebuild_portfolio(onchain if live else internal, accepted_rows)          # on-chain-confirmed ∩ ACCEPTED
  if result.status in (OK, DORMANT):
      controller.set_state(RUNNING, reason="restart_reconciled")     # the ONLY auto HALTED->RUNNING
  else:   # DIVERGED, SETTLING, or any orphan
      controller.set_state(HALTED, reason=REASON_UNCLEAN_RESTART)    # stay HALTED; alert
  return portfolio
```

- **Starts from HALTED** (the `SafetyController` boot default, reason `unclean_restart`). The restart-reconcile
  is the ONLY automatic path to RUNNING; everything else (operator RESUME, S4.6 Telegram) is a separate
  authority.
- **`DORMANT` (shadow / no wallet) ⇒ RUNNING.** Rebuild the portfolio from the internal ACCEPTED set (there
  is no chain to confirm against in shadow). This is the data-gated-cold state that lets S9's shadow harness
  run; it carries `triggers=("dormant_no_wallet",)` so the op-audit/HANDOFF is honest that reconciliation
  was not actually performed.
- **`SETTLING` is a running-cadence concept, not a boot one.** Replayed fills carry `latest_fill_at = None`
  (a prior monotonic epoch is not comparable to this `now`), so at boot they get NO settle-window grace: an
  unconfirmed pre-restart fill is `DIVERGED`, not `SETTLING`. The state machine still treats a `SETTLING`
  result as **stay HALTED** (fail-closed) for completeness, but in practice the boot reconcile yields only
  `OK` / `DORMANT` / `DIVERGED`. During the *running* cadence, in-session SETTLING tokens are merely excluded
  from the halt — expected in-flight state.
- **`DIVERGED`/orphan ⇒ stay HALTED(`unclean_restart`)** + alert; a human must reconcile. Never auto-resume.
- The transition is audited (`set_state` writes an `op_audit` row before mutating), so the boot decision is
  always explained.

---

## 6. The new cap field

Add to `RiskCaps` (additive, frozen, `_verify`-checked, auto-covered by `content_hash` via `asdict`):

```python
reconcile_settle_window_seconds: int = 90   # internal-fill age under which a not-yet-confirmed token is
                                            # SETTLING (exempt from the divergence halt), NOT DIVERGED.
```

`_verify` additions: `reconcile_settle_window_seconds > 0` (join the existing strictly-positive integer
loop). It is **tighten-only**: a ratchet (S4.7) may only *decrease* it (a shorter window = stricter = fewer
exemptions). S4.5 only adds + hashes + `_verify`s the field; the tighten-only *guard* enforcement lives in
the S4.7 ratchet (it already governs every hashed field). The pinned 90 s is covered by the injected
acceptance test and is the only operator-tunable number here.

---

## 7. The fills ledger schema + the `fill_sink` seam

```sql
CREATE TABLE IF NOT EXISTS fills (
    fill_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at             INTEGER NOT NULL,   -- shared MonotonicStamper.stamp()
    intent_id      TEXT    NOT NULL,
    token_id       TEXT    NOT NULL,   -- the JOIN KEY (decimal ERC-1155 id)
    condition_id   TEXT    NOT NULL,   -- provenance/grouping only
    side           TEXT    NOT NULL,
    shares         TEXT    NOT NULL,   -- Decimal-as-string
    price_exec     TEXT    NOT NULL,
    worst_case_risk TEXT   NOT NULL    -- = notional for a long
);
```

```python
# IntentStore (S4.5a) — mirrors record_op_event / op_audit_log exactly.
def record_fill(self, *, intent_id, token_id, condition_id, side, shares, price_exec, worst_case_risk): ...
def fills_log(self): ...   # [{at, intent_id, token_id, condition_id, side, shares: Decimal, ...}], ORDER BY fill_id

# service.process_pending (S4.5a) — additive None-defaulting seam, the gtd_for= pattern.
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None,
                    gtd_for=None, fill_sink=None): ...
#   On ACCEPT, after signer.place(...) and the fold: if fill_sink is not None: fill_sink(intent, decision, portfolio.positions[-1])
#   fill_sink=None  =>  byte-for-byte today's behavior (the 517 stay green).
```

`shares = worst_case_risk / price_exec` (notional / entry, the `OpenPosition` convention). Recording is
**opt-in**: `process_pending` and `ERSController` BOTH default `fill_sink=None` (⇒ byte-for-byte today's
behavior — no `fills` rows, the 517 and the S4.1 controller tests stay green). A small helper
`make_fill_sink(store)` returns the recording callable `lambda intent, decision, position:
store.record_fill(...)`; the S4.5d integration (and real operation) constructs the `ERSController` WITH that
sink so the durable internal leg is populated for every ACCEPT. Nothing in the existing scaffold passes it,
so back-compat is exact; the reconciler simply sees an empty internal leg until a recording sink is wired.

---

## 8. Safety invariants & new reason codes

- **On-chain is ground truth; default = HOLD.** Any unexplained divergence/orphan halts. CLOB is advisory.
- **Crash = HOLD; auto-RUNNING only via a clean restart-reconcile.** Boot starts HALTED(`unclean_restart`).
- **Settle-window never uses block height** — keyed on the internal fill age in real seconds.
- **Fail-closed parsing:** a malformed Envelope, a non-integer/negative net on-chain balance, or a
  share-unit ambiguity ⇒ treat as divergence/halt, never as "agree". Untrusted data is data.
- **Append-only + single shared stamper** for `fills` (crash-consistent replay; no half-write corruption).
- **New `Decision.reason` code:** `l5_recon_mismatch` (free-form string; NO validator/schema change).
  Reuses the existing `unclean_restart` for the boot-HALT path.
- **UNCHANGED:** `evaluate_intent`, the validator, `propose_trade`'s INSERT-only chokepoint, and
  `process_pending`'s decision/precedence flow (extended ONLY via the `fill_sink=None` seam and the
  `ERSController` boot/cadence wiring). `RiskCaps` extended additively + re-`content_hash`ed.

---

## 9. Built-now vs deferred

| Capability | Built now (shadow / injected) | Deferred |
|---|---|---|
| Durable `fills` ledger + `fill_sink` seam | ✅ full | — |
| Leg parsers (internal/clob/onchain) | ✅ pure over Envelope rows | live wallet-scoped feeds (POL-4) |
| Pure `ThreeWayReconciler` + `ReconResult` | ✅ full (injected-divergence test) | — |
| `RestartReconciler` (crash=HOLD) + boot wiring | ✅ full | — |
| `reconcile_settle_window_seconds` cap | ✅ added/hashed/verified | tighten-only ratchet enforcement (S4.7) |
| Per-cycle reconcile cadence | ✅ dormant `reconciler=None` seam | live polling cadence (deploy) |
| `ReconResult → S4.4` recon-mismatch trigger | seam (S4.4 consumes it) | S4.4 |
| ERC-1155 share-unit scaling (6 decimals) | pinned constant + fail-closed guard | empirical POL-4 verification |

---

## 10. Acceptance criteria

1. `./.venv/bin/pytest` green; the **existing 517 tests still pass** (`fill_sink=None`/`reconciler=None` ⇒
   today's behavior; additive seams; `RiskCaps` extended additively + re-verified).
2. New unit tests (strict TDD, RED→GREEN, one concern each):
   - `record_fill`/`fills_log` are append-only + ordered + Decimal-round-trip-exact.
   - each leg parser folds correctly; `onchain_balances(wallet=None) ⇒ DORMANT`; a malformed Envelope is
     skipped/fails-closed, not silently "agree".
   - **the injected-divergence test (acceptance criterion):** internal holds N shares of a token, on-chain
     holds 0 (wallet injected) ⇒ `DIVERGED` with the token's `$N` divergence; and the `RestartReconciler`
     stays `HALTED(unclean_restart)`.
   - `wallet=None ⇒ DORMANT ⇒ RestartReconciler sets RUNNING` (the shadow path).
   - the settle-window: an in-session internal fill with `latest_fill_at` younger than `90 s` absent
     on-chain ⇒ `SETTLING` (not `DIVERGED`); the same fill aged past the window ⇒ `DIVERGED`; and a replayed
     fill with `latest_fill_at = None` absent on-chain ⇒ `DIVERGED` (no boot grace — fail-closed).
   - on-chain authoritative: a CLOB-only mismatch (chain agrees with internal) does NOT halt.
   - `reconcile_settle_window_seconds` fails `_verify` at `<= 0`.
3. Two pinned-`opus` `superpowers:code-reviewer` passes (spec-compliance reviewer first, then the
   pinned-opus reviewer); mutation-test the injected-divergence + settle-window + DORMANT tests (break the
   impl, confirm the test fails) to prove they aren't vacuous; re-review after any safety-critical fix.
   Verify the tree is clean afterward (`git status --porcelain` empty AND no stray `MUTATION` markers).
4. `docs/HANDOFF.md` + memory updated; a POL-6 progress comment; branch `pol-6-s4.5-reconcile`; merge
   `--no-ff` with the verification status; **confirm before pushing**.

---

## 11. Open risks / for the Opus review to probe

- **DORMANT vs fail-closed.** The `wallet=None ⇒ DORMANT ⇒ RUNNING` path is the one place reconciliation is
  *skipped* rather than *passed*. Probe that it can ONLY be reached with no wallet (never masks a real
  divergence when a wallet IS set), that the `dormant_no_wallet` trigger is always recorded, and that a live
  wallet with an empty chain (genuinely zero positions) is distinguished from dormant.
- **Settle-window correctness.** Probe the boundary (`age == window`), that the window keys on the internal
  fill stamp not any leg's `published_at`, and that a SETTLING token can't permanently mask a real divergence
  (once it ages out it must flip to DIVERGED).
- **Orphan detection via the union.** Probe a token present ONLY on-chain (internal never recorded it) and a
  token present ONLY internally (chain shows nothing past the window) — both must DIVERGE.
- **Share-unit scaling.** The 6-decimal `value→shares` constant is unverified until POL-4; probe that a wrong
  scaling fails CLOSED (over-reports divergence → halt) rather than open, and that the guard rejects a
  non-integer net.
- **`fill_sink` seam purity.** Probe that `fill_sink=None` reproduces pre-S4.5 behavior byte-for-byte (no
  `fills` rows written, the 517 unaffected) and that the recorded `shares = worst_case_risk / price_exec` is
  exact (Decimal, no float).
- **Restart replay crash-consistency.** Probe that a half-written `fills` row (simulated) can't corrupt the
  rebuild, and that the rebuilt portfolio matches the on-chain-confirmed ∩ ACCEPTED set (not the raw ACCEPTED
  set) when live.
- **CLOB-advisory boundary.** Confirm a CLOB/`/positions` discrepancy that the chain does NOT corroborate
  cannot by itself halt (on-chain authoritative) — and that this can't be exploited to mask a real on-chain
  divergence.
