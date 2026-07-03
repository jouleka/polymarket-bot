# DESIGN — S4.6 / POL-6: L8 TelegramController (remote authenticated safety control)

**Date:** 2026-07-03 · **Ticket:** [POL-6](https://mysigner.youtrack.cloud/issue/POL-6) (S4 sub-slice 6 — the LAST S4 sub-slice) ·
**Status:** DESIGN (forks operator-resolved 2026-07-03; awaiting operator spec review → writing-plans).
**Depends on:** S4.1 (`SafetyController.set_state`/op-states + the `l8_*` reason codes + the facade structural-sweep
precedent), S4.7 (`swap_caps` + the `ers/ramp.py` step factories for LOWER_CAPS), S4.3 (the `Heartbeat`/
`OutOfBandSupervisor` dead-man the alerts-down halt complements), the `Signer` Protocol (the injected-fake
template), the news `allowlist`/`sanitizer` + `truthgate` (the allowlist-first + neutralize templates).
**Runs SHADOW-ONLY over a FAKE transport.** Contract-level parent: `DESIGN-S4-SAFETY.md` §3 S4.6.

> Master design §5 L8: *"Telegram NOTIFY + remote KILL/PAUSE only. Inbound commands authenticated (allowlisted
> chat-id + signed rotating secret + nonces), restricted to safety-increasing actions
> (KILL/PAUSE/RESUME/FLATTEN/lower-caps/blacklist) — no 'open trade' command exists, so a compromised channel
> can at worst stop the bot. Trading loop never blocks on Telegram."* Plus the Security case-catalog row:
> *"signed/rotating secret + chat-id + nonces; fail-safe halt if alerts down; dead-man's-switch."* Everything
> here is NET-NEW (grep-confirmed: zero existing telegram/hmac/nonce/notify/blacklist code).

---

## 0. TL;DR + resolved forks

S4.6 is the **remote authenticated safety-control channel** — the human's final-authority override that bypasses
Hermes entirely and is obeyed deterministically. It is built as: (1) a **pure auth core** (`CommandAuth`) that
mirrors the news allowlist-first gate — allowlisted chat-id FIRST, then the safety-increasing-only command set,
then constant-time HMAC-SHA256 under the current rotating secret, then a monotonic per-chat-id nonce; every step
fail-closed; (2) a **structurally-bounded `TelegramController`** that mirrors `ProposeOnlyFacade` — it composes a
name-mangled `SafetyController`, exposes ONLY `{drain, notify}`, and has structurally NO `place`/`sign`/open-trade
path (a compromised channel can at worst STOP the bot); (3) **`notify()`** best-effort fire-and-forget over the
fake transport — the loop NEVER blocks or raises on Telegram, and a persistent alerts-down condition itself
HALTS (the master-design fail-safe); (4) the `ERSController(telegram=)` seam that **drains authenticated commands
at the TOP of `run_cycle`** (ahead of even the L5 anomaly consult, so an operator KILL dominates the cycle) — all
on the single-threaded serial runloop, so `swap_caps`'s no-cross-thread requirement holds by construction.

**Resolved forks (operator-confirmed 2026-07-03):**

| # | Fork | Decision |
|---|---|---|
| 1 | RESUME safety | **Operator-trusted.** RESUME → `set_state(RUNNING, reason=REASON_L8_RESUME)` from PAUSED **or** HALTED, audited. Directly satisfies the documented "RESUME clears the sticky L5/loss halts" (an authenticated human override after an alert, consistent with human-gated authority). The live fail-closed hardening — re-run the 3-way reconcile before the flip — is a documented S9 seam (the `RestartReconciler` isn't in the runloop yet and is DORMANT in shadow, so gating would be cosmetic now). This is the ONLY operator HALTED→RUNNING path; the automatic one remains the boot-reconcile. |
| 2 | BLACKLIST scope | **Durable set + seam.** BLACKLIST records `(target_kind, target_value)` — wallet/market/source — into a NEW durable append-only `blacklist` table, authenticated + audited, with a read accessor. ERS-side ENFORCEMENT is a documented seam (consumed by the future FOLLOW/detector path, hard-off today; the docs' only substantive definition is "blacklist negative-post-entry-drift wallets"). No sacred-loop change. |
| 3 | Nonce model | **In-session monotonic.** Per allowlisted chat-id, reject any nonce ≤ last-seen (in-memory, within session). Per-restart secret rotation defeats cross-restart replay. Sufficient because the safety-increasing-only command set makes replay low-stakes (replaying a KILL just re-halts). A durable nonce table is a documented hardening, not built. |

**Baked (safety/contract-forced, not asked):** HMAC-SHA256 with `hmac.compare_digest` constant-time compare
(stdlib; the repo's only prior crypto is `hashlib.sha256` content-hashing) under the CURRENT rotating secret;
the rotating secret lives in deploy-config OFF-REPO (mirroring `deploy/hermes/config.yaml`'s `holds_keys:false` +
`secrets_in_model_mutable_text:false`) — S4.6 builds the VERIFY side + a `rotate()` seam + a current-secret
holder, the cadence (per-restart + operator-triggered) is deploy-config not a code constant; the inbound message
is UNTRUSTED DATA never instructions — `neutralize()` every id/command field (strip control/bidi/zero-width) +
fail-LOUD on malformed structure (the news DOCTYPE-guard discipline); the command drain runs at the TOP of
`run_cycle` on the serial runloop (KILL dominates; `swap_caps` stays single-threaded); LOWER_CAPS applies the
existing deepest ramp step `step_weekly` through `swap_caps` (tighten-only-guarded, audited, idempotent — NO
arbitrary-caps parse surface from an untrusted message; per-field operator caps = a future reviewed-spec seam);
FLATTEN reuses the existing FLATTENING op-state (de-risks once in `verdict` then settles HALTED); the command set
is a frozenset of EXACTLY the six verbs, structurally pinned; `telegram=None` == pre-S4.6 byte-for-byte.

---

## 1. Goal & non-goals

**Goal:** (a) `ers/telegram_auth.py` — the pure `CommandAuth` (allowlist-first chat-id → command-set → HMAC →
monotonic nonce, each fail-closed) + the `RawMessage`/`AuthResult` shapes + the untrusted-field neutralize + the
`TelegramTransport` Protocol + the new `REASON_L8_*` constants; (b) `ers/telegram.py` — the structurally-bounded
`TelegramController` (the six-verb command map over a name-mangled `SafetyController`, the `drain()` poll→auth→
apply→audit path, LOWER_CAPS→`step_weekly`, the structural-sweep + command-set pins); (c) `notify()` best-effort
+ the alerts-down→HALT health counter; (d) the `blacklist` durable table (additive on `IntentStore`) + the
BLACKLIST wiring + read accessor; (e) the `ERSController(telegram=)` seam draining at the top of `run_cycle` +
the whole-slice e2e.

**Non-goals (deferred; §7):** the real Telegram bot send/recv transport (deploy — the fake is the test seam);
the actual secret VALUE + its rotation cadence (deploy-config, off-repo); reconcile-gated RESUME (S9, when the
`RestartReconciler` is in the runloop + live feeds exist — today DORMANT); BLACKLIST enforcement in the sizing
path (the FOLLOW/detector consumer is hard-off); per-field operator LOWER_CAPS (a reviewed-spec surface —
untrusted arbitrary-caps construction is not built); a durable nonce table (in-session + rotation suffices);
the heartbeat-stop tie-in for alerts-down (S4.6 halts via op-state; the deploy refinement is to also stop
`beat()` so the out-of-band supervisor fires). **No change to** `evaluate_intent`/the validator,
`propose_trade`'s chokepoint, `process_pending`'s signature/flow, `set_state`/`swap_caps`/`verdict` internals.

## 2. Architecture

```
Fake TelegramTransport (Protocol; real deferred)
    .poll() -> list[RawMessage]   (NON-blocking; returns pending inbound, [] when none)
    .send(text) -> bool           (best-effort; True on success, False/raise on failure)

ERSController.run_cycle (extended additively; telegram=None == today byte-for-byte)
  0. if telegram is not None: telegram.drain()   # NET-NEW, the VERY FIRST step -- operator KILL dominates
  1. heartbeat.beat()                             (unchanged)
  2. L5 anomaly consult                           (unchanged, S4.4)
  3. loss-breaker consult                         (unchanged, S4.7)
  4. process_pending(..., caps=active_caps())     (unchanged)

TelegramController.drain()   (all on the serial runloop -- no cross-thread swap_caps)
  for raw in self.__transport.poll():             # non-blocking; a hung real transport is deploy's concern
      result = self.__auth.authenticate(raw)      # allowlist-first, fail-closed (below)
      if not result.ok:
          self.__store.record_op_event(kind="l8_refused", reason=result.reason, detail=raw.chat_id_neutralized)
          continue                                # a refused command is audited and dropped; loop continues
      self.__apply(result.command, result.payload)  # the bounded six-verb map (below); audits kind="l8_command"

CommandAuth.authenticate(raw) -> AuthResult   (PURE; the FIVE fail-closed gates, IN ORDER)
  1. structure:   neutralize(chat_id/command/nonce); malformed/absent field -> AuthResult(False, "l8_malformed")
  2. chat-id:     self._allow.get(chat_id) is None  -> AuthResult(False, "l8_bad_chat")     # THE allowlist gate, first
  3. command:     command not in _COMMAND_SET       -> AuthResult(False, "l8_unknown_cmd")  # structural: no open verb dispatches
  4. hmac:        not compare_digest(_mac(raw, self._secret()), raw.sig) -> AuthResult(False, "l8_bad_sig")  # constant-time
  5. nonce:       nonce <= self._seen.get(chat_id, -inf) -> AuthResult(False, "l8_replay")  # monotonic, per chat-id
  # all pass: self._seen[chat_id] = nonce ; return AuthResult(True, command=command, payload=payload)

TelegramController.__apply  (the safety-increasing-ONLY command map -- structurally no open-trade verb)
  KILL       -> ctl.set_state(HALTED,     reason=REASON_L8_KILL)
  PAUSE      -> ctl.set_state(PAUSED,     reason=REASON_L8_PAUSED)
  RESUME     -> ctl.set_state(RUNNING,    reason=REASON_L8_RESUME)     # operator-trusted (Fork 1); from PAUSED or HALTED
  FLATTEN    -> ctl.set_state(FLATTENING, reason=REASON_OP_FLATTEN)    # de-risks once in verdict, settles HALTED
  LOWER_CAPS -> ctl.swap_caps(step_weekly(ctl.active_caps()), reason=REASON_L8_LOWER_CAPS)  # deepest ramp step, tighten-only
  BLACKLIST  -> store.record_blacklist(kind=payload.target_kind, value=payload.target_value); (audited l8_blacklist)
```

- **Fail isolation.** A hung/hostile transport cannot wedge the loop: `poll()` is non-blocking by contract (the
  fake returns immediately; the real transport MUST timeout-bound — deploy's concern), and every per-message
  authenticate/apply is isolated (a raising apply is caught, audited, and the drain continues — mirroring
  `news.poll_all` per-source isolation). `notify()` is best-effort and never raises into the loop.
- **Structural bounding (the ProposeOnlyFacade mirror).** `TelegramController` composes a NAME-MANGLED
  `SafetyController` (`self.__ctl` → `_TelegramController__ctl`), exposes ONLY `{drain, notify}`, is not callable,
  is not a `SafetyController` subclass, and its source never references `place`/`propose_trade`/`sign`/`submit`/
  `open`. The command MAP is the only path to `set_state`/`swap_caps`, and it maps EXACTLY the six safety-
  increasing verbs — a structural-sweep test (extending `test_ers_facade.py`'s precedent) proves no trade verb.
- **Serial-runloop safety.** `drain()` runs inside `run_cycle`; `set_state`/`swap_caps` therefore execute on the
  single serial thread, satisfying the `swap_caps` concurrency docstring ("LOWER_CAPS must route through the same
  serial runloop") by construction — the receive path AUTHENTICATES + APPLIES in the same serial step, no queue,
  no other thread.
- **Alerts-down → HALT.** `notify()` increments a consecutive-failure counter on a `False`/raising `send`; when
  it crosses `_alerts_down_threshold` (a plain constructor default, deploy-tunable — an alerting concern, not a
  risk cap) the controller sets `HALTED(REASON_L8_ALERTS_DOWN)` — the master-design "fail-safe halt if alerts
  down." A successful `send` resets the counter. The deploy refinement (also stop `beat()` so the out-of-band
  dead-man fires) is documented, not built.

## 3. The six commands + the auth gates

**Command map (safety-increasing-ONLY; the frozenset is structurally pinned):**

| Verb | Primitive | Reason | Notes |
|---|---|---|---|
| KILL | `set_state(HALTED, ...)` | `l8_kill` (exists) | dominates the cycle (drained first) |
| PAUSE | `set_state(PAUSED, ...)` | `l8_paused` (exists) | soft halt; blocks new, no de-risk |
| RESUME | `set_state(RUNNING, ...)` | `l8_resume` (NEW) | operator-trusted; PAUSED or HALTED → RUNNING (Fork 1) |
| FLATTEN | `set_state(FLATTENING, ...)` | `op_flatten` (exists) | de-risks once in `verdict`, settles HALTED |
| LOWER_CAPS | `swap_caps(step_weekly(active_caps), ...)` | `l8_lower_caps` (NEW) | deepest ramp step; tighten-only-guarded; idempotent |
| BLACKLIST | `store.record_blacklist(kind, value)` | `l8_blacklist` (NEW) | durable set; enforcement deferred (Fork 2) |

**The five fail-closed auth gates (IN ORDER — allowlist FIRST, every one refuses-and-audits):**

1. **Structure** — `neutralize()` chat_id/command/nonce (strip Cc/Cf control/bidi/zero-width so a spoofed
   command can't smuggle delimiters); an absent/malformed field → refuse `l8_malformed`. Signature is bytes,
   compared raw. The message is UNTRUSTED DATA, never instructions.
2. **Chat-id allowlist** — `self._allow.get(chat_id) is None → refuse l8_bad_chat`. The FIRST semantic check,
   mirroring `NewsPoller.poll_source`'s `dict.get(...) is None → raise` as the very first statement. The
   allowlist is an operator-curated dict `{chat_id: role}` injected at construction.
3. **Command set** — `command not in _COMMAND_SET → refuse l8_unknown_cmd`. Structural: an unknown or open-trade
   verb never reaches dispatch (defense-in-depth with the structural-sweep test).
4. **HMAC** — `hmac.compare_digest(_mac(canonical, current_secret), provided_sig)` — constant-time; mismatch →
   refuse `l8_bad_sig`. The canonical message is a fixed field order `chat_id|command|payload|nonce`; the current
   secret comes from `self._secret()` (a 0-arg accessor over the rotating-secret holder — `rotate(new)` swaps it).
5. **Nonce** — `nonce <= self._seen.get(chat_id, sentinel) → refuse l8_replay`; else record `self._seen[chat_id]
   = nonce` (in-session monotonic, per chat-id).

Only when ALL five pass does `authenticate` return `ok=True`. A refusal at any gate is audited `kind="l8_refused"`
with the specific reason and drops the message; the drain continues to the next (per-message isolation).

## 4. Net-new units & seam extensions (the pinned contract block)

```python
# ers/telegram_auth.py  (NET-NEW — the pure auth core + transport Protocol + shapes)
@dataclass(frozen=True)
class RawMessage:                 # the untrusted inbound; fields are strings/bytes as received
    chat_id: str; command: str; payload: str; nonce: str; sig: bytes
@dataclass(frozen=True)
class AuthResult:
    ok: bool; reason: str; command: str | None = None; payload: str | None = None; chat_id: str = ""

@runtime_checkable
class TelegramTransport(Protocol):
    def poll(self) -> list: ...        # non-blocking; pending RawMessages, [] when none
    def send(self, text) -> bool: ...  # best-effort; True on success

class SecretHolder:                    # the rotating-secret holder (value lives off-repo/deploy)
    def __init__(self, secret: bytes): ...
    def current(self) -> bytes: ...
    def rotate(self, new_secret: bytes) -> None: ...   # per-restart + operator-triggered (deploy cadence)

class CommandAuth:
    def __init__(self, *, allowlist, secret_holder, command_set=_COMMAND_SET): ...
    def authenticate(self, raw) -> AuthResult    # the 5 fail-closed gates in order (§3); monotonic nonce state

_COMMAND_SET = frozenset({"KILL","PAUSE","RESUME","FLATTEN","LOWER_CAPS","BLACKLIST"})  # structurally pinned
def canonical_message(raw) -> bytes    # b"chat_id|command|payload|nonce" (fixed order; the HMAC input)
def compute_mac(canonical, secret) -> bytes            # hmac.new(secret, canonical, sha256).digest()
# The five auth-refusal reasons are module-level constants (the REASON_* convention), used as both the
# AuthResult.reason and the op-audit reason: REASON_MALFORMED="l8_malformed", REASON_BAD_CHAT="l8_bad_chat",
# REASON_UNKNOWN_CMD="l8_unknown_cmd", REASON_BAD_SIG="l8_bad_sig", REASON_REPLAY="l8_replay".

# ers/telegram.py  (NET-NEW — the structurally-bounded L8 surface)
class TelegramController:
    def __init__(self, controller, store, transport, auth, *, notifier=None,
                 alerts_down_threshold=3): ...         # controller/transport name-mangled: __ctl/__transport/__store/__auth
    def drain(self) -> None                            # poll -> authenticate -> apply (the six-verb map) -> audit
    def notify(self, text) -> None                     # best-effort send; increments the consecutive-failure counter;
                                                       # threshold crossed -> __ctl.set_state(HALTED, REASON_L8_ALERTS_DOWN)
#   NO place/sign/open path; public surface == {drain, notify}; not callable; not a SafetyController subclass.

# ers/safety.py  (EXTENDED additively — 4 new REASON_* constants)
REASON_L8_RESUME = "l8_resume"; REASON_L8_LOWER_CAPS = "l8_lower_caps"
REASON_L8_BLACKLIST = "l8_blacklist"; REASON_L8_ALERTS_DOWN = "l8_alerts_down"

# ers/intent_store.py  (EXTENDED additively — one new append-only table, op_audit-style)
# CREATE TABLE IF NOT EXISTS blacklist (
#     bl_id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
#     target_kind TEXT NOT NULL, target_value TEXT NOT NULL )   # kind in {wallet, market, source}
def record_blacklist(self, *, target_kind, target_value): ...   # at = stamper.stamp(); commit per write
def blacklist_log(self): ...   # ORDER BY bl_id -> [{"at","target_kind","target_value"}]
#   record_op_event docstring kind-set grows: + l8_command, l8_refused, l8_blacklist.

# ers/controller.py  (EXTENDED additively)
class ERSController:
    def __init__(self, *, ..., anomaly=None, lossbreakers=None, telegram=None, clock): ...
    # run_cycle: FIRST step -> if self._telegram is not None: self._telegram.drain()
    #            then beat -> anomaly -> loss -> process_pending  (all unchanged); telegram=None == today.
```

**op_audit:** the drain writes `kind="l8_command"` (applied) and `kind="l8_refused"` (rejected) rows; the state
transition itself is audited by `set_state`/`swap_caps` as today. **UNCHANGED:** `process_pending`'s signature/
decision flow, `evaluate_intent`/the validator, `propose_trade`, `set_state`/`swap_caps`/`verdict` internals,
`MonotonicStamper`, heartbeat/supervisor, breaker/anomaly/lossbreaker/ramp/flow.

## 5. New `RiskCaps` fields

**None.** LOWER_CAPS reuses the S4.7 `step_weekly` factory + the tighten-only `swap_caps` guard. The
`alerts_down_threshold` is a `TelegramController` constructor default (an alerting concern, deploy-tunable), not
a signed risk cap.

## 6. Safety invariants

1. **Structurally no open-trade verb.** `TelegramController` public surface == `{drain, notify}`; no
   `place`/`sign`/`submit`/`propose_trade`/`open`/`place_gtd_bracket` attr (bare + underscore), not callable, not
   a `SafetyController` subclass, `__ctl` only under name-mangling; the module source never references a trade
   verb. `_COMMAND_SET` is EXACTLY the six safety-increasing verbs. A compromised channel can at worst STOP the
   bot (mutation targets mirror `test_ers_facade.py`).
2. **Allowlist-first, fail-closed, in order.** The five gates run in the pinned order; any refusal audits + drops
   + continues; an unknown chat-id/command/bad-sig/replay never mutates op-state. `compare_digest` is
   constant-time.
3. **No new auto-resume, one operator path.** RESUME is the ONLY operator HALTED→RUNNING (Fork 1); the automatic
   one remains the boot-reconcile. `telegram.py`/`telegram_auth.py` reaching `set_state(RUNNING)` is legitimate
   (this IS the operator authority path) — but the drain applies it ONLY via an authenticated RESUME command.
4. **Loop never blocks / never crashes on Telegram.** `poll()` non-blocking; per-message isolation; `notify()`
   best-effort (never raises into the loop); a raising `send` counts toward alerts-down, not a loop crash.
5. **Alerts-down fails safe.** Persistent notify failure → sticky `HALTED(l8_alerts_down)` (a success resets the
   counter before the threshold).
6. **Serial-runloop only.** `drain()` runs inside `run_cycle`; `swap_caps`/`set_state` never touched off-thread.
7. **Dominance.** The drain is `run_cycle`'s FIRST step — a KILL halts before the anomaly/loss/process_pending
   consults run this cycle (the HALTED verdict then blocks everything). `telegram=None` == pre-S4.6 byte-for-byte.
8. **Untrusted input.** Every id/command field is `neutralize()`d; malformed structure fails loud; the message is
   data, never instructions.

## 7. Built-now vs deferred

| Capability | Built now (fake transport / seams) | Deferred (why safe) |
|---|---|---|
| CommandAuth (allowlist+cmd-set+HMAC+nonce) | ✅ full, fail-closed, constant-time | the real secret VALUE + rotation cadence (deploy-config, off-repo; `rotate()` seam built) |
| TelegramController + structural bounding | ✅ full + sweep test | — |
| The six-verb command map | ✅ KILL/PAUSE/RESUME/FLATTEN/LOWER_CAPS/BLACKLIST | — |
| notify() + alerts-down halt | ✅ best-effort + counter + halt | the heartbeat-stop tie-in to the out-of-band dead-man (deploy) |
| BLACKLIST durable set | ✅ table + record/read + audit | ERS-side enforcement in the sizing path (FOLLOW/detector consumer hard-off — Fork 2) |
| Transport | ✅ fake (Protocol) | real Telegram bot send/recv (deploy) |
| RESUME | ✅ operator-trusted (Fork 1) | reconcile-gated RESUME (S9 — reconciler not in runloop; DORMANT in shadow) |
| Nonce | ✅ in-session monotonic (Fork 3) | durable nonce table (rotation + safety-increasing-only make replay low-stakes) |
| LOWER_CAPS magnitude | ✅ step_weekly (deepest safe tighten) | per-field operator caps (a reviewed arbitrary-caps spec surface) |

## 8. Acceptance criteria

1. Full suite green; the existing **763 stay green** (`telegram=None` == today; additive tables/constants).
2. New TDD tests (RED→GREEN observed) incl. at minimum: each auth gate's refuse/accept pair (bad chat-id,
   unknown/open command, bad sig, replayed nonce, malformed field) — refuse audits + drops + does NOT mutate
   op-state; a fully-authenticated command of each verb applies the exact primitive with the exact reason + audit
   row; constant-time compare used (`compare_digest`); the monotonic nonce boundary (equal nonce rejects,
   strictly-greater accepts, per chat-id independent); RESUME lifts PAUSED and HALTED; LOWER_CAPS tightens to
   per_trade 6 via swap_caps (idempotent second call no-ops); BLACKLIST appends + reads back + audits, unknown
   target_kind rejected; the structural-sweep (no open-trade verb, public surface == {drain, notify}, name-mangled
   ctl) + the `_COMMAND_SET` == six-verbs pin + the source-scan (no place/sign/propose_trade); notify best-effort
   (a raising/False send never raises into the caller) + the alerts-down threshold crossing → HALTED + success
   resets; the drain-at-top dominance (a KILL message halts before the anomaly/loss consults run); `telegram=None`
   inert; reason-constant existence.
3. **The e2e:** a RUNNING loop with a wired `TelegramController` over a fake transport: an authenticated KILL
   halts the loop at the top of the cycle before any intent is processed; a forged-sig / wrong-chat / replayed
   command is refused (audited, op-state unchanged, still RUNNING); an authenticated RESUME lifts the halt; an
   authenticated LOWER_CAPS tightens the active caps that the same cycle's process_pending then sizes against;
   `alerts_down_threshold` consecutive notify failures halt the loop.
4. Two-stage review per sub-slice (spec-compliance + pinned-opus with mutation batteries; pycache sweep after
   each mutation revert); re-review after any safety-critical fix; final whole-slice review.
5. HANDOFF/memory/POL-6 updated; branch `pol-6-s4.6-telegram`; merge `--no-ff` with verification status;
   **confirm before push**. This CLOSES the S4 safety envelope.

## 9. Sub-slice decomposition (build order)

| # | Sub-slice | Contents |
|---|---|---|
| S4.6a | **The auth core** | `ers/telegram_auth.py` — `RawMessage`/`AuthResult`/`TelegramTransport` Protocol/`SecretHolder` + `CommandAuth` (the 5 fail-closed gates in order, constant-time HMAC, monotonic nonce) + `_COMMAND_SET`/`canonical_message`/`compute_mac` + `neutralize` wiring + the 4 new `REASON_L8_*` constants. Pure unit; every refuse/accept boundary pair. |
| S4.6b | **The bounded controller** | `ers/telegram.py` — `TelegramController` (name-mangled ctl/transport/store/auth) + the six-verb `__apply` map (LOWER_CAPS→step_weekly) + `drain()` (poll→auth→apply→audit, per-message isolation) + the structural-sweep + `_COMMAND_SET`-pin + source-scan tests. |
| S4.6c | **notify + alerts-down** | `notify()` best-effort (never raises into the loop) + the consecutive-failure counter + the `alerts_down_threshold`→`HALTED(l8_alerts_down)` halt + success-resets + loop-never-blocks pins. |
| S4.6d | **BLACKLIST + the seam + e2e** | the `blacklist` durable table + `record_blacklist`/`blacklist_log` (additive on `IntentStore`) + the BLACKLIST command wiring + unknown-kind reject + the `ERSController(telegram=)` seam draining at the TOP of `run_cycle` + `telegram=None`==today + the §8.3 whole-slice e2e. |

Each sub-slice: strict TDD (observe the RED), then the two-stage review, serial on `pol-6-s4.6-telegram`.
