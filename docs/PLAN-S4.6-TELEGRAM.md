# S4.6 L8 TelegramController Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build S4.6 / POL-6 — the L8 remote authenticated safety-control channel: a pure `CommandAuth` (allowlist-first chat-id → six-verb command set → constant-time HMAC → monotonic nonce), a structurally-bounded `TelegramController` (safety-increasing-only, no open-trade verb), `notify()` best-effort with an alerts-down→HALT fail-safe, a durable BLACKLIST set, and the `ERSController(telegram=)` seam draining at the top of `run_cycle` — all shadow-only over a fake transport. **This closes the S4 safety envelope.**

**Architecture:** NEW `src/polybot/ers/telegram_auth.py` (the pure auth core + transport Protocol + shapes) + `src/polybot/ers/telegram.py` (the bounded controller + notify) + additive extensions: 4 new `REASON_L8_*` constants, one new `blacklist` IntentStore table, the `ERSController(telegram=None)` seam. The authoritative spec is `docs/DESIGN-S4.6-TELEGRAM.md` (§4 = the pinned contract); this plan implements exactly that. **No new RiskCaps fields.**

**Tech Stack:** Python 3.13, pytest, stdlib only (`hmac`/`hashlib`/`dataclasses`) — no new dependencies; reuses `ingestion/sanitizer.neutralize` and `ers/ramp.step_weekly`.

---

## Execution notes (READ FIRST — every implementer)

- **Environment:** repo is WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot`, branch `pol-6-s4.6-telegram` (already checked out). Run tests/git from Windows via `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`; edit files via UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...` (EISDIR = 9p glitch, retry). Tests: `./.venv/bin/pytest -o addopts="" -q` — baseline **763 passing** before Task A1.
- **Strict TDD:** run each Step 2 and OBSERVE the RED (fail for the stated reason) before writing Step 3. One commit per RED→GREEN cycle. **Commit messages: single `-m`, NO Co-Authored-By trailer.** If you mutation-check anything, revert with `git checkout` AND sweep pycache (`find src -name __pycache__ -exec rm -rf {} +`) — a stale .pyc once masqueraded as a source regression. A handful of A-tasks pin an EXISTING branch and are green-on-arrival; those note the mutation to apply/revert to confirm the pin bites (observe that RED explicitly).
- **SACRED — never touch:** `validator.py`/`evaluate_intent`, `propose_trade`/`record_decision`/`record_op_event` bodies in `intent_store.py` (ADDING the `blacklist` table + its two methods in S4.6d is in-scope; the `record_op_event` DOCSTRING kind-set may grow), `process_pending`'s signature + decision flow in `service.py`, `set_state`/`swap_caps`/`verdict` BODIES in `safety.py` (ADDING `REASON_*` constants is in-scope), `core/clock.py`, `heartbeat.py`, `supervisor.py`, `breaker.py`, `anomaly.py`, `ramp.py`, `flow.py`, `lossbreaker.py`, `caps.py`, `sanitizer.py`.
- **Sub-slices run SERIALLY A → B → C → D** on the shared branch. B stubs the BLACKLIST verb as `NotImplementedError`; D replaces it. C appends `notify()` tests to the same `tests/test_ers_telegram.py` B created. When a Step-3 code block shows surrounding code that has since evolved, reconcile against the CURRENT file and NEVER delete an earlier sub-slice's additions. If reconciliation is ambiguous, STOP and report NEEDS_CONTEXT.
- **Suite counts are per-sub-slice estimates.** Authoritative verification: the named new tests pass, the FULL suite is all green (exit 0), no test deleted/skipped. If an absolute count differs but everything is green, proceed and note it.
- **Fail-closed doctrine:** the inbound message is UNTRUSTED DATA, never instructions. Under ambiguity the correct behavior is REFUSE + audit + do not mutate op-state. Re-read `docs/DESIGN-S4.6-TELEGRAM.md` §3/§6 before asking.

---

I now have every symbol verified. Confirming: `MonotonicStamper` is in `polybot.core.clock`; `neutralize` is in `polybot.ingestion.sanitizer` (signature `neutralize(text, marker=...)`, strips Cc/Cf incl. `\n`/`\t`, `.strip()`s, deterministic). `SafetyController` has `set_state`/`active_caps`/`state`. Existing S4.1 reason constants (`REASON_L8_KILL`/`REASON_L8_PAUSED`/`REASON_OP_FLATTEN`) live in safety.py. `RiskCaps` default `per_trade=Decimal("12")`, `step_weekly` â†’ `per_trade` min 6. No existing telegram_auth test file.

One detail to lock: the contract says gate 4 canonical is `canonical_message`-over-neutralized-fields but `canonical_message(raw)` takes a `raw` and encodes its fields. Since `authenticate` neutralizes into `nc_chat/nc_cmd/nc_nonce` and uses raw payload, it must build a `RawMessage` with neutralized plumbing fields + raw payload + raw sig to feed `canonical_message`. This is the "canonical over the NEUTRALIZED chat_id/command/nonce + raw payload" note. My tasks will pin exactly that.

Now writing the S4.6a plan fragment.

## Sub-slice S4.6a: The auth core (`ers/telegram_auth.py` + the 4 new `REASON_L8_*` in `safety.py`)

All tests live in the NEW file `tests/test_ers_telegram_auth.py`. Baseline before S4.6 = 763 passing. Branch `pol-6-s4.6-telegram` (checked out). Module-level test helpers are copied into every test that needs them (no conftest/fixtures beyond `tmp_path`/`monkeypatch`, no test classes). Commands (run from the repo root in WSL):
- Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram_auth.py -o addopts="" -q'`
- Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'`

> **Ordering note for the implementer:** each task's Step-3 code is the *cumulative* state of `src/polybot/ers/telegram_auth.py` after that task (later tasks add to the module; the shown block is the whole file at that point). Steps 1â€“2 (RED) must be observed against the module as it stood *before* this task. Every task ends with the task file green AND the full suite green.

---

### Task A1: The four new `REASON_L8_*` constants in `safety.py`
**Files:** Modify `src/polybot/ers/safety.py` (add after the S4.7 reason-code block ending line 56, i.e. after `REASON_FLOW_DATA_ERROR = "flow_data_error"`) Â· Test `tests/test_ers_telegram_auth.py` (new)

- [ ] **Step 1: Write the failing test** (complete python code â€” this is the first content of the new test file)
```python
"""S4.6a â€” the L8 auth core (telegram_auth.py) + the new safety.py reason constants."""
from polybot.ers import safety as _safety


def test_new_l8_reason_constants_exist_with_exact_values():
    # Kills: mutation deleting/renaming any of the 4 NEW S4.6 reason constants, or drifting a value.
    assert _safety.REASON_L8_RESUME == "l8_resume"
    assert _safety.REASON_L8_LOWER_CAPS == "l8_lower_caps"
    assert _safety.REASON_L8_BLACKLIST == "l8_blacklist"
    assert _safety.REASON_L8_ALERTS_DOWN == "l8_alerts_down"


def test_preexisting_l8_reason_constants_unchanged():
    # Kills: mutation that accidentally edits the S4.1 constants while adding the new ones.
    assert _safety.REASON_L8_KILL == "l8_kill"
    assert _safety.REASON_L8_PAUSED == "l8_paused"
    assert _safety.REASON_OP_FLATTEN == "op_flatten"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram_auth.py -o addopts="" -q'` â†’ `test_new_l8_reason_constants_exist_with_exact_values` FAILS with `AttributeError: module 'polybot.ers.safety' has no attribute 'REASON_L8_RESUME'`. (`test_preexisting_...` passes â€” those already exist.)

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/safety.py`, immediately after line 56 (`REASON_FLOW_DATA_ERROR = "flow_data_error"`), add:
```python
# --- S4.6 reason codes (NET-NEW; the L8 TelegramController vocabulary) ------------------------
REASON_L8_RESUME = "l8_resume"              # operator RESUME: PAUSED or HALTED -> RUNNING (Fork 1)
REASON_L8_LOWER_CAPS = "l8_lower_caps"      # operator LOWER_CAPS: swap_caps(step_weekly(active_caps))
REASON_L8_BLACKLIST = "l8_blacklist"        # operator BLACKLIST: durable (target_kind, target_value)
REASON_L8_ALERTS_DOWN = "l8_alerts_down"    # notify() persistent failure -> fail-safe HALT
```

- [ ] **Step 4: Task file green + FULL suite green** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram_auth.py -o addopts="" -q'` (2 passing) then `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` (765 passing = 763 + 2).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: add the four new REASON_L8_* constants in safety.py"'`

---

### Task A2: `RawMessage` + `AuthResult` frozen dataclasses
**Files:** Create `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append to the test file)
```python
import dataclasses

import pytest

from polybot.ers.telegram_auth import RawMessage, AuthResult


def test_rawmessage_is_frozen_dataclass_with_exact_fields():
    # Kills: mutation making RawMessage mutable, or dropping/renaming a field, or reordering sig off bytes.
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=b"\x00\x01")
    assert dataclasses.is_dataclass(raw)
    assert raw.chat_id == "c1" and raw.command == "KILL" and raw.payload == ""
    assert raw.nonce == "1" and raw.sig == b"\x00\x01"
    names = [f.name for f in dataclasses.fields(raw)]
    assert names == ["chat_id", "command", "payload", "nonce", "sig"]


def test_rawmessage_is_immutable():
    # Kills: mutation dropping frozen=True (an untrusted inbound record must not be mutable in place).
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=b"")
    with pytest.raises(dataclasses.FrozenInstanceError):
        raw.command = "RESUME"


def test_authresult_defaults_and_fields():
    # Kills: mutation changing AuthResult field defaults (command/payload=None, chat_id="") or their order.
    r = AuthResult(True, "ok")
    assert r.ok is True and r.reason == "ok"
    assert r.command is None and r.payload is None and r.chat_id == ""
    names = [f.name for f in dataclasses.fields(r)]
    assert names == ["ok", "reason", "command", "payload", "chat_id"]


def test_authresult_is_immutable():
    # Kills: mutation dropping frozen=True on AuthResult.
    r = AuthResult(False, "l8_bad_sig", chat_id="c1")
    assert r.chat_id == "c1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = True
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram_auth.py -o addopts="" -q'` â†’ the 4 new tests FAIL at import with `ModuleNotFoundError: No module named 'polybot.ers.telegram_auth'`.

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/telegram_auth.py` with (cumulative file after A2):
```python
"""L8 authenticated-command core (S4.6a / POL-6).

The PURE auth boundary for the remote safety-control channel. An inbound Telegram
message is UNTRUSTED DATA, never instructions: every plumbing field (chat_id /
command / nonce) is neutralize()d (strip Cc/Cf control/bidi/zero-width), then the
FIVE fail-closed gates run IN ORDER -- allowlisted chat-id FIRST, then the
safety-increasing-only command set, then a constant-time HMAC-SHA256 over the
current rotating secret, then a monotonic per-chat-id nonce. Only a message that
passes all five authenticates; every refusal reports the FIRST failing gate.

Nothing here can OPEN a trade -- the command set is EXACTLY the six safety-
increasing verbs, structurally pinned. A compromised channel can at worst STOP
the bot.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from polybot.ingestion.sanitizer import neutralize


@dataclass(frozen=True)
class RawMessage:
    """The untrusted inbound message as received (strings/bytes, un-neutralized)."""
    chat_id: str
    command: str
    payload: str
    nonce: str
    sig: bytes


@dataclass(frozen=True)
class AuthResult:
    """The verdict of CommandAuth.authenticate. On ok=True, command/payload/chat_id
    carry the NEUTRALIZED values the controller applies; on ok=False, reason is the
    first-failing-gate REASON_* and chat_id is the neutralized id where known."""
    ok: bool
    reason: str
    command: str | None = None
    payload: str | None = None
    chat_id: str = ""
```

- [ ] **Step 4: Task file green + FULL suite green** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram_auth.py -o addopts="" -q'` (6 passing) then full suite `--tb=no` (769 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: RawMessage + AuthResult frozen dataclasses"'`

---

### Task A3: `TelegramTransport` Protocol (`@runtime_checkable`, duck-typed `isinstance`)
**Files:** Modify `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append)
```python
from polybot.ers.telegram_auth import TelegramTransport


def test_transport_protocol_is_runtime_checkable_on_duck_typed_fake():
    # Kills: mutation dropping @runtime_checkable (isinstance against a Protocol would raise TypeError).
    class _FakeTransport:
        def poll(self):
            return []

        def send(self, text):
            return True

    assert isinstance(_FakeTransport(), TelegramTransport)


def test_transport_protocol_rejects_object_missing_send():
    # Kills: mutation renaming/removing `send` from the Protocol (an incomplete transport would pass).
    class _PollOnly:
        def poll(self):
            return []

    assert not isinstance(_PollOnly(), TelegramTransport)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ the 2 new tests FAIL at import with `ImportError: cannot import name 'TelegramTransport' from 'polybot.ers.telegram_auth'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/telegram_auth.py` (after `AuthResult`):
```python
@runtime_checkable
class TelegramTransport(Protocol):
    """Non-blocking inbound/outbound seam (the real Telegram bot is deploy-deferred; the
    tests inject a fake). poll() returns pending RawMessages ([] when none); send() is
    best-effort (True on success)."""

    def poll(self) -> list: ...

    def send(self, text) -> bool: ...
```

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (8 passing) then full suite (771 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: TelegramTransport runtime_checkable Protocol"'`

---

### Task A4: `SecretHolder` (`current` / `rotate`)
**Files:** Modify `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append)
```python
from polybot.ers.telegram_auth import SecretHolder


def test_secret_holder_current_returns_initial_secret():
    # Kills: mutation making current() return a constant / the wrong field.
    holder = SecretHolder(b"seed-secret")
    assert holder.current() == b"seed-secret"


def test_secret_holder_rotate_swaps_current():
    # Kills: mutation making rotate() a no-op (cross-restart-replay defense relies on the swap).
    holder = SecretHolder(b"old")
    assert holder.current() == b"old"
    holder.rotate(b"new")
    assert holder.current() == b"new"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ the 2 new tests FAIL at import with `ImportError: cannot import name 'SecretHolder' from 'polybot.ers.telegram_auth'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/telegram_auth.py`:
```python
class SecretHolder:
    """Holds the CURRENT rotating HMAC secret. The value lives off-repo (deploy-config);
    this is the verify-side holder + the rotate() seam. rotate() is the per-restart +
    operator-triggered swap (cadence is deploy-config, not a code constant)."""

    def __init__(self, secret: bytes):
        self._secret = secret

    def current(self) -> bytes:
        return self._secret

    def rotate(self, new_secret: bytes) -> None:
        self._secret = new_secret
```

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (10 passing) then full suite (773 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: SecretHolder current/rotate"'`

---

### Task A5: `_COMMAND_SET` frozenset â€” the exact six-verb pin + the five refusal-reason constants
**Files:** Modify `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append)
```python
from polybot.ers import telegram_auth as _auth


def test_command_set_is_exactly_the_six_safety_verbs():
    # Kills: mutation adding an OPEN/PLACE verb to the command set, or dropping a safety verb.
    assert _auth._COMMAND_SET == frozenset(
        {"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"}
    )


def test_command_set_is_a_frozenset():
    # Kills: mutation making the command set a mutable set (a runtime .add of "OPEN" would then be possible).
    assert isinstance(_auth._COMMAND_SET, frozenset)


def test_command_set_excludes_open_trade_verbs():
    # Kills: mutation that widens the set; an explicit pin that no trade verb is dispatchable.
    for forbidden in ("OPEN", "PLACE", "OPEN_TRADE", "SIGN", "SUBMIT", "BUY", "SELL"):
        assert forbidden not in _auth._COMMAND_SET


def test_refusal_reason_constants_exact_values():
    # Kills: mutation drifting any of the five auth-refusal reason strings.
    assert _auth.REASON_MALFORMED == "l8_malformed"
    assert _auth.REASON_BAD_CHAT == "l8_bad_chat"
    assert _auth.REASON_UNKNOWN_CMD == "l8_unknown_cmd"
    assert _auth.REASON_BAD_SIG == "l8_bad_sig"
    assert _auth.REASON_REPLAY == "l8_replay"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ `test_command_set_is_exactly_the_six_safety_verbs` FAILS with `AttributeError: module 'polybot.ers.telegram_auth' has no attribute '_COMMAND_SET'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/telegram_auth.py` (place these module-level after `SecretHolder`; the `_COMMAND_SET` and reason constants must exist before `CommandAuth` in A8):
```python
# The safety-increasing-ONLY command set -- structurally pinned to EXACTLY these six verbs.
# There is deliberately NO open-trade verb: a compromised channel can at worst STOP the bot.
_COMMAND_SET = frozenset({"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"})

# The five auth-refusal reasons -- used both as AuthResult.reason and the op-audit reason.
REASON_MALFORMED = "l8_malformed"      # gate 1: empty/absent field OR non-base-10 nonce
REASON_BAD_CHAT = "l8_bad_chat"        # gate 2: chat-id not in the operator allowlist
REASON_UNKNOWN_CMD = "l8_unknown_cmd"  # gate 3: command not in _COMMAND_SET
REASON_BAD_SIG = "l8_bad_sig"          # gate 4: HMAC mismatch (constant-time compare)
REASON_REPLAY = "l8_replay"            # gate 5: nonce <= last-seen for this chat-id
```

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (14 passing) then full suite (777 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: _COMMAND_SET six-verb pin + five refusal-reason constants"'`

---

### Task A6: `canonical_message` â€” fixed field order, `|` separator, `.encode()` each field
**Files:** Modify `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append)
```python
from polybot.ers.telegram_auth import canonical_message


def test_canonical_message_is_pipe_joined_fixed_order():
    # Kills: mutation reordering fields, changing the separator, or dropping the payload from the MAC input.
    raw = RawMessage(chat_id="c1", command="KILL", payload="p", nonce="7", sig=b"ignored")
    assert canonical_message(raw) == b"c1|KILL|p|7"


def test_canonical_message_encodes_each_field_and_omits_sig():
    # Kills: mutation that folds sig into the canonical bytes, or str()s the whole tuple instead of encoding fields.
    raw = RawMessage(chat_id="chat", command="RESUME", payload="", nonce="42", sig=b"\xff\xff")
    canonical = canonical_message(raw)
    assert canonical == b"chat|RESUME||42"     # empty payload -> two adjacent separators
    assert b"\xff" not in canonical            # the signature is NEVER part of its own input


def test_canonical_message_order_is_chat_command_payload_nonce_not_permuted():
    # Kills: mutation swapping command<->payload or nonce<->payload (a transposition would still be "|"-joined).
    raw = RawMessage(chat_id="A", command="B", payload="C", nonce="9", sig=b"")
    assert canonical_message(raw) == b"A|B|C|9"
    assert canonical_message(raw) != b"A|C|B|9"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ the 3 new tests FAIL at import with `ImportError: cannot import name 'canonical_message'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/telegram_auth.py`:
```python
def canonical_message(raw) -> bytes:
    """The HMAC input: b"chat_id|command|payload|nonce" -- fixed field order, "|" separator,
    each field .encode()d. The signature is NEVER part of its own input. The caller feeds a
    RawMessage whose chat_id/command/nonce are already NEUTRALIZED (payload as received)."""
    return b"|".join(
        (
            raw.chat_id.encode(),
            raw.command.encode(),
            raw.payload.encode(),
            raw.nonce.encode(),
        )
    )
```

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (17 passing) then full suite (780 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: canonical_message fixed-order pipe-joined HMAC input"'`

---

### Task A7: `compute_mac` â€” HMAC-SHA256 digest (determinism + secret-dependence)
**Files:** Modify `src/polybot/ers/telegram_auth.py` Â· Test `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append)
```python
import hashlib as _hashlib
import hmac as _hmac

from polybot.ers.telegram_auth import compute_mac


def test_compute_mac_matches_stdlib_hmac_sha256_digest():
    # Kills: mutation swapping the hash to md5/sha1, or returning hexdigest() instead of digest().
    canonical = b"c1|KILL|p|7"
    secret = b"s3cr3t"
    expected = _hmac.new(secret, canonical, _hashlib.sha256).digest()
    assert compute_mac(canonical, secret) == expected


def test_compute_mac_is_deterministic():
    # Kills: mutation introducing per-call salt/nonce into the MAC (verify would never match).
    assert compute_mac(b"m", b"k") == compute_mac(b"m", b"k")


def test_compute_mac_depends_on_secret():
    # Kills: mutation ignoring the secret arg (all messages would share one MAC -> forgeable).
    assert compute_mac(b"m", b"k1") != compute_mac(b"m", b"k2")


def test_compute_mac_depends_on_message():
    # Kills: mutation ignoring the canonical arg (any message would verify under a known MAC).
    assert compute_mac(b"m1", b"k") != compute_mac(b"m2", b"k")
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ the 4 new tests FAIL at import with `ImportError: cannot import name 'compute_mac'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/telegram_auth.py`:
```python
def compute_mac(canonical: bytes, secret: bytes) -> bytes:
    """HMAC-SHA256(secret, canonical) raw digest bytes (compared constant-time in gate 4)."""
    return hmac.new(secret, canonical, hashlib.sha256).digest()
```

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (21 passing) then full suite (784 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: compute_mac HMAC-SHA256 digest"'`

---

### Task A8: `CommandAuth` gate 1 â€” structure (malformed field / non-int nonce vs clean accept-shape)
**Files:** Modify `src/polybot/ers/telegram_auth.py` (add `CommandAuth` class) Â· Test `tests/test_ers_telegram_auth.py`

> This task introduces `CommandAuth` with **all five gates implemented at once** (they are one tightly-coupled `authenticate` method that can't be built a fragment at a time without dead intermediate states). Tasks A8â€“A13 each pin a *different behavioural facet* of that method with its own refuse/accept pair; the Step-3 code is repeated in full only in A8 (the birth of the class), and A9â€“A13 add **no** production code â€” their Step 2 already passes against the A8 body, so those tasks are pure characterization pins. To keep strict REDâ†’GREEN, A9â€“A13 are written so their Step-1 test FAILS if the corresponding gate/branch is mutated out; the implementer confirms RED by temporarily reverting the specific line named in each `# Kills:` comment (documented per task), then GREEN with it restored. (This is the standard "pin an existing branch" pattern; no TBD.)

A shared, copied-per-test helper block (`_holder`, `_auth_obj`, `_signed`) is defined at the top of each gate test in A8â€“A13.

- [ ] **Step 1: Write the failing test** (append â€” includes the helper block used by A8â€“A13)
```python
from polybot.ers.telegram_auth import CommandAuth, compute_mac, canonical_message, RawMessage, SecretHolder
from polybot.ers import telegram_auth as _auth


# --- helpers (copied verbatim into A8..A13 gate tests) ---------------------------------------
def _holder(secret=b"unit-secret"):
    return SecretHolder(secret)


def _auth_obj(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=_holder(secret))


def _signed(chat_id, command, payload, nonce, secret=b"unit-secret"):
    """Build a RawMessage with a VALID signature over the NEUTRALIZED plumbing fields + raw
    payload (mirrors CommandAuth's gate-4 canonical construction)."""
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate1_malformed_empty_chat_id_refuses(tmp_path):
    # Kills: mutation removing the empty-nc_chat structure check (an empty id must never reach the allowlist).
    auth = _auth_obj()
    raw = _signed("", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_MALFORMED


def test_gate1_malformed_non_integer_nonce_refuses(tmp_path):
    # Kills: mutation removing the base-10-int nonce check (a non-numeric nonce would crash gate 5's int()).
    auth = _auth_obj()
    raw = _signed("c1", "KILL", "", "not-a-number")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_MALFORMED


def test_gate1_accept_shape_wellformed_message_passes_structure(tmp_path):
    # Kills: over-tight gate 1 that rejects a legitimate well-formed message. Boundary PAIR to the two refuses.
    auth = _auth_obj()
    raw = _signed("c1", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.reason == "ok"
    assert result.command == "KILL" and result.chat_id == "c1"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file â†’ the 3 new tests FAIL at import with `ImportError: cannot import name 'CommandAuth'`.

- [ ] **Step 3: Minimal implementation** â€” append the full `CommandAuth` class to `src/polybot/ers/telegram_auth.py`:
```python
class CommandAuth:
    """The five fail-closed gates (Â§3), IN ORDER. Every plumbing field is neutralize()d;
    a refusal reports the FIRST failing gate. State: a per-chat-id in-memory monotonic
    nonce dict (in-session; per-restart secret rotation defeats cross-restart replay)."""

    def __init__(self, *, allowlist, secret_holder, command_set=_COMMAND_SET):
        self._allow = allowlist                # {chat_id: role}; operator-curated, injected
        self._secret_holder = secret_holder
        self._command_set = command_set
        self._seen = {}                        # {chat_id: last_nonce_int}

    def authenticate(self, raw) -> AuthResult:
        # Gate 1 -- structure. Neutralize the plumbing fields; the signature stays raw bytes.
        nc_chat = neutralize(raw.chat_id)
        nc_cmd = neutralize(raw.command)
        nc_nonce = neutralize(raw.nonce)
        nc_payload = neutralize(raw.payload)
        if not nc_chat or not nc_cmd or not nc_nonce or not nc_nonce.isdigit():
            return AuthResult(False, REASON_MALFORMED)
        # Gate 2 -- chat-id allowlist (the FIRST semantic check).
        if self._allow.get(nc_chat) is None:
            return AuthResult(False, REASON_BAD_CHAT, chat_id=nc_chat)
        # Gate 3 -- command set (structural: no open verb dispatches).
        if nc_cmd not in self._command_set:
            return AuthResult(False, REASON_UNKNOWN_CMD, chat_id=nc_chat)
        # Gate 4 -- HMAC over the NEUTRALIZED plumbing fields + raw payload; constant-time.
        canonical = canonical_message(
            RawMessage(chat_id=nc_chat, command=nc_cmd, payload=raw.payload, nonce=nc_nonce, sig=b"")
        )
        mac = compute_mac(canonical, self._secret_holder.current())
        if not hmac.compare_digest(mac, raw.sig):
            return AuthResult(False, REASON_BAD_SIG, chat_id=nc_chat)
        # Gate 5 -- monotonic per-chat-id nonce.
        n = int(nc_nonce)
        if n <= self._seen.get(nc_chat, -1):
            return AuthResult(False, REASON_REPLAY, chat_id=nc_chat)
        self._seen[nc_chat] = n
        return AuthResult(True, "ok", command=nc_cmd, payload=nc_payload, chat_id=nc_chat)
```
> Note on the accept-shape test: `_signed("c1","KILL","","1")` signs over `neutralize("")==""` payload; `authenticate` builds gate-4 canonical with `payload=raw.payload` (also `""`), so the two canonicals match and gate 4 passes. The pin holds because `neutralize("")==""` (no control chars). Any payload carrying control chars would need the `_signed` helper's `neutralize(payload)` to be dropped â€” see A11/A12 for the sig-mismatch pins.

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (24 passing) then full suite (787 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram_auth.py tests/test_ers_telegram_auth.py && git commit -m "S4.6a: CommandAuth + gate 1 structure (malformed field / non-int nonce)"'`

---

### Task A9: Gate 2 â€” chat-id allowlist (refuse `l8_bad_chat` vs accept in-list)
**Files:** Test-only `tests/test_ers_telegram_auth.py` (pins the gate-2 branch in `authenticate`)

- [ ] **Step 1: Write the failing test** (append â€” re-declares the helper block)
```python
def _holder_g2(secret=b"unit-secret"):
    return SecretHolder(secret)


def _auth_obj_g2(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=_holder_g2(secret))


def _signed_g2(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate2_unknown_chat_id_refuses_bad_chat(tmp_path):
    # Kills: mutation deleting the allowlist gate (`self._allow.get(...) is None`), which would let an
    # un-allowlisted chat reach the HMAC gate. RED if gate 2 is removed: reason becomes l8_bad_sig, not l8_bad_chat.
    auth = _auth_obj_g2(allowlist={"c1": "operator"})
    raw = _signed_g2("intruder", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_BAD_CHAT
    assert result.chat_id == "intruder"


def test_gate2_allowlisted_chat_id_passes_gate2(tmp_path):
    # Kills: over-tight gate 2 that rejects an allowlisted id. Accept half of the pair.
    auth = _auth_obj_g2(allowlist={"c1": "operator"})
    raw = _signed_g2("c1", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.chat_id == "c1"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” task file: both pass immediately (gate 2 already implemented in A8). To OBSERVE RED, temporarily delete the two gate-2 lines (`if self._allow.get(nc_chat) is None: return AuthResult(False, REASON_BAD_CHAT, chat_id=nc_chat)`) in `authenticate` and rerun â†’ `test_gate2_unknown_chat_id_refuses_bad_chat` FAILS (`result.reason == 'l8_bad_sig'`, expected `'l8_bad_chat'`). Restore the lines. (Documented characterization-of-existing-branch RED.)

- [ ] **Step 3: Minimal implementation** â€” none (gate 2 already lives in `authenticate` from A8; these tests pin its behaviour and ordering).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (26 passing) then full suite (789 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin gate 2 chat-id allowlist refuse/accept"'`

---

### Task A10: Gate 3 â€” command set (refuse unknown/lowercase/OPEN `l8_unknown_cmd` vs accept a known verb)
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _auth_obj_g3(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(secret))


def _signed_g3(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate3_open_trade_verb_refuses_unknown_cmd(tmp_path):
    # Kills: mutation widening dispatch to a non-safety verb. An "OPEN" command must be refused at gate 3.
    auth = _auth_obj_g3()
    raw = _signed_g3("c1", "OPEN", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_UNKNOWN_CMD
    assert result.chat_id == "c1"


def test_gate3_place_verb_refuses_unknown_cmd(tmp_path):
    # Kills: mutation that would let a "PLACE" trade verb through (defense-in-depth with the structural sweep).
    auth = _auth_obj_g3()
    raw = _signed_g3("c1", "PLACE", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_UNKNOWN_CMD


def test_gate3_lowercase_kill_refuses_unknown_cmd(tmp_path):
    # Kills: mutation case-folding the command (the set is case-SENSITIVE; "kill" must not match "KILL").
    auth = _auth_obj_g3()
    raw = _signed_g3("c1", "kill", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_UNKNOWN_CMD


def test_gate3_known_verb_passes_gate3(tmp_path):
    # Kills: over-tight gate 3 that rejects a valid safety verb. Accept half of the pair.
    auth = _auth_obj_g3()
    raw = _signed_g3("c1", "PAUSE", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.command == "PAUSE"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” all pass immediately. RED check: temporarily change gate 3 to `if False:` (disable it) â†’ the three refuse tests FAIL (an OPEN/PLACE/kill message would reach gate 4 and return `l8_bad_sig`). Restore.

- [ ] **Step 3: Minimal implementation** â€” none (gate 3 from A8).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (30 passing) then full suite (793 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin gate 3 command-set (OPEN/PLACE/lowercase refused, known verb accepted)"'`

---

### Task A11: Gate 4 â€” HMAC bad-sig refuse (wrong-secret sig / `b"wrong"`) vs correct-sig accept
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _auth_obj_g4(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(secret))


def _signed_g4(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate4_signature_under_wrong_secret_refuses_bad_sig(tmp_path):
    # Kills: mutation dropping the HMAC gate. A sig computed under a DIFFERENT secret must be refused.
    auth = _auth_obj_g4(secret=b"the-real-secret")
    raw = _signed_g4("c1", "KILL", "", "1", secret=b"attacker-secret")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_BAD_SIG
    assert result.chat_id == "c1"


def test_gate4_garbage_signature_refuses_bad_sig(tmp_path):
    # Kills: mutation that accepts any sig (e.g. `if True:` short-circuit). A junk sig must be refused.
    auth = _auth_obj_g4(secret=b"the-real-secret")
    good = _signed_g4("c1", "KILL", "", "1", secret=b"the-real-secret")
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=b"wrong")
    assert auth.authenticate(raw).reason == _auth.REASON_BAD_SIG
    # sanity: the same message with the RIGHT sig authenticates (fresh auth to avoid nonce state)
    assert _auth_obj_g4(secret=b"the-real-secret").authenticate(good).ok is True


def test_gate4_correct_signature_passes_gate4(tmp_path):
    # Kills: over-tight gate 4 rejecting a correctly-signed message. Accept half of the pair.
    auth = _auth_obj_g4(secret=b"matching")
    raw = _signed_g4("c1", "FLATTEN", "", "1", secret=b"matching")
    result = auth.authenticate(raw)
    assert result.ok is True and result.command == "FLATTEN"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” all pass immediately. RED check: temporarily change gate 4 to skip the check (`if False:`) â†’ both refuse tests FAIL (a wrong-secret/garbage sig would fall through to the nonce gate and authenticate). Restore.

- [ ] **Step 3: Minimal implementation** â€” none (gate 4 from A8).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (33 passing) then full suite (796 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin gate 4 HMAC bad-sig refuse / correct-sig accept"'`

---

### Task A12: Gate 4 constant-time â€” `compare_digest` is used (correct-length-but-wrong sig still refuses)
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _auth_obj_ct(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(secret))


def _signed_ct(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate4_correct_length_but_wrong_sig_refuses(tmp_path):
    # Kills: mutation comparing sig by length or truthiness instead of value; a 32-byte-but-wrong sig must refuse.
    auth = _auth_obj_ct(secret=b"the-real-secret")
    good = _signed_ct("c1", "KILL", "", "1", secret=b"the-real-secret")
    assert len(good.sig) == 32                       # HMAC-SHA256 digest length
    flipped = bytes([good.sig[0] ^ 0x01]) + good.sig[1:]   # same length, one bit flipped
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=flipped)
    assert auth.authenticate(raw).reason == _auth.REASON_BAD_SIG


def test_gate4_uses_compare_digest_not_equality(monkeypatch, tmp_path):
    # Kills: mutation replacing hmac.compare_digest with `==` (drops constant-time comparison).
    # We assert the module-level hmac.compare_digest is the function actually consulted in gate 4:
    # patching it to a sentinel-recording spy must be hit exactly once during a verify.
    import polybot.ers.telegram_auth as mod
    calls = {"n": 0}
    real = mod.hmac.compare_digest

    def _spy(a, b):
        calls["n"] += 1
        return real(a, b)

    monkeypatch.setattr(mod.hmac, "compare_digest", _spy)
    auth = _auth_obj_ct(secret=b"matching")
    raw = _signed_ct("c1", "KILL", "", "1", secret=b"matching")
    auth.authenticate(raw)
    assert calls["n"] == 1
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” both pass immediately. RED check: temporarily replace `hmac.compare_digest(mac, raw.sig)` with `mac == raw.sig` â†’ `test_gate4_uses_compare_digest_not_equality` FAILS (`calls["n"] == 0`). Restore. (`test_gate4_correct_length_but_wrong_sig_refuses` stays green either way â€” it pins the *value* comparison, the spy test pins the *primitive*.)

- [ ] **Step 3: Minimal implementation** â€” none (constant-time compare from A8).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (35 passing) then full suite (798 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin gate 4 constant-time compare_digest usage"'`

---

### Task A13: Gate 5 â€” nonce replay (equal rejects / strictly-greater accepts) + per-chat-id independence
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _auth_obj_g5(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator", "c2": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(secret))


def _signed_g5(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate5_equal_nonce_is_replay_refused(tmp_path):
    # Kills: mutation using `<` instead of `<=` in the replay check (an exact-replay nonce would slip through).
    auth = _auth_obj_g5()
    first = auth.authenticate(_signed_g5("c1", "KILL", "", "5"))
    assert first.ok is True
    second = auth.authenticate(_signed_g5("c1", "KILL", "", "5"))   # same nonce 5
    assert second.ok is False and second.reason == _auth.REASON_REPLAY


def test_gate5_lower_nonce_is_replay_refused(tmp_path):
    # Kills: mutation that records but does not compare (an out-of-order lower nonce would authenticate).
    auth = _auth_obj_g5()
    assert auth.authenticate(_signed_g5("c1", "KILL", "", "10")).ok is True
    down = auth.authenticate(_signed_g5("c1", "KILL", "", "9"))
    assert down.ok is False and down.reason == _auth.REASON_REPLAY


def test_gate5_strictly_greater_nonce_accepts(tmp_path):
    # Kills: over-tight gate 5 rejecting a legitimately-advancing nonce. Boundary PAIR to the equal-refuse.
    auth = _auth_obj_g5()
    assert auth.authenticate(_signed_g5("c1", "KILL", "", "5")).ok is True
    assert auth.authenticate(_signed_g5("c1", "KILL", "", "6")).ok is True


def test_gate5_nonce_is_per_chat_id_independent(tmp_path):
    # Kills: mutation using a single global last-nonce instead of per-chat-id (chat A's 9 must not gate chat B).
    auth = _auth_obj_g5()
    assert auth.authenticate(_signed_g5("c1", "KILL", "", "9")).ok is True
    # c2 has never sent -> its sentinel is -1, so nonce 1 must be accepted despite c1 being at 9.
    assert auth.authenticate(_signed_g5("c2", "KILL", "", "1")).ok is True


def test_gate5_first_nonce_zero_accepts(tmp_path):
    # Kills: mutation setting the unseen sentinel to 0 instead of -1 (nonce 0 would falsely be a replay).
    auth = _auth_obj_g5()
    assert auth.authenticate(_signed_g5("c1", "KILL", "", "0")).ok is True
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” all pass immediately. RED checks (each reverted after observing): change `<=` to `<` â†’ `test_gate5_equal_nonce_is_replay_refused` FAILS; change `self._seen.get(nc_chat, -1)` to a shared instance int â†’ `test_gate5_nonce_is_per_chat_id_independent` FAILS; change sentinel `-1` to `0` â†’ `test_gate5_first_nonce_zero_accepts` FAILS. Restore each.

- [ ] **Step 3: Minimal implementation** â€” none (gate 5 from A8).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (40 passing) then full suite (803 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin gate 5 nonce replay boundary + per-chat-id independence"'`

---

### Task A14: Gate ORDER pins â€” first-failing-gate reported (bad-chat + bad-sig â†’ `l8_bad_chat`; unknown-cmd before hmac)
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _auth_obj_ord(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(secret))


def test_order_bad_chat_before_bad_sig(tmp_path):
    # Kills: mutation reordering the HMAC gate ahead of the allowlist gate. A message with BOTH a
    # non-allowlisted chat-id AND a garbage sig must report l8_bad_chat (chat checked first), proving
    # HMAC is never even computed for an unknown chat.
    auth = _auth_obj_ord(allowlist={"c1": "operator"})
    raw = RawMessage(chat_id="intruder", command="KILL", payload="", nonce="1", sig=b"garbage")
    result = auth.authenticate(raw)
    assert result.reason == _auth.REASON_BAD_CHAT     # NOT l8_bad_sig


def test_order_unknown_cmd_before_bad_sig(tmp_path):
    # Kills: mutation reordering HMAC ahead of the command-set gate. An unknown command with a bad sig
    # must report l8_unknown_cmd (command checked before the crypto).
    auth = _auth_obj_ord(allowlist={"c1": "operator"})
    raw = RawMessage(chat_id="c1", command="NOPE", payload="", nonce="1", sig=b"garbage")
    result = auth.authenticate(raw)
    assert result.reason == _auth.REASON_UNKNOWN_CMD  # NOT l8_bad_sig


def test_order_malformed_before_bad_chat(tmp_path):
    # Kills: mutation reordering the allowlist gate ahead of the structure gate. An empty chat-id must
    # report l8_malformed (structure first), not l8_bad_chat.
    auth = _auth_obj_ord(allowlist={"c1": "operator"})
    raw = RawMessage(chat_id="", command="KILL", payload="", nonce="1", sig=b"garbage")
    assert auth.authenticate(raw).reason == _auth.REASON_MALFORMED
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” all pass immediately (gate order correct in A8). RED check: temporarily move the gate-4 HMAC block above the gate-2 allowlist block â†’ `test_order_bad_chat_before_bad_sig` FAILS (`reason == 'l8_bad_sig'`). Restore.

- [ ] **Step 3: Minimal implementation** â€” none (order pinned by A8's structure).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (43 passing) then full suite (806 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin the five-gate order (first-failing-gate wins)"'`

---

### Task A15: Rotation â€” a message signed under the OLD secret fails `l8_bad_sig` after `rotate` (cross-restart-replay defense)
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helpers)
```python
def _signed_rot(chat_id, command, payload, nonce, secret):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_rotation_old_secret_sig_fails_after_rotate(tmp_path):
    # Kills: mutation making authenticate() read a cached/stale secret instead of secret_holder.current().
    # After rotate(new), a message validly signed under the OLD secret must now fail l8_bad_sig.
    holder = SecretHolder(b"old-secret")
    auth = CommandAuth(allowlist={"c1": "operator"}, secret_holder=holder)
    old_signed = _signed_rot("c1", "KILL", "", "1", secret=b"old-secret")
    holder.rotate(b"new-secret")
    result = auth.authenticate(old_signed)
    assert result.ok is False and result.reason == _auth.REASON_BAD_SIG


def test_rotation_new_secret_sig_authenticates_after_rotate(tmp_path):
    # Kills: over-tight rotation that breaks the live path. A message signed under the NEW secret
    # must authenticate after rotate. Accept half of the pair.
    holder = SecretHolder(b"old-secret")
    auth = CommandAuth(allowlist={"c1": "operator"}, secret_holder=holder)
    holder.rotate(b"new-secret")
    new_signed = _signed_rot("c1", "KILL", "", "1", secret=b"new-secret")
    result = auth.authenticate(new_signed)
    assert result.ok is True and result.command == "KILL"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” both pass immediately (A8 reads `self._secret_holder.current()` per call). RED check: temporarily cache the secret in `__init__` (`self._secret = secret_holder.current()`) and use it in gate 4 â†’ `test_rotation_old_secret_sig_fails_after_rotate` FAILS (old sig still verifies against the cached old secret). Restore.

- [ ] **Step 3: Minimal implementation** â€” none (A8 already reads `current()` at verify time).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (45 passing) then full suite (808 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin secret rotation defeats old-secret replay"'`

---

### Task A16: Neutralize wiring â€” control/bidi chars stripped from chat_id/command/nonce (untrusted-field defense)
**Files:** Test-only `tests/test_ers_telegram_auth.py`

- [ ] **Step 1: Write the failing test** (append â€” re-declares helper)
```python
def _signed_neut(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_neutralize_strips_zero_width_from_command_before_dispatch(tmp_path):
    # Kills: mutation removing neutralize() on the command; a zero-width-joiner-laced "KI<ZWJ>LL" would
    # otherwise miss the command set. The signed helper neutralizes before signing, so a raw laced command
    # that neutralizes to "KILL" must authenticate with command=="KILL".
    auth = CommandAuth(allowlist={"c1": "operator"}, secret_holder=SecretHolder(b"unit-secret"))
    laced_cmd = "KI\u200dLL"                        # ZWJ (Cf) inside KILL
    raw = _signed_neut("c1", laced_cmd, "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.command == "KILL"


def test_neutralize_strips_control_char_from_chat_id_before_allowlist(tmp_path):
    # Kills: mutation removing neutralize() on chat_id; a control-char-laced id that neutralizes to an
    # allowlisted id must match the allowlist (and the returned chat_id is the clean form).
    auth = CommandAuth(allowlist={"c1": "operator"}, secret_holder=SecretHolder(b"unit-secret"))
    laced_chat = "c\x001"                           # NUL (Cc) inside c1
    raw = _signed_neut(laced_chat, "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.chat_id == "c1"


def test_neutralize_returned_command_carries_no_control_chars(tmp_path):
    # Kills: mutation returning raw.command instead of the neutralized nc_cmd in the ok AuthResult
    # (a laced command string could otherwise flow to the __apply map / audit detail).
    auth = CommandAuth(allowlist={"c1": "operator"}, secret_holder=SecretHolder(b"unit-secret"))
    raw = _signed_neut("c1", "PAU\u200bSE", "", "1")   # ZWSP (Cf) inside PAUSE
    result = auth.authenticate(raw)
    assert result.ok is True and result.command == "PAUSE"
    assert "\u200b" not in result.command
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” all pass immediately (A8 neutralizes all three plumbing fields and returns `nc_cmd`/`nc_chat`). RED check: temporarily change gate-4 return to `command=raw.command` â†’ `test_neutralize_returned_command_carries_no_control_chars` FAILS; drop `neutralize` on `nc_cmd` â†’ the command tests FAIL (laced command misses the set â†’ `l8_unknown_cmd`). Restore.

- [ ] **Step 3: Minimal implementation** â€” none (neutralize wiring from A8).

- [ ] **Step 4: Task file green + FULL suite green** â€” task file (48 passing) then full suite (811 passing).

- [ ] **Step 5: Commit** â€” `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram_auth.py && git commit -m "S4.6a: pin neutralize wiring on chat_id/command/nonce"'`

---

**Sub-slice S4.6a exit state:** `src/polybot/ers/telegram_auth.py` complete (`RawMessage`, `AuthResult`, `TelegramTransport`, `SecretHolder`, `_COMMAND_SET`, five `REASON_*`, `canonical_message`, `compute_mac`, `CommandAuth` with all five fail-closed gates in order); `src/polybot/ers/safety.py` extended with the four new `REASON_L8_*` constants (sacred `set_state`/`swap_caps`/`verdict` bodies untouched). Full suite 763 â†’ 811. Every gate has an explicit refuse/accept PAIR; gate order, constant-time compare, rotation, and neutralize wiring pinned. Ready for S4.6b (`ers/telegram.py` `TelegramController`), which imports these plus `neutralize`d command dispatch.

---

I have everything verified. Here is the S4.6b plan fragment.

## Sub-slice S4.6b: The bounded controller

**Prereqs:** S4.6a (`src/polybot/ers/telegram_auth.py` with `RawMessage`/`AuthResult`/`SecretHolder`/`CommandAuth`/`canonical_message`/`compute_mac`/`_COMMAND_SET`) and the four new `REASON_L8_*` constants in `safety.py` MUST be committed and green first. Every task below imports from `telegram_auth` and the new `REASON_*` â€” if S4.6a is not yet merged, do not start B. Baseline before S4.6 = 763 passing; run the full suite green after every task. Branch `pol-6-s4.6-telegram`.

**Design note carried through every B task (module-level helpers, copied verbatim into `tests/test_ers_telegram.py` â€” NO conftest/fixtures beyond `tmp_path`):**

```python
"""S4.6b (POL-6) -- the structurally-bounded L8 TelegramController.

Skeleton + name-mangled composition + the structural-sweep/command-set-pin/source-scan,
then drain() (poll -> authenticate -> apply -> audit, per-message isolation), then the
six-verb __apply map (each verb its exact SafetyController primitive + op_audit row).
Runs SHADOW-ONLY over a fake transport. BLACKLIST raises NotImplementedError here (S4.6d
completes it). Clocks are injected 0-arg callables; money is Decimal; helpers copied per
file per convention (no conftest, no test classes).
"""
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.telegram import TelegramController
from polybot.ers.telegram_auth import (
    CommandAuth, RawMessage, SecretHolder, canonical_message, compute_mac,
)

_SECRET = b"s4.6b-test-secret"


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _running_ctl(store):
    # Boot HALTED -> the operator clean-reconcile RUNNING (one state_change audit row is
    # seeded BEFORE the drain; every op_audit assertion below accounts for it).
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl


def _auth(*, allowlist=None):
    # A real CommandAuth over a real SecretHolder (the pure S4.6a core -- not re-faked).
    if allowlist is None:
        allowlist = {"chatA": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(_SECRET))


def _signed(chat_id, command, payload, nonce, secret=_SECRET):
    # Build a VALID signed RawMessage: sig = HMAC over the canonical (neutralized-order)
    # fields. canonical_message takes a RawMessage; the sig field is irrelevant to it, so
    # pass a placeholder sig, compute the mac, then rebuild frozen with the real sig.
    unsigned = RawMessage(chat_id=chat_id, command=command, payload=payload,
                          nonce=nonce, sig=b"")
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


class _FakeTransport:
    # poll() returns a queued list of RawMessages ONCE then []; send() records + returns True.
    def __init__(self, queued=()):
        self._queued = list(queued)
        self.sent = []

    def poll(self):
        out, self._queued = self._queued, []
        return out

    def send(self, text):
        self.sent.append(text)
        return True
```

> Convention checks baked in: `_running_ctl` seeds exactly ONE `("state_change", "clean_reconcile", "RUNNING")` op_audit row; every op_audit assertion in B compares the FULL list including that seed row so a mutation that drops/duplicates a drain row is caught. Clocks are `lambda: 0`. `_signed` is the valid-message helper; an invalid message uses `sig=b"wrongsig"` or a sig under a different secret. `RiskCaps().per_trade == Decimal("12")`; `step_weekly` tightens it to `Decimal("6")`.

---

### Task B1: `TelegramController` skeleton + name-mangled composition + structural-sweep

**Files:** Create `src/polybot/ers/telegram.py`; Create/append `tests/test_ers_telegram.py` (module docstring + helpers above, then this test).

- [ ] **Step 1: Write the failing test** (append after the helpers)

```python
def test_structural_sweep_public_surface_is_exactly_drain_and_notify_no_trade_verb(tmp_path):
    # Load-bearing L8 safety guarantee (mirrors test_ers_facade.py's sweep): the controller
    # exposes EXACTLY {drain, notify}, holds ctl/store/transport/auth ONLY under name-mangling,
    # is not callable, is not a SafetyController subclass, and reaches set_state/swap_caps ONLY
    # via the six-verb map -- so a compromised channel can at worst STOP the bot.
    # Kills: leaking a public place/sign/set_state/active_caps/controller attr; a bare/underscore
    # ctl or store handle; subclassing SafetyController; adding __call__.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport()
        auth = _auth()
        tc = TelegramController(ctl, store, transport, auth)

        # (a) Public surface is EXACTLY the two allowed names.
        allowed = {"drain", "notify"}
        public = {name for name in dir(tc) if not name.startswith("_")}
        assert public == allowed, f"unexpected public surface: {public ^ allowed}"

        # (b) No trade / op-state-mutation / handle attr reachable, bare OR single-underscore.
        for name in ("place", "propose_trade", "open_trade", "sign", "submit",
                     "set_state", "swap_caps", "active_caps", "state", "verdict",
                     "record_op_event", "controller", "store", "transport", "auth"):
            assert not hasattr(tc, name), f"forbidden attr exposed: {name}"
            assert name not in dir(tc), name
            assert not hasattr(tc, "_" + name), f"forbidden single-underscore attr: _{name}"

        # (c) Not callable -- a __call__ would be an unguarded dispatch path outside {drain, notify}.
        assert not callable(tc), "TelegramController must not be callable"

        # (d) Composition-only over the SafetyController (no inherited mutators).
        assert not isinstance(tc, SafetyController)
        assert SafetyController not in type(tc).__mro__

        # (e) The controller/store refs exist ONLY under name-mangling -- no plain dot-in path.
        assert getattr(tc, "_TelegramController__ctl", None) is ctl
        assert getattr(tc, "_TelegramController__store", None) is store
        assert getattr(tc, "_TelegramController__transport", None) is transport
        assert getattr(tc, "_TelegramController__auth", None) is auth
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  Expected: `ModuleNotFoundError: No module named 'polybot.ers.telegram'` (collection error â€” the module does not exist yet).

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/telegram.py` with the skeleton (map + drain stubbed just enough that the sweep passes; the six-verb body lands in B4-B6, so BLACKLIST/full map may be minimal here but the imports and mangled fields must be final):

```python
"""L8 TelegramController (S4.6b/c/d / POL-6) -- the structurally-bounded remote safety surface.

Mirrors ProposeOnlyFacade: composes (never subclasses) a name-mangled SafetyController + store +
transport + auth, and exposes EXACTLY {drain, notify}. There is structurally NO place/sign/
open-trade path -- the command map dispatches ONLY the six safety-INCREASING verbs, so a
compromised channel can at worst STOP the bot. drain() runs on the serial runloop (S4.6d seam),
so set_state/swap_caps stay single-threaded. See DESIGN-S4.6-TELEGRAM.md SS2/SS3/SS6.
"""
from polybot.ers.ramp import step_weekly
from polybot.ers.safety import (
    FLATTENING, HALTED, PAUSED, RUNNING,
    REASON_L8_ALERTS_DOWN, REASON_L8_BLACKLIST, REASON_L8_KILL, REASON_L8_LOWER_CAPS,
    REASON_L8_PAUSED, REASON_L8_RESUME, REASON_OP_FLATTEN,
)

# The op-audit reason each authenticated verb records on its l8_command row (the state
# transition itself is audited by set_state/swap_caps; this is the command-level provenance).
_REASON_FOR_COMMAND = {
    "KILL": REASON_L8_KILL,
    "PAUSE": REASON_L8_PAUSED,
    "RESUME": REASON_L8_RESUME,
    "FLATTEN": REASON_OP_FLATTEN,
    "LOWER_CAPS": REASON_L8_LOWER_CAPS,
    "BLACKLIST": REASON_L8_BLACKLIST,
}


class TelegramController:
    def __init__(self, controller, store, transport, auth, *, alerts_down_threshold=3):
        self.__ctl = controller           # name-mangled -> _TelegramController__ctl
        self.__store = store
        self.__transport = transport
        self.__auth = auth
        self.__threshold = alerts_down_threshold
        self.__notify_fails = 0

    def drain(self):
        for raw in self.__transport.poll():
            result = self.__auth.authenticate(raw)
            if not result.ok:
                self.__store.record_op_event(
                    kind="l8_refused", reason=result.reason, detail=result.chat_id)
                continue
            try:
                self.__apply(result)
                self.__store.record_op_event(
                    kind="l8_command", reason=self.__reason_for(result.command),
                    detail=result.command)
            except Exception as exc:
                self.__store.record_op_event(
                    kind="l8_command", reason="l8_apply_error",
                    detail=f"{result.command}:{exc}")

    def notify(self, text):
        # Best-effort; the alerts-down->HALT counter lands in S4.6c. Placeholder keeps the
        # public surface == {drain, notify} for the B1 sweep; C-tasks flesh out the body.
        try:
            self.__transport.send(text)
        except Exception:
            pass

    def __reason_for(self, command):
        return _REASON_FOR_COMMAND[command]

    def __apply(self, result):
        command = result.command
        if command == "KILL":
            self.__ctl.set_state(HALTED, reason=REASON_L8_KILL)
        elif command == "PAUSE":
            self.__ctl.set_state(PAUSED, reason=REASON_L8_PAUSED)
        elif command == "RESUME":
            self.__ctl.set_state(RUNNING, reason=REASON_L8_RESUME)
        elif command == "FLATTEN":
            self.__ctl.set_state(FLATTENING, reason=REASON_OP_FLATTEN)
        elif command == "LOWER_CAPS":
            self.__ctl.swap_caps(step_weekly(self.__ctl.active_caps()), reason=REASON_L8_LOWER_CAPS)
        elif command == "BLACKLIST":
            # S4.6d wires store.record_blacklist(kind:value); until then this verb is unbuilt and
            # an authenticated BLACKLIST hits the drain's l8_apply_error isolation path (B3 pins it).
            raise NotImplementedError("BLACKLIST wiring lands in S4.6d")
        else:  # pragma: no cover -- unreachable: _COMMAND_SET gates command before dispatch.
            raise ValueError(f"unmapped command: {command!r}")
```

> `notify` is intentionally a thin placeholder here so B1's sweep sees the final public surface; its counter/alerts-down body is S4.6c and MUST NOT be asserted in B. The BLACKLIST `raise NotImplementedError` is the documented B stub â€” S4.6d replaces that one branch.

- [ ] **Step 4: Task file green + FULL suite green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  then `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` (expect 763 + the B1 test green).

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/telegram.py tests/test_ers_telegram.py && git commit -m "S4.6b: TelegramController skeleton + name-mangled composition + structural-sweep"`

---

### Task B2: `_COMMAND_SET` == the six verbs pin + module source-scan (no trade verb)

**Files:** Modify `tests/test_ers_telegram.py` (append two tests); no source change (both pass on the B1 module).

- [ ] **Step 1: Write the failing test** â€” write BOTH first (they belong to one concern: the structural pin that no open-trade verb exists), observe both fail, then confirm the B1 source already satisfies them (this task is the executable pin, so it may go green immediately on the existing source â€” that is expected for a pin test; the RED is proven by the assertion existing before the pin is trusted). To force a genuine RED for the source-scan, temporarily confirm the scan catches a forbidden substring (see Step 2 note).

```python
def test_command_set_is_exactly_the_six_safety_increasing_verbs(tmp_path):
    # Structural pin: the auth command set (imported from telegram_auth, the single source of
    # truth) is EXACTLY the six safety-increasing verbs -- no seventh, no open-trade verb.
    # Kills: adding a verb to _COMMAND_SET; dropping one; a typo'd verb string.
    from polybot.ers.telegram_auth import _COMMAND_SET
    assert _COMMAND_SET == frozenset(
        {"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"})


def test_telegram_module_source_never_references_a_trade_verb():
    # Defense-in-depth over the structural sweep: the module TEXT must not contain a
    # place/propose/sign/submit/open_trade token -- there is no code path, dead or live, that
    # could ever be a trade dispatch. "open_trade" (not bare "open") avoids false hits on the
    # English word 'open' in the docstring.
    # Kills: sneaking a place()/sign()/submit()/propose_trade()/open_trade() into telegram.py.
    import polybot.ers.telegram as _mod
    src = Path(_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("place", "propose_trade", "sign", "submit", "open_trade"):
        assert forbidden not in src, f"trade-verb token leaked into telegram.py: {forbidden!r}"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_command_set_is_exactly_the_six_safety_increasing_verbs tests/test_ers_telegram.py::test_telegram_module_source_never_references_a_trade_verb -o addopts="" -q'`
  Expected before B1 exists: import error. Given B1 is committed, these are PIN tests over correct source and will pass green â€” to observe a real RED for the source-scan, momentarily add a comment line `# place` to `telegram.py`, run, watch `AssertionError: trade-verb token leaked ... 'place'`, then REMOVE it. (Note the risk called out in the pinned contract: "sign" is a substring of e.g. "assign"/"design"/"signature" â€” the B1 source was written to avoid ALL of those tokens; if a future edit needs the word "assign"/"signature", the scan will false-positive by design, which is the intended tripwire. Confirm the committed B1 source contains none of the five substrings before trusting green.)

- [ ] **Step 3: Minimal implementation** â€” none (pins over the B1 source). If the source-scan RED demo above revealed a real token in `telegram.py`, rename it out; otherwise no change.

- [ ] **Step 4: Task file green + FULL suite green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  then full suite `--tb=no`.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_telegram.py && git commit -m "S4.6b: pin _COMMAND_SET==six verbs + telegram.py source-scan (no trade verb)"`

---

### Task B3: `drain()` â€” refused message audits + does NOT mutate + continues; per-message isolation

**Files:** Modify `tests/test_ers_telegram.py` (append three tests). The `drain` body from B1 already implements this; these are the behavioral pins. Add the boundary/refuse PAIR (refused vs accepted) explicitly.

- [ ] **Step 1: Write the failing test**

```python
def test_drain_refused_message_audits_l8_refused_with_reason_and_chat_and_leaves_state(tmp_path):
    # A message auth rejects (unknown chat-id) is audited kind="l8_refused" with the SPECIFIC
    # refusal reason + the neutralized chat_id, and does NOT mutate op-state (still RUNNING).
    # PAIR with the accept case below (test_drain_accepted_message...).
    # Kills: swallowing the refusal (no audit row); auditing the wrong kind/reason; a refused
    # message reaching __apply and mutating op-state.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        bad = _signed("evil", "KILL", "", "1")   # valid sig, but 'evil' not in the allowlist
        transport = _FakeTransport([bad])
        tc = TelegramController(ctl, store, transport, _auth())
        tc.drain()
        assert ctl.state() == _safety.RUNNING                       # op-state untouched
        # The seed state_change row, then exactly one l8_refused row for the bad chat-id.
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("l8_refused", "l8_bad_chat", "evil"),
        ]


def test_drain_accepted_message_applies_and_audits_l8_command(tmp_path):
    # The accept half of the pair: a fully-authenticated KILL applies (HALTED) and is audited
    # kind="l8_command", reason=l8_kill, detail="KILL" -- AND the set_state adds its own
    # state_change row (l8_kill). Kills: dropping the l8_command audit; wrong reason/detail.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        msg = _signed("chatA", "KILL", "", "1")
        transport = _FakeTransport([msg])
        tc = TelegramController(ctl, store, transport, _auth())
        tc.drain()
        assert ctl.state() == _safety.HALTED
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_kill", "HALTED"),          # set_state's own row
            ("l8_command", "l8_kill", "KILL"),              # the drain's command-level row
        ]


def test_drain_isolates_a_raising_apply_audits_l8_apply_error_and_continues(tmp_path):
    # Per-message isolation (mirrors news.poll_all): an authenticated command whose __apply
    # RAISES (BLACKLIST is unbuilt in B -> NotImplementedError) is caught + audited
    # kind="l8_command", reason="l8_apply_error", and the drain CONTINUES to the next message
    # (a following KILL still halts). Two DISTINCT chat-ids so both pass the monotonic nonce.
    # Kills: a raising __apply escaping drain (loop crash); the drain aborting the remaining
    # queue; not auditing the apply error.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        allow = {"chatA": "operator", "chatB": "operator"}
        boom = _signed("chatA", "BLACKLIST", "wallet:0xdead", "1")
        kill = _signed("chatB", "KILL", "", "1")
        transport = _FakeTransport([boom, kill])
        tc = TelegramController(ctl, store, transport, _auth(allowlist=allow))
        tc.drain()
        assert ctl.state() == _safety.HALTED                        # the KILL after the boom applied
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),                    # seed
            ("l8_command", "l8_apply_error"),                       # BLACKLIST raised, isolated
            ("state_change", "l8_kill"),                            # KILL's set_state row
            ("l8_command", "l8_kill"),                              # KILL's command row
        ]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -k "drain_refused or drain_accepted or isolates" -o addopts="" -q'`
  Expected: all three PASS on the B1 `drain` body (it already implements refuse-audit, apply-audit, and try/except isolation). To prove the isolation test's RED is real, momentarily change B1's `except Exception as exc:` to `except ValueError as exc:` (so `NotImplementedError` escapes), run, watch the isolation test error with an uncaught `NotImplementedError`, then revert. Similarly to prove the refuse test's RED, momentarily change the refused branch to `continue` WITHOUT the `record_op_event` and watch the op_audit assertion fail; revert.

- [ ] **Step 3: Minimal implementation** â€” none (the B1 `drain` body is the implementation; B3 pins its three behaviors). If either RED demo above exposed a real defect, restore the B1 body exactly as written in Task B1 Step 3.

- [ ] **Step 4: Task file green + FULL suite green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  then full suite `--tb=no`.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_telegram.py && git commit -m "S4.6b: drain refuse-audit/no-mutate + accept-apply + per-message isolation"`

---

### Task B4: `__apply` â€” KILL + PAUSE (each its exact primitive + reason)

**Files:** Modify `tests/test_ers_telegram.py` (append two tests). Body already in B1; these pin each verb's exact `set_state` reason and op_audit rows.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_kill_from_running_halts_via_set_state_l8_kill(tmp_path):
    # KILL -> set_state(HALTED, reason=REASON_L8_KILL). The state_change audit carries l8_kill
    # (NOT a generic 'halted'), and the drain's l8_command row detail is "KILL".
    # Kills: KILL routed to PAUSED/FLATTENING; wrong reason on set_state; a swap_caps instead.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "KILL", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.HALTED
        assert store.op_audit_log()[-2:] == [
            {"at": store.op_audit_log()[-2]["at"], "kind": "state_change",
             "reason": "l8_kill", "detail": "HALTED"},
            {"at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
             "reason": "l8_kill", "detail": "KILL"},
        ]


def test_apply_pause_from_running_pauses_via_set_state_l8_paused(tmp_path):
    # PAUSE -> set_state(PAUSED, reason=REASON_L8_PAUSED). Distinct from KILL: soft halt.
    # Kills: PAUSE routed to HALTED; reason l8_kill instead of l8_paused.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "PAUSE", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.PAUSED
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_paused", "PAUSED"),
            ("l8_command", "l8_paused", "PAUSE"),
        ]
```

> The KILL test uses a `[-2:]` slice against the freshly re-read log to isolate the two drain-produced rows without re-hardcoding the seed; the PAUSE test uses the full-list tuple-compare for the exhaustive form. Both patterns are in-repo idioms.

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -k "apply_kill or apply_pause" -o addopts="" -q'`
  Expected: PASS on the B1 map. Prove RED by momentarily swapping the KILL branch to `set_state(PAUSED, reason=REASON_L8_PAUSED)` in `telegram.py`, run, watch `assert ctl.state() == HALTED` fail; revert.

- [ ] **Step 3: Minimal implementation** â€” none beyond B1 (`__apply` KILL/PAUSE branches). Restore B1 body if the RED demo mutated it.

- [ ] **Step 4: Task file green + FULL suite green** â€” task file, then full suite `--tb=no`.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_telegram.py && git commit -m "S4.6b: __apply KILL->HALTED(l8_kill) + PAUSE->PAUSED(l8_paused)"`

---

### Task B5: `__apply` â€” RESUME lifts PAUSED and HALTED (Fork 1 pair) + FLATTEN

**Files:** Modify `tests/test_ers_telegram.py` (append three tests). RESUME is the operator-trusted HALTEDâ†’RUNNING path; test BOTH source states (Fork 1). FLATTEN uses the existing `REASON_OP_FLATTEN`.

- [ ] **Step 1: Write the failing test**

```python
def test_apply_resume_lifts_a_paused_loop_to_running_l8_resume(tmp_path):
    # RESUME (from PAUSED) -> set_state(RUNNING, reason=REASON_L8_RESUME). Half 1 of the Fork-1
    # pair. Reach PAUSED via a direct set_state (operator PAUSE), THEN drain a RESUME.
    # Kills: RESUME reason wrong; RESUME failing to reach RUNNING from PAUSED.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        transport = _FakeTransport([_signed("chatA", "RESUME", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-2:] == [
            {"at": store.op_audit_log()[-2]["at"], "kind": "state_change",
             "reason": "l8_resume", "detail": "RUNNING"},
            {"at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
             "reason": "l8_resume", "detail": "RESUME"},
        ]


def test_apply_resume_lifts_a_halted_loop_to_running_l8_resume(tmp_path):
    # RESUME (from HALTED) -> RUNNING. Half 2 of the Fork-1 pair: this IS the documented operator
    # override that clears a sticky L5/loss HALT (the ONLY operator HALTED->RUNNING path). The
    # controller boots HALTED (unclean_restart) -- no set_state needed to reach the source state.
    # Kills: gating RESUME on the source being PAUSED only (a HALTED loop would stay stuck).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot HALTED
        assert ctl.state() == _safety.HALTED
        transport = _FakeTransport([_signed("chatA", "RESUME", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1] == {
            "at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
            "reason": "l8_resume", "detail": "RESUME"}


def test_apply_flatten_from_running_sets_flattening_op_flatten(tmp_path):
    # FLATTEN -> set_state(FLATTENING, reason=REASON_OP_FLATTEN). It sets the op-state ONLY; the
    # actual de-risk fires later in verdict() (unchanged S4.1 path), NOT in __apply.
    # Kills: FLATTEN calling signer.flatten directly from __apply; wrong op-state/reason.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "FLATTEN", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.FLATTENING
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "op_flatten", "FLATTENING"),
            ("l8_command", "op_flatten", "FLATTEN"),
        ]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -k "apply_resume or apply_flatten" -o addopts="" -q'`
  Expected: PASS on the B1 map. Prove RED for the HALTED-lift by momentarily guarding the RESUME branch `if self.__ctl.state() == PAUSED:` (so a boot-HALTED loop stays HALTED), run, watch half-2 fail; revert.

- [ ] **Step 3: Minimal implementation** â€” none beyond B1 (`__apply` RESUME/FLATTEN branches map unconditionally, satisfying Fork 1). Restore B1 body if mutated.

- [ ] **Step 4: Task file green + FULL suite green** â€” task file, then full suite `--tb=no`.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_telegram.py && git commit -m "S4.6b: RESUME lifts PAUSED and HALTED (Fork 1) + FLATTEN->FLATTENING"`

---

### Task B6: `__apply` â€” LOWER_CAPS tightens to per_trade 6 via `swap_caps` + idempotent no-op pair

**Files:** Modify `tests/test_ers_telegram.py` (append two tests). This is the boundary/no-op PAIR: first LOWER_CAPS writes a `caps_swap` row and drops per_trade to 6; a second is a hash-identical no-op (no second `caps_swap` row).

- [ ] **Step 1: Write the failing test**

```python
def test_apply_lower_caps_tightens_per_trade_to_six_via_swap_caps(tmp_path):
    # LOWER_CAPS -> swap_caps(step_weekly(active_caps()), reason=REASON_L8_LOWER_CAPS). Default
    # RiskCaps per_trade 12 -> step_weekly -> 6; swap_caps writes a caps_swap audit row
    # (reason=l8_lower_caps) BEFORE the l8_command row.
    # Kills: LOWER_CAPS calling set_state; using step_daily (per_trade 9) instead of step_weekly;
    # wrong swap_caps reason; bypassing swap_caps (no caps_swap audit row).
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        assert ctl.active_caps().per_trade == Decimal("12")
        transport = _FakeTransport([_signed("chatA", "LOWER_CAPS", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.active_caps().per_trade == Decimal("6")
        assert ctl.active_caps().total_open_risk == Decimal("30")
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),
            ("caps_swap", "l8_lower_caps"),          # swap_caps' own audit-before-mutate row
            ("l8_command", "l8_lower_caps"),         # the drain's command row
        ]


def test_apply_lower_caps_a_second_time_is_a_hash_identical_no_op_no_caps_swap_row(tmp_path):
    # The idempotent boundary (pair with the tighten case): step_weekly is idempotent, so a
    # SECOND LOWER_CAPS produces a hash-identical caps -> swap_caps returns False -> NO second
    # caps_swap row -- but the l8_command row IS still written (the command WAS authenticated +
    # applied; the swap was simply a no-op). Two chat-ids so both messages pass the nonce gate.
    # Kills: a compounding step (per_trade < 6 on the 2nd); a spurious 2nd caps_swap audit row;
    # the drain skipping the l8_command row when swap_caps no-ops.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        allow = {"chatA": "operator", "chatB": "operator"}
        first = _signed("chatA", "LOWER_CAPS", "", "1")
        second = _signed("chatB", "LOWER_CAPS", "", "1")
        transport = _FakeTransport([first, second])
        TelegramController(ctl, store, transport, _auth(allowlist=allow)).drain()
        assert ctl.active_caps().per_trade == Decimal("6")        # not compounded below 6
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),
            ("caps_swap", "l8_lower_caps"),      # only the FIRST swap writes a caps_swap row
            ("l8_command", "l8_lower_caps"),     # first command
            ("l8_command", "l8_lower_caps"),     # second command applied (swap no-op'd, still audited)
        ]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -k "lower_caps" -o addopts="" -q'`
  Expected: PASS on the B1 map. Prove RED by momentarily swapping `step_weekly` for `step_daily` in the LOWER_CAPS branch, run, watch `assert per_trade == Decimal("6")` fail (`step_daily` gives 9); revert to `step_weekly`.

- [ ] **Step 3: Minimal implementation** â€” none beyond B1 (`__apply` LOWER_CAPS branch already routes `step_weekly(active_caps())` through `swap_caps`). Restore B1 body if mutated.

- [ ] **Step 4: Task file green + FULL suite green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  then `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` (expect 763 + all B tests green).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_telegram.py && git commit -m "S4.6b: LOWER_CAPS->step_weekly via swap_caps (per_trade 6) + idempotent no-op"`

---

**Sub-slice B done-check (before handing to S4.6c):** `TelegramController` public surface is exactly `{drain, notify}`; the six-verb `__apply` maps KILL/PAUSE/RESUME/FLATTEN/LOWER_CAPS to their exact `SafetyController` primitives + audit rows, with BLACKLIST stubbed as `NotImplementedError` (S4.6d completes it, and B3's isolation test pins that an authenticated BLACKLIST currently hits the `l8_apply_error` path without crashing the drain); `_COMMAND_SET` and the module source-scan are pinned. `notify` is a thin placeholder (its alerts-downâ†’HALT counter is S4.6c) and MUST NOT be relied on by B. Sacred surfaces (`validator`/`evaluate_intent`, `service.py` flow, `set_state`/`swap_caps`/`verdict` internals, `caps.py`, `ramp.py`) are byte-unchanged â€” B only ADDS `src/polybot/ers/telegram.py` and `tests/test_ers_telegram.py`, importing the S4.6a `REASON_L8_*` constants and `telegram_auth` symbols.

**Files (all repo-relative):**
- Create `src/polybot/ers/telegram.py`
- Create `tests/test_ers_telegram.py`

---

The plan is complete and verified against the actual source. Here is the S4.6c plan fragment.

---

## Sub-slice S4.6c: notify() best-effort + the alerts-down â†’ HALTED health counter

**Preconditions (built by earlier sub-slices, serial on `pol-6-s4.6-telegram`):** S4.6a landed `src/polybot/ers/telegram_auth.py` (+ the 4 `REASON_L8_*` constants in `safety.py`, incl. `REASON_L8_ALERTS_DOWN = "l8_alerts_down"`). S4.6b landed `src/polybot/ers/telegram.py` with `class TelegramController.__init__(self, controller, store, transport, auth, *, alerts_down_threshold=3)` storing name-mangled `__ctl/__store/__transport/__auth/__threshold/__notify_fails`, plus `drain()`. Its structural-sweep test pins the public surface to EXACTLY `{drain, notify}`, so a `notify` member already EXISTS after S4.6b (as a stub / minimal body). **S4.6c makes `notify` functional** â€” the failing tests below drive out the real best-effort + consecutive-failure-counter + threshold-halt body, REPLACING whatever stub S4.6b left. New tests are APPENDED to `tests/test_ers_telegram.py` (task ids C1â€“C5).

**Verified facts used below (re-read when writing code):**
- `safety.py`: `RUNNING/PAUSED/HALTED/FLATTENING` module strings; `REASON_L8_ALERTS_DOWN = "l8_alerts_down"` (added S4.6a). `SafetyController(*, caps, store, clock)`; `set_state(op_state, *, reason)` writes an op_audit row `kind="state_change", reason=<reason>, detail=<op_state>` BEFORE mutating; `state()` returns the current op-state string.
- `intent_store.py`: `IntentStore(path, stamper)` (ctx-managed `with ... as store:`); `op_audit_log()` â†’ `[{"at","kind","reason","detail"}]` ORDER BY op_id. `record_op_event(*, kind, reason, detail="")`.
- `core/clock.py`: `MonotonicStamper()`. Â· `caps.py`: `RiskCaps()` (all defaults valid).
- `notify()` per the PINNED CONTRACT: `try: ok=self.__transport.send(text) except Exception: ok=False`. `if ok: self.__notify_fails=0 else: self.__notify_fails+=1; if self.__notify_fails >= self.__threshold: self.__ctl.set_state(HALTED, reason=REASON_L8_ALERTS_DOWN)`. NEVER re-raise; returns None. NO op_audit row is written BY notify itself (the only audit trail of the halt is `set_state`'s own `state_change/l8_alerts_down` row).
- `notify()` NEVER touches `poll`/`auth`, so a trivial no-method stub `auth` and a `poll()->[]` transport suffice; `__init__` does NO validation of `auth`.

---

### Task C1: a successful send returns None, does not raise, and resets the consecutive-failure counter
**Files:**
- Modify `src/polybot/ers/telegram.py` (add/replace the `notify` method body on `TelegramController`)
- Test `tests/test_ers_telegram.py` (APPEND â€” module-level helpers copied per file; NO conftest/fixtures beyond tmp_path)

- [ ] **Step 1: Write the failing test** (complete python code â€” APPEND to `tests/test_ers_telegram.py`)
```python
# ---------------------------------------------------------------------------
# S4.6c: notify() best-effort + alerts-down halt (tasks C1-C5)
# Module-level helpers copied per file (no conftest / no shared fixtures).
# ---------------------------------------------------------------------------
from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.ers.caps import RiskCaps
from polybot.ers import safety as _safety
from polybot.ers.telegram import TelegramController


def _c_store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _c_ctl(store):
    # SafetyController driven to RUNNING so an alerts-down HALT is an observable transition.
    ctl = _safety.SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl


class _CStubAuth:
    """notify() never touches auth; a no-method stub satisfies construction."""


class _CFlakyTransport:
    """poll()->[] (notify never polls). send() replays a scripted sequence of
    True (success), False (soft failure), or the string "raise" (send raises)."""
    def __init__(self, script):
        self._script = list(script)
        self._i = 0
        self.sent = []

    def poll(self):
        return []

    def send(self, text):
        self.sent.append(text)
        step = self._script[self._i]
        self._i += 1
        if step == "raise":
            raise RuntimeError("telegram send exploded")
        return step


def _c_states(store):
    return [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()]


def test_notify_success_returns_none_and_does_not_halt(tmp_path):
    # Kills: notify() returning a truthy/echoed value instead of None; a spurious halt on success.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([True])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        result = tg.notify("hello operator")
        assert result is None
        assert transport.sent == ["hello operator"]      # the text was actually sent
        assert ctl.state() == _safety.RUNNING            # a success never halts


def test_notify_success_after_failures_resets_the_consecutive_counter(tmp_path):
    # Kills: a CUMULATIVE (not consecutive) counter -- a success in the middle must RESET it,
    #        so fail,fail,success,fail,fail (4 total, run broken) stays BELOW a threshold of 3.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, True, False, False])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        for _ in range(5):
            tg.notify("beat")
        # 4 total failures but never 3 IN A ROW -> no halt; the success reset the run.
        assert ctl.state() == _safety.RUNNING
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - Command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q -k "notify_success"'`
  - Expected failure: if S4.6b left `notify` as a bare stub (`pass`), `test_notify_success_returns_none_and_does_not_halt` FAILS with `AssertionError` on `transport.sent == ["hello operator"]` (the stub never called `send`); `test_notify_success_after_failures_resets_the_consecutive_counter` likewise fails on `_c_states(...)`. (If S4.6b left NO `notify` at all â€” inconsistent with its own sweep test â€” it FAILS with `AttributeError: 'TelegramController' object has no attribute 'notify'`.)

- [ ] **Step 3: Minimal implementation** (complete python code â€” the real `notify` body on `TelegramController` in `src/polybot/ers/telegram.py`; REPLACE the S4.6b stub `notify`). Ensure `HALTED` and `REASON_L8_ALERTS_DOWN` are on the S4.6b top-of-module import line `from polybot.ers.safety import HALTED, PAUSED, RUNNING, FLATTENING, REASON_L8_KILL, REASON_L8_PAUSED, REASON_OP_FLATTEN, REASON_L8_RESUME, REASON_L8_LOWER_CAPS, REASON_L8_BLACKLIST, REASON_L8_ALERTS_DOWN`.
```python
    def notify(self, text):
        """Best-effort fire-and-forget alert over the (fake) transport. NEVER raises into the
        caller/loop: a send that returns False OR raises is counted as ONE consecutive failure.
        A success resets the run to zero. When the CONSECUTIVE-failure run reaches the
        alerts-down threshold, the master-design fail-safe fires: HALTED(l8_alerts_down)
        (persistent alerts-down means the operator is blind -> stop the bot). Returns None."""
        try:
            ok = self.__transport.send(text)
        except Exception:
            ok = False
        if ok:
            self.__notify_fails = 0
        else:
            self.__notify_fails += 1
            if self.__notify_fails >= self.__threshold:
                self.__ctl.set_state(HALTED, reason=REASON_L8_ALERTS_DOWN)
```
> Note: inside a method of `TelegramController`, `self.__transport` / `self.__notify_fails` / `self.__threshold` / `self.__ctl` resolve to the S4.6b name-mangled `_TelegramController__*` attrs â€” correct as written.

- [ ] **Step 4: Task file green + FULL suite green**
  - Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  - Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` (the pre-S4.6 763 unaffected)

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram.py tests/test_ers_telegram.py && git commit -m "S4.6c: notify() best-effort send + consecutive-failure counter reset"'`

---

### Task C2: a send that returns False does NOT raise; a send that RAISES is caught and counted (not propagated)
**Files:**
- Test `tests/test_ers_telegram.py` (APPEND) â€” no source change (behavior already covered by C1's `notify` body; this is a behavior-lock)

- [ ] **Step 1: Write the failing test** (complete python code â€” APPEND; uses the C1 helpers already in this file)
```python
def test_notify_false_send_does_not_raise_into_caller(tmp_path):
    # Kills: a `raise` on a False send; notify propagating the transport's soft-failure to the loop.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # one soft failure, below threshold
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        result = tg.notify("degraded")                 # MUST NOT raise
        assert result is None
        assert transport.sent == ["degraded"]
        assert ctl.state() == _safety.RUNNING          # one failure < threshold -> no halt yet


def test_notify_raising_send_is_caught_and_counted_not_propagated(tmp_path):
    # Kills: an uncaught exception from send() escaping notify(); a raising send NOT counting
    #        toward alerts-down (a bare `try/except: pass` that forgets to increment the counter).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        # Two raises with threshold=2 -> the SECOND consecutive raise must trip the halt, proving
        # a raising send is both (a) swallowed and (b) counted exactly like a False send.
        transport = _CFlakyTransport(["raise", "raise"])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=2)
        tg.notify("boom-1")                            # raise #1 caught, counted (1 < 2, no halt)
        assert ctl.state() == _safety.RUNNING
        tg.notify("boom-2")                            # raise #2 caught, counted (2 >= 2 -> halt)
        assert ctl.state() == _safety.HALTED
        assert transport.sent == ["boom-1", "boom-2"]  # both sends were attempted, neither escaped
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - Command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q -k "false_send or raising_send"'`
  - Expected failure: BEFORE C1's real `notify` body exists these fail (stub never sends / never halts). If C1 is authored+committed in the same pass, treat C2 as a regression-lock: momentarily replace `notify`'s body with `pass` and observe `test_notify_raising_send_...` fail on `ctl.state() == HALTED`, then restore the real body.

- [ ] **Step 3: Minimal implementation**
  - No source change â€” C1's `notify` body already satisfies both the False-send and raising-send branches (`except Exception: ok=False` + the `>= threshold` halt). This task is a behavior-lock test only.

- [ ] **Step 4: Task file green + FULL suite green**
  - Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  - Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'`

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6c: lock notify() best-effort isolation (False + raising send never propagate)"'`

---

### Task C3: the alerts-down threshold boundary â€” 2 consecutive failures do NOT halt, the 3rd DOES (refuse/accept PAIR)
**Files:**
- Test `tests/test_ers_telegram.py` (APPEND) â€” no source change (C1's body already implements `>= threshold`)

- [ ] **Step 1: Write the failing test** (complete python code â€” APPEND; the boundary PAIR)
```python
def test_notify_two_consecutive_failures_below_default_threshold_do_not_halt(tmp_path):
    # Kills (boundary, the "no" half of the pair): an off-by-one `>= threshold-1` that would
    #        halt at the 2nd failure under the default threshold of 3.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False])   # exactly 2 consecutive failures
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("f1")
        tg.notify("f2")
        assert ctl.state() == _safety.RUNNING          # 2 < 3 -> NO halt
        # No state_change row beyond the initial RUNNING (the halt would append one).
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


def test_notify_third_consecutive_failure_at_default_threshold_halts_alerts_down(tmp_path):
    # Kills (boundary, the "yes" half): a `> threshold` that would NEVER fire at exactly 3;
    #        the wrong halt reason; the missing state_change audit row.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, False])   # the 3rd trips it
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("f1")
        tg.notify("f2")
        assert ctl.state() == _safety.RUNNING          # still running after 2
        tg.notify("f3")                                # the 3rd consecutive failure
        assert ctl.state() == _safety.HALTED           # 3 >= 3 -> HALTED
        # set_state wrote the alerts-down transition row (the ONLY audit trail of the halt).
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - Command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q -k "two_consecutive or third_consecutive"'`
  - Expected failure: against the S4.6b stub `notify`, `test_notify_third_consecutive_failure_...` FAILS on `ctl.state() == HALTED` and on the two-row `_c_states` compare. `test_notify_two_consecutive_...` passes even on the stub (that is fine â€” the discriminating half is the 3rd-failure test). If C1 is already committed, both PASS and this task locks the exact boundary + reason + audit row.

- [ ] **Step 3: Minimal implementation**
  - No source change â€” `if self.__notify_fails >= self.__threshold:` in C1's body is exactly the boundary these tests pin.

- [ ] **Step 4: Task file green + FULL suite green**
  - Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  - Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'`

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6c: pin alerts-down threshold boundary (2 no / 3 yes) + halt reason + audit row"'`

---

### Task C4: alerts_down_threshold is a ctor kwarg (default 3) â€” a custom threshold of 1 halts on the FIRST failure (refuse/accept PAIR)
**Files:**
- Test `tests/test_ers_telegram.py` (APPEND) â€” no source change (C1's `__threshold` already reads the ctor kwarg)

- [ ] **Step 1: Write the failing test** (complete python code â€” APPEND; the threshold-is-configurable PAIR)
```python
def test_notify_custom_threshold_one_halts_on_first_failure(tmp_path):
    # Kills: a HARD-CODED threshold of 3 ignoring the ctor kwarg; the "yes" half at threshold=1.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # a single failure
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        tg.notify("only-one")
        assert ctl.state() == _safety.HALTED           # 1 >= 1 -> immediate halt
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]


def test_notify_custom_threshold_one_success_does_not_halt(tmp_path):
    # Kills (the "no" half): a threshold=1 that halts even on SUCCESS (a mis-wired counter that
    #        increments regardless of ok, or a halt outside the `else` branch).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([True])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        tg.notify("healthy")
        assert ctl.state() == _safety.RUNNING          # a success never halts, even at threshold 1
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


def test_notify_default_threshold_is_three(tmp_path):
    # Kills: the ctor default drifting off 3 (a build error would surface here, not only in C3).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, False])
        tg = TelegramController(ctl, store, transport, _CStubAuth())   # NO threshold kwarg -> default
        tg.notify("d1"); tg.notify("d2")
        assert ctl.state() == _safety.RUNNING          # default 3 not reached at 2
        tg.notify("d3")
        assert ctl.state() == _safety.HALTED           # reached at 3 -> the default IS 3
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - Command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q -k "custom_threshold or default_threshold_is_three"'`
  - Expected failure: against the stub `notify`, `test_notify_custom_threshold_one_halts_on_first_failure` and `test_notify_default_threshold_is_three` FAIL on `ctl.state() == HALTED`. The two "no" halves pass on the stub (fine). After C1's body all PASS; if C1 is already committed, this task locks the kwarg wiring + default.

- [ ] **Step 3: Minimal implementation**
  - No source change â€” C1's body uses `self.__threshold` (bound from the S4.6b `alerts_down_threshold=3` ctor kwarg), so both the custom and default paths already work.

- [ ] **Step 4: Task file green + FULL suite green**
  - Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  - Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'`

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6c: pin alerts_down_threshold ctor kwarg (custom=1 immediate, default=3)"'`

---

### Task C5: loop-never-blocks/never-crashes â€” notify over a persistently-raising transport returns normally; no self-written audit row
**Files:**
- Test `tests/test_ers_telegram.py` (APPEND) â€” no source change (integration-shaped behavior-lock over C1's body)

- [ ] **Step 1: Write the failing test** (complete python code â€” APPEND). **The `alerts_rows` expected count is resolved to `3` in Step 3 â€” see the note there before authoring the assert.**
```python
def test_notify_over_persistently_raising_transport_returns_normally_and_halts(tmp_path):
    # Kills: notify() blocking/crashing the loop on a hostile transport; the halt failing to fire
    #        under a stream of raises.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport(["raise", "raise", "raise", "raise", "raise"])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        for i in range(5):
            assert tg.notify(f"m{i}") is None          # every call returns normally, never raises
        assert ctl.state() == _safety.HALTED
        assert transport.sent == ["m0", "m1", "m2", "m3", "m4"]   # all five attempted
        # The counter keeps tripping >= threshold on calls 3,4,5 -> one set_state each = 3 rows
        # (the pinned notify body has NO reset-on-halt and NO already-halted guard).
        alerts_rows = [s for s in _c_states(store) if s == ("state_change", "l8_alerts_down", "HALTED")]
        assert len(alerts_rows) == 3


def test_notify_does_not_write_its_own_op_audit_row(tmp_path):
    # Kills: notify() sneaking a record_op_event call (an l8_* audit row) -- the ONLY audit trail
    #        of the alerts-down halt must be set_state's state_change row, nothing else.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # below threshold -> no halt, no rows at all
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("quiet-failure")
        # Only the setup RUNNING row exists; notify wrote NOTHING to op_audit on a sub-threshold fail.
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - Command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q -k "persistently_raising or own_op_audit"'`
  - Expected failure: against the stub `notify`, `test_notify_over_persistently_raising_transport_...` FAILS on `ctl.state() == HALTED`. `test_notify_does_not_write_its_own_op_audit_row` passes on the stub (fine â€” it locks the no-extra-row property once the real body lands).

- [ ] **Step 3: Minimal implementation** â€” **CONFIRM THE `alerts_rows` COUNT AGAINST THE ACTUAL C1 BODY.** With the pinned `notify` (no counter reset on the halt path, no already-HALTED guard), `__notify_fails` runs 1,2,3,4,5 and `>= 3` holds for calls 3,4,5 â†’ `set_state(HALTED, ...)` fires 3 times â†’ **3 rows**. So `assert len(alerts_rows) == 3` is correct. Do NOT add an idempotence/already-halted guard to `notify` to force `== 1` â€” that is a behavior change outside this sub-slice's pinned contract; `set_state` re-writing the same transition row is the audited-transition doctrine and is harmless. No source change to `telegram.py` in this task.

- [ ] **Step 4: Task file green + FULL suite green**
  - Task file: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'`
  - Full suite: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` (the pre-S4.6 763 still pass + the S4.6a/b/c additions)

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6c: lock notify() loop-never-crashes over a hostile transport + no self-written audit row"'`

---

**Drafter's flags for the executing agent (facts I verified, not guesses):**
- `telegram.py` / `telegram_auth.py` / `tests/test_ers_telegram.py` **do not exist yet** on `pol-6-s4.6-telegram` (763 baseline confirmed). S4.6a+S4.6b must land first; C1's Modify assumes a stub `notify` is present (forced to exist by S4.6b's own `public == {"drain","notify"}` sweep). If S4.6b instead defers `notify` entirely, C1 Step-2 RED is an `AttributeError` and Step-3 ADDS the method â€” the plan still holds.
- The `set_state(HALTED, reason="l8_alerts_down")` audit row is `("state_change", "l8_alerts_down", "HALTED")` â€” verified from `SafetyController.set_state` (writes `kind="state_change", reason=<reason>, detail=<op_state>`) and `op_audit_log()` shape in `intent_store.py`.
- `notify()` writes **no** op_audit row of its own (C5's second test locks this) â€” the pinned contract's `notify` body calls only `transport.send` and `ctl.set_state`.
- Relevant absolute paths: `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\src\polybot\ers\telegram.py`, `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\tests\test_ers_telegram.py`, `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\src\polybot\ers\safety.py`, `...\src\polybot\ers\intent_store.py`.

---

Now I'll return the plan fragment directly as my final response.

## Sub-slice S4.6d: BLACKLIST durable table + the ERSController seam + the whole-slice e2e

> **Preconditions (landed by S4.6a/b/c before this sub-slice runs):** `src/polybot/ers/telegram_auth.py` (`RawMessage`, `AuthResult`, `TelegramTransport`, `SecretHolder`, `CommandAuth`, `_COMMAND_SET`, `canonical_message`, `compute_mac`, the five `REASON_*` refusal constants); `src/polybot/ers/telegram.py` (`TelegramController` with name-mangled `__ctl/__store/__transport/__auth/__threshold/__notify_fails`, public surface `{drain, notify}`, `drain()`, `notify()`, `__apply` with the BLACKLIST branch a **`NotImplementedError` stub**, and the `__reason_for(command)` helper mapping the six verbs â†’ their reasons); the four new `REASON_L8_*` constants in `src/polybot/ers/safety.py`; `tests/test_ers_telegram.py`. Baseline before S4.6 = **763 passing**. Branch `pol-6-s4.6-telegram` (checked out). All new tests go in `tests/test_ers_blacklist.py` (store) or `tests/test_ers_telegram.py` (verb + seam + e2e). Module-level helpers are copied per file (no conftest/fixtures beyond `tmp_path`/`monkeypatch`).

---

### Task D1: The `blacklist` durable table â€” round-trip record/read ORDER BY bl_id

**Files:**
- Test: `tests/test_ers_blacklist.py` (Create)
- Modify: `src/polybot/ers/intent_store.py` â€” add the `CREATE TABLE IF NOT EXISTS blacklist` DDL block inside `__init__` (before the single `self._conn.commit()` at line 138) + `record_blacklist` / `blacklist_log` methods (after `flow_log`, ~line 264)

- [ ] **Step 1: Write the failing test** (complete python code)

```python
"""S4.6d (POL-6): the durable append-only `blacklist` table on IntentStore.

The store is DUMB -- record_blacklist records ANY target_kind string (the
TelegramController.__apply validates the kind and raises BEFORE calling; the store
just records). Append-only + the shared monotonic stamp + commit-per-write, mirroring
record_fill/record_op_event. Helpers copied per file per convention (no conftest)."""

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_blacklist_round_trips_wallet_market_source_in_bl_id_order(tmp_path):
    # Kills: dropping the table/method, wrong ORDER BY (not bl_id), or column mis-mapping
    # (target_kind/target_value swapped). Three kinds recorded in a fixed order come back in
    # that exact order with a monotonic `at`.
    with _store(str(tmp_path / "i.db")) as store:
        store.record_blacklist(target_kind="wallet", target_value="0xabc")
        store.record_blacklist(target_kind="market", target_value="m-42")
        store.record_blacklist(target_kind="source", target_value="rss-7")

        rows = store.blacklist_log()
        assert [(r["target_kind"], r["target_value"]) for r in rows] == [
            ("wallet", "0xabc"), ("market", "m-42"), ("source", "rss-7")]
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 3 and ats[0] > 0
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py -o addopts="" -q'
```
Expected failure: `AttributeError: 'IntentStore' object has no attribute 'record_blacklist'`.

- [ ] **Step 3: Minimal implementation** (complete python code)

In `src/polybot/ers/intent_store.py`, insert this DDL block inside `__init__` immediately **before** the final `self._conn.commit()` (currently line 138), mirroring the `fills`/`flow_journal` blocks:

```python
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                bl_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at           INTEGER NOT NULL,
                target_kind  TEXT    NOT NULL,
                target_value TEXT    NOT NULL
            )
            """
        )
```

And add these two methods after `flow_log` (~line 264, before `close`):

```python
    def record_blacklist(self, *, target_kind, target_value):
        """Append an IMMUTABLE blacklist row (S4.6d). The store is DUMB: it records ANY
        target_kind string -- the TelegramController.__apply validates the kind in
        {wallet, market, source} and raises BEFORE calling this. Append-only + the shared
        monotonic stamp; commit per write (mirrors record_op_event / record_fill)."""
        self._conn.execute(
            "INSERT INTO blacklist (at, target_kind, target_value) VALUES (?, ?, ?)",
            (self._stamper.stamp(), target_kind, target_value),
        )
        self._conn.commit()

    def blacklist_log(self):
        rows = self._conn.execute(
            "SELECT at, target_kind, target_value FROM blacklist ORDER BY bl_id"
        ).fetchall()
        return [{"at": r[0], "target_kind": r[1], "target_value": r[2]} for r in rows]
```

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **764** passing (763 + this one).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/intent_store.py tests/test_ers_blacklist.py && git commit -m "S4.6d D1: durable append-only blacklist table -- record_blacklist/blacklist_log round-trip ORDER BY bl_id, dumb store records any kind"'
```

---

### Task D2: The `blacklist` table is DUMB (records an unknown kind verbatim)

**Files:**
- Test: `tests/test_ers_blacklist.py` (Modify â€” append)
- Modify: none (asserts the D1 store contract; expected GREEN from birth)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_blacklist.py`:

```python
def test_record_blacklist_is_dumb_and_records_an_unknown_kind_verbatim(tmp_path):
    # DESIGN Fork 2 + the pinned contract: the store does NOT validate target_kind (kind
    # validation lives in TelegramController.__apply, which raises BEFORE calling). Proves the
    # store persists a kind OUTSIDE {wallet,market,source} verbatim. Kills: sneaking a
    # kind-whitelist ValueError into record_blacklist (which would move policy into the store
    # and break the "dumb store" contract D3 relies on).
    with _store(str(tmp_path / "i.db")) as store:
        store.record_blacklist(target_kind="banana", target_value="whatever")
        rows = store.blacklist_log()
        assert [(r["target_kind"], r["target_value"]) for r in rows] == [
            ("banana", "whatever")]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py::test_record_blacklist_is_dumb_and_records_an_unknown_kind_verbatim -o addopts="" -q'
```
Expected: **GREEN from birth** â€” it pins the D1 no-validation contract (it is the boundary partner of D5's `__apply`-raises test, which lives on the controller side). It fails RED only against a mutant that adds a kind-whitelist to the store. Confirm by temporarily inserting `if target_kind not in {"wallet","market","source"}: raise ValueError` at the top of `record_blacklist`, running, seeing `Failed: DID NOT RAISE`-style `ValueError`, then reverting and sweeping pycache:
```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && find . -name __pycache__ -type d -prune -exec rm -rf {} +'
```

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” D1's `record_blacklist` already satisfies this (no validation). If you added the temporary whitelist in Step 2, ensure it is reverted so the store stays dumb.

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **765** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_blacklist.py && git commit -m "S4.6d D2: pin the dumb-store contract -- record_blacklist persists an unknown kind verbatim (kind policy lives in __apply, not the store)"'
```

---

### Task D3: The `blacklist` table persists across restart (close-and-reopen)

**Files:**
- Test: `tests/test_ers_blacklist.py` (Modify â€” append)
- Modify: none (asserts D1 durability; expected GREEN from birth)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_blacklist.py`:

```python
def test_blacklist_persists_across_restart_and_new_rows_append_after(tmp_path):
    # Append-only + committed: a blacklist row survives a process restart and a FRESH stamper,
    # and a new row after restart appends AFTER the persisted one (bl_id ordering, not the
    # per-process stamp clock). Mirrors test_op_audit_log_persists_across_restart.
    # Kills: a missing commit, an in-memory-only set, or CREATE TABLE (without IF NOT EXISTS)
    # nuking the persisted rows on reopen.
    db = str(tmp_path / "i.db")
    with _store(db) as store:
        store.record_blacklist(target_kind="wallet", target_value="0xabc")
    with _store(db) as reopened:            # process restart, new stamper
        rows = reopened.blacklist_log()
        assert len(rows) == 1 and rows[0]["target_value"] == "0xabc"
        reopened.record_blacklist(target_kind="market", target_value="m-9")
        assert [(r["target_kind"], r["target_value"]) for r in reopened.blacklist_log()] == [
            ("wallet", "0xabc"), ("market", "m-9")]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py::test_blacklist_persists_across_restart_and_new_rows_append_after -o addopts="" -q'
```
Expected: **GREEN from birth** â€” pins D1's commit-per-write + append-only durability. It fails RED against a mutant that drops the `self._conn.commit()` in `record_blacklist` or replaces the table with `CREATE TABLE blacklist` (no `IF NOT EXISTS`). Confirm by temporarily deleting the `commit()` line in `record_blacklist`, running, seeing `assert len(rows) == 1` fail with `0`, then reverting + pycache sweep.

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” D1 already commits per write and uses `IF NOT EXISTS`.

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_blacklist.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **766** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_blacklist.py && git commit -m "S4.6d D3: blacklist rows persist across restart -- committed + append-only, new rows append after the persisted ones by bl_id"'
```

---

### Task D4: `__apply` BLACKLIST â€” a valid `"kind:value"` payload records to the store + audits `l8_blacklist`

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; reuse the file's existing helpers `_store`, `_signed`, the fake transport double, `CommandAuth` wiring from S4.6a/b)
- Modify: `src/polybot/ers/telegram.py` â€” replace the BLACKLIST `NotImplementedError` stub in `__apply` with the real parse+record

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`. (Helpers below are copied per this task in case the S4.6b file laid them out differently; if `_store`/`_signed`/`_FakeTransport`/`_auth` already exist verbatim in the file, drop the redundant copies and reuse them.)

```python
# --- S4.6d: the BLACKLIST verb (replacing the S4.6b NotImplementedError stub) -----------------
import hmac as _hmac_d
from polybot.core.clock import MonotonicStamper as _Stamper_d
from polybot.ers.intent_store import IntentStore as _IntentStore_d
from polybot.ers import telegram_auth as _ta_d
from polybot.ers.telegram import TelegramController as _TC_d


def _store_d(tmp_path):
    return _IntentStore_d(str(tmp_path / "i.db"), _Stamper_d())


_SECRET_D = b"s4.6d-secret"


def _signed_d(chat_id, command, payload, nonce, secret=_SECRET_D):
    # Build a VALID signed RawMessage: sig = compute_mac(canonical over the sig-less message).
    unsigned = _ta_d.RawMessage(chat_id=chat_id, command=command, payload=payload,
                                nonce=nonce, sig=b"")
    sig = _ta_d.compute_mac(_ta_d.canonical_message(unsigned), secret)
    return _ta_d.RawMessage(chat_id=chat_id, command=command, payload=payload,
                            nonce=nonce, sig=sig)


class _FakeTransport_d:
    """Duck-typed TelegramTransport: poll() drains a FIFO queue of RawMessages; send() records."""
    def __init__(self, inbound=()):
        self._inbound = list(inbound)
        self.sent = []
        self.send_result = True

    def poll(self):
        out, self._inbound = self._inbound, []
        return out

    def send(self, text):
        self.sent.append(text)
        return self.send_result


def _auth_d(chat_id="ops"):
    return _ta_d.CommandAuth(allowlist={chat_id: "operator"},
                             secret_holder=_ta_d.SecretHolder(_SECRET_D))


def _tc_d(store, ctl, transport, chat_id="ops"):
    return _TC_d(ctl, store, transport, _auth_d(chat_id))


def test_blacklist_verb_records_the_parsed_kind_value_and_audits_l8_blacklist(tmp_path):
    # DESIGN Â§3 row BLACKLIST: an authenticated "kind:value" payload parses to
    # (target_kind, target_value), records a durable blacklist row, and the drain audits
    # kind="l8_command" reason="l8_blacklist" detail="BLACKLIST". Kills: leaving the
    # NotImplementedError stub, mis-parsing the payload, or the wrong audit reason.
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        transport = _FakeTransport_d([_signed_d("ops", "BLACKLIST", "wallet:0xdead", "1")])
        tc = _tc_d(store, ctl, transport)
        tc.drain()
        # The durable row landed with the parsed kind + value.
        assert [(r["target_kind"], r["target_value"]) for r in store.blacklist_log()] == [
            ("wallet", "0xdead")]
        # The drain audited exactly the l8_command / l8_blacklist row for BLACKLIST.
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("l8_command", "l8_blacklist", "BLACKLIST")]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_blacklist_verb_records_the_parsed_kind_value_and_audits_l8_blacklist -o addopts="" -q'
```
Expected failure: the S4.6b BLACKLIST stub raises `NotImplementedError`, which `drain`'s per-message isolation catches and audits as `("l8_command", "l8_apply_error", "BLACKLIST:...")` â€” so `blacklist_log()` is empty and the op-audit assertion mismatches (`l8_apply_error` != `l8_blacklist`, and no blacklist row).

- [ ] **Step 3: Minimal implementation** (complete python code)

In `src/polybot/ers/telegram.py`, replace the BLACKLIST branch stub in `__apply` (the `raise NotImplementedError(...)` placed by S4.6b) with the real parse + record. The branch becomes:

```python
        elif result.command == "BLACKLIST":
            # Payload is the already-neutralized "kind:value" (split on the FIRST colon so a
            # value may itself contain colons). An unknown kind raises ValueError, caught by
            # drain's per-message isolation -> l8_apply_error audit, op-state untouched. The
            # store is dumb (records any kind); the KIND policy lives HERE.
            target_kind, _, target_value = result.payload.partition(":")
            if target_kind not in ("wallet", "market", "source"):
                raise ValueError(f"unknown blacklist kind: {target_kind!r}")
            self.__store.record_blacklist(target_kind=target_kind, target_value=target_value)
```

(Note: inside the class body `self.__store` name-mangles to `self._TelegramController__store` â€” write it as `self.__store` in source, exactly as the other verbs reference `self.__ctl`.)

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **767** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/telegram.py tests/test_ers_telegram.py && git commit -m "S4.6d D4: complete the BLACKLIST verb -- parse neutralized kind:value, record durable row, audit l8_command/l8_blacklist"'
```

---

### Task D5: `__apply` BLACKLIST â€” an unknown kind raises â†’ drain isolates it (`l8_apply_error`), op-state + store untouched

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; reuse D4 helpers)
- Modify: none (asserts D4's `__apply` guard + the S4.6b drain isolation; expected GREEN from birth)

- [ ] **Step 1: Write the failing test** (complete python code)

Boundary partner of D4 (valid kind accepts / invalid kind refuses). Append to `tests/test_ers_telegram.py`:

```python
def test_blacklist_unknown_kind_is_isolated_as_l8_apply_error_and_touches_no_state(tmp_path):
    # Refuse-partner of D4: an AUTHENTICATED BLACKLIST with a kind outside {wallet,market,
    # source} makes __apply raise ValueError, which drain's per-message isolation catches +
    # audits kind="l8_command" reason="l8_apply_error" detail="BLACKLIST:<exc>". NO blacklist
    # row is written and the op-state is untouched (still boot HALTED). Kills: over-widening
    # the kind whitelist to accept the bad kind, or letting the raise escape drain (which
    # would crash the runloop), or recording a row before the guard.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        transport = _FakeTransport_d([_signed_d("ops", "BLACKLIST", "banana:x", "1")])
        tc = _tc_d(store, ctl, transport)
        tc.drain()                                   # must NOT raise
        assert store.blacklist_log() == []           # nothing recorded
        assert ctl.state() == _safety.HALTED         # op-state untouched
        rows = store.op_audit_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "l8_command"
        assert rows[0]["reason"] == "l8_apply_error"
        assert rows[0]["detail"].startswith("BLACKLIST:")
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_blacklist_unknown_kind_is_isolated_as_l8_apply_error_and_touches_no_state -o addopts="" -q'
```
Expected: **GREEN from birth** given D4's guard + the S4.6b drain isolation. It fails RED against a mutant that widens the kind whitelist (D4's `if target_kind not in (...)`) to accept `banana` â€” then a row would be recorded and the audit reason would be `l8_blacklist`. Confirm by temporarily adding `"banana"` to D4's tuple, running, seeing the assertions fail, then reverting + pycache sweep.

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” D4's ValueError guard + the S4.6b `drain` per-message `try/except` already produce this. (This task's value is the refuse-side boundary pin + the mutation kill.)

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **768** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6d D5: BLACKLIST unknown-kind boundary -- __apply raises, drain isolates as l8_apply_error, no row, op-state untouched"'
```

---

### Task D6: `ERSController(telegram=)` seam â€” `telegram=None` default leaves the cycle byte-for-byte as today

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; mirrors `test_lossbreakers_none_default_leaves_the_cycle_exactly_as_today`)
- Modify: `src/polybot/ers/controller.py` â€” add `telegram=None` kwarg after `lossbreakers=None` (line 25) + `self._telegram = telegram` (after line 48)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`:

```python
# --- S4.6d: the ERSController(telegram=) seam -------------------------------------------------
from polybot.ers import safety as _safety_seam
from polybot.ers.controller import ERSController as _ERS_seam
from polybot.ers.safety import SafetyController as _SC_seam
from polybot.ers.caps import RiskCaps as _RC_seam
from polybot.ers.service import PaperSigner as _PS_seam
from polybot.ingestion.orderbook import LocalBook as _LB_seam

_P_seam = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
               max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
               resolution_summary="", thesis="", citations=())


def _book_seam(ask, *, size="1000", bid="0.01"):
    book = _LB_seam()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


def test_telegram_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Dormant-by-default: an ERSController WITHOUT the telegram kwarg (None) trades exactly as
    # before S4.6 -- the intent ACCEPTs, no extra audit rows beyond the setup state_change.
    # Expected GREEN from birth (the 763 baseline is the wider proof). Mirrors
    # test_lossbreakers_none_default_leaves_the_cycle_exactly_as_today. Kills: draining/acting
    # when the seam is None, or requiring the kwarg.
    with _store_d(tmp_path) as store:
        ctl = _SC_seam(caps=_RC_seam(), store=store, clock=lambda: 0)
        ctl.set_state(_safety_seam.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P_seam)
        signer = _PS_seam()
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=_RC_seam(),
                       signer=signer, controller=ctl, clock=lambda: 0)   # telegram unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_telegram_none_default_leaves_the_cycle_exactly_as_today -o addopts="" -q'
```
Expected failure: `TypeError: __init__() got an unexpected keyword argument` is **not** what fires (the test doesn't pass `telegram`); instead this test is GREEN even before the seam exists (it exercises no telegram behavior). To make it a genuine REDâ†’GREEN for the seam, run D7 first-fail (which passes `telegram=`) â€” but keep this test as the None-default pin. If you prefer strict RED here, temporarily add `telegram=` to the constructor call and assert the kwarg is accepted; the canonical approach (matching the lossbreaker precedent) is: this test is **GREEN from birth** and the RED is owned by D7. Note it accordingly and proceed.

- [ ] **Step 3: Minimal implementation** (complete python code)

In `src/polybot/ers/controller.py`, extend the constructor signature â€” change line 25/26 from:

```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 clock):
```
to add `telegram=None` **after** `lossbreakers=None` (keyword-only, before `clock`):

```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 telegram=None, clock):
```

And store it â€” after line 48 (`self._lossbreakers = lossbreakers`) add:

```python
        # telegram (S4.6d seam): the opt-in L8 TelegramController drained at the TOP of
        # run_cycle (ahead of even beat/anomaly) so an operator KILL dominates the cycle.
        # telegram=None (the default) == pre-S4.6 byte-for-byte.
        self._telegram = telegram
```

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **769** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_telegram.py && git commit -m "S4.6d D6: ERSController telegram= seam (stored, None default) -- telegram=None leaves the cycle byte-for-byte as today"'
```

---

### Task D7: `run_cycle` drains at the TOP â€” an authenticated KILL halts before any intent processes (drain-at-top dominance)

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; mirrors `test_run_cycle_starts_halted_and_blocks` for the "blocked â†’ REJECTED, placed==[]" shape)
- Modify: `src/polybot/ers/controller.py` â€” `run_cycle`: add the drain as the FIRST statement, before the `if self._heartbeat is not None:` guard (currently line 62)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`. This wires a REAL `TelegramController` (over the D4 fake transport + `CommandAuth`) into the controller's `telegram=` seam:

```python
def test_run_cycle_drains_first_so_an_authenticated_kill_halts_before_any_intent_processes(tmp_path):
    # DESIGN Â§2 step 0 + invariant 7 (DOMINANCE): drain is run_cycle's FIRST step, so a queued
    # authenticated KILL flips the loop HALTED at the top -- and the SAME cycle's process_pending
    # then REJECTs the pending intent under l8_kill and places NOTHING (the S4.4/S4.7 drain-at-top
    # pattern, mirroring test_run_cycle_starts_halted_and_blocks). Kills: draining AFTER
    # beat/anomaly/process_pending (the KILL would land a cycle late and the intent would ACCEPT
    # first), or not wiring the seam into run_cycle at all.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")   # a LIVE loop
        store.propose_trade("i1", **_P_seam)
        signer = PaperSigner()
        transport = _FakeTransport_d([_signed_d("ops", "KILL", "", "1")])
        tc = _tc_d(store, ctl, transport)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, clock=lambda: 0)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED                        # KILL applied at the top
        assert store.get("i1").status == "REJECTED"                 # ...before the intent processed
        assert store.get("i1").decision_reason == "l8_kill"         # under the KILL's stored reason
        assert signer.placed == []                                  # NOTHING was placed this cycle
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_run_cycle_drains_first_so_an_authenticated_kill_halts_before_any_intent_processes -o addopts="" -q'
```
Expected failure: with the seam stored (D6) but **not yet drained in `run_cycle`**, the KILL never applies this cycle â€” the loop stays RUNNING, `i1` ACCEPTs, and the asserts fail (`ctl.state()` is `RUNNING`, `i1.status` is `ACCEPTED`, `signer.placed == [{...}]`).

- [ ] **Step 3: Minimal implementation** (complete python code)

In `src/polybot/ers/controller.py`, make the drain the **first** statement of `run_cycle`. Change the top of the method (currently starting at line 62 `if self._heartbeat is not None:`) to prepend:

```python
        # S4.6d: drain authenticated Telegram commands FIRST -- ahead of even the heartbeat
        # beat / L5 anomaly / loss consults -- so an operator KILL dominates THIS cycle (the
        # HALTED verdict then blocks every pending intent). telegram=None == today (no drain).
        if self._telegram is not None:
            self._telegram.drain()
        if self._heartbeat is not None:
            self._heartbeat.beat()
```

(Everything below the beat guard stays byte-identical.)

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **770** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_telegram.py && git commit -m "S4.6d D7: run_cycle drains at the TOP -- an authenticated KILL halts before any intent processes (dominance), placed==[] under l8_kill"'
```

---

### Task D8: The drain runs BEFORE `heartbeat.beat()` (drain-ahead-of-beat ordering)

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append)
- Modify: none (asserts D7's ordering; expected GREEN from birth)

- [ ] **Step 1: Write the failing test** (complete python code)

The contract says the drain is the FIRST statement, *before* `heartbeat.beat()`. Pin that ordering with a spy that observes op-state at beat-time. Append to `tests/test_ers_telegram.py`:

```python
class _StateSnoopingHeartbeat_d:
    """A heartbeat that records the controller op-state AT THE MOMENT beat() is called --
    proves the KILL drain ran (HALTED) BEFORE the beat, per the DESIGN Â§2 step-0-before-step-1
    ordering."""
    def __init__(self, ctl):
        self._ctl = ctl
        self.state_at_beat = []

    def beat(self):
        self.state_at_beat.append(self._ctl.state())


def test_drain_runs_before_the_heartbeat_beat(tmp_path):
    # DESIGN Â§2: step 0 (drain) precedes step 1 (beat). A KILL queued this cycle must ALREADY
    # have flipped the loop HALTED by the time beat() fires. Kills: ordering the drain AFTER
    # beat (state_at_beat would read RUNNING), which would let the out-of-band supervisor beat
    # a loop that should already be dying.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        transport = _FakeTransport_d([_signed_d("ops", "KILL", "", "1")])
        tc = _tc_d(store, ctl, transport)
        hb = _StateSnoopingHeartbeat_d(ctl)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, heartbeat=hb, clock=lambda: 0)
        rc.run_cycle()
        assert hb.state_at_beat == [_safety.HALTED]   # the KILL drained BEFORE the beat
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_drain_runs_before_the_heartbeat_beat -o addopts="" -q'
```
Expected: **GREEN from birth** given D7's placement. It fails RED against a mutant that moves the drain below the beat guard. Confirm by temporarily swapping the two `if` blocks in `run_cycle` (beat first, then drain), running, seeing `assert hb.state_at_beat == [RUNNING]`-style failure, then reverting + pycache sweep.

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” D7 already places the drain ahead of the beat.

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **771** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6d D8: pin drain-before-beat ordering -- a KILL is applied HALTED before heartbeat.beat() fires this cycle"'
```

---

### Task D9: WHOLE-SLICE E2E part 1 â€” authenticated KILL dominance + forged-sig / wrong-chat / replay all REFUSED (op-state unchanged)

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; the Â§8.3 e2e over a wired controller, mirroring `test_s4_7_whole_slice_e2e_*`)
- Modify: none (assembles D1â€“D8; expected GREEN)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`. This is the first half of the whole-slice e2e â€” dominance + the three refusals:

```python
def test_s4_6_whole_slice_e2e_kill_dominates_then_forgery_wrongchat_replay_all_refuse(tmp_path):
    # DESIGN-S4.6 Â§8.3 (part 1): a RUNNING loop with a wired TelegramController over a fake
    # transport. Cycle 1: an authenticated KILL halts the loop at the TOP before a proposed
    # intent processes (it REJECTs under l8_kill, nothing placed). Cycle 2: a forged-sig, a
    # wrong-chat, and a replayed KILL are EACH refused (audited l8_refused with the specific
    # reason; op-state stays HALTED; no blacklist/extra state_change). Kills: cross-module
    # mis-wiring (drain not at top, auth gates bypassed, a refusal mutating op-state).
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        transport = _FakeTransport_d()
        tc = _tc_d(store, ctl, transport)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, clock=lambda: 0)

        # Cycle 1: an authenticated KILL dominates -- the pending intent REJECTs under l8_kill.
        store.propose_trade("i1", **_P_seam)
        transport._inbound = [_signed_d("ops", "KILL", "", "1")]
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "l8_kill"
        assert signer.placed == []

        # Cycle 2: three forgeries, each refused with its specific reason; op-state stays HALTED.
        forged = _signed_d("ops", "KILL", "", "2")
        forged = _ta_d.RawMessage(chat_id="ops", command="KILL", payload="", nonce="2",
                                  sig=b"wrongsig")                       # bad HMAC
        wrong_chat = _signed_d("intruder", "KILL", "", "9")             # not on the allowlist
        replay = _signed_d("ops", "KILL", "", "1")                      # nonce 1 <= last-seen 1
        transport._inbound = [forged, wrong_chat, replay]
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED                            # unchanged by any refusal
        refused = [(r["kind"], r["reason"]) for r in store.op_audit_log()
                   if r["kind"] == "l8_refused"]
        assert refused == [
            ("l8_refused", "l8_bad_sig"),
            ("l8_refused", "l8_bad_chat"),
            ("l8_refused", "l8_replay"),
        ]
        # No forgery applied a command: no l8_command row appeared in cycle 2.
        assert [r["kind"] for r in store.op_audit_log()].count("l8_command") == 0
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_s4_6_whole_slice_e2e_kill_dominates_then_forgery_wrongchat_replay_all_refuse -o addopts="" -q'
```
Expected: **GREEN** once D1â€“D8 (and the S4.6a/b/c auth+controller) are in place â€” this test assembles them. If it fails, it exposes a genuine cross-module mis-wiring (e.g. drain not at top, or a `CommandAuth` gate ordering bug from S4.6a â€” verify the refusal reasons come back in gate order `bad_sig`/`bad_chat`/`replay` per the queued messages). Do NOT weaken the test to pass; fix the wiring.

- [ ] **Step 3: Minimal implementation** (complete python code)

None expected â€” this is an assembly test over D1â€“D8. If a real defect surfaces, fix it in the owning module (`controller.py` for ordering; `telegram.py` for drain/audit; note that `telegram_auth.py` is S4.6a and out of this sub-slice's scope â€” flag it for a re-review rather than editing here).

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **772** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6d D9: whole-slice e2e part 1 -- authenticated KILL dominates the cycle; forged-sig/wrong-chat/replay each refused, op-state unchanged"'
```

---

### Task D10: WHOLE-SLICE E2E part 2 â€” RESUME lifts the halt, then LOWER_CAPS tightens the SAME cycle's sizing to per_trade 6

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; continues the Â§8.3 e2e)
- Modify: none (assembles D1â€“D8 + the S4.6a/b/c RESUME + LOWER_CAPS verbs; expected GREEN)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`. This is the second half â€” RESUME + LOWER_CAPS same-cycle sizing (the sizing math: `_P` at ask 0.50 places `stake_usd == per_trade`; default 12, after `step_weekly` â†’ 6):

```python
def test_s4_6_whole_slice_e2e_resume_lifts_the_halt_then_lower_caps_tightens_same_cycle_sizing(tmp_path):
    # DESIGN-S4.6 Â§8.3 (part 2): from a HALTED loop, an authenticated RESUME lifts it back to
    # RUNNING; then in a later cycle an authenticated LOWER_CAPS drains at the TOP and tightens
    # active_caps().per_trade 12 -> 6 (step_weekly) which the SAME cycle's process_pending sizes
    # against -- the intent ACCEPTs clamped to stake_usd == 6 (not 12). Kills: RESUME not
    # reaching RUNNING; LOWER_CAPS not routing through swap_caps/active_caps; the drain landing
    # a cycle late so the OLD caps (per_trade 12) size the intent.
    from decimal import Decimal
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        signer = PaperSigner()
        transport = _FakeTransport_d()
        tc = _tc_d(store, ctl, transport)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, clock=lambda: 0)

        # Cycle 1: RESUME lifts the boot-HALTED loop to RUNNING.
        transport._inbound = [_signed_d("ops", "RESUME", "", "1")]
        rc.run_cycle()
        assert ctl.state() == _safety.RUNNING
        assert ctl.active_caps().per_trade == Decimal("12")           # not yet tightened

        # Cycle 2: LOWER_CAPS drains at the top -> per_trade 6; the SAME cycle sizes i1 to 6.
        store.propose_trade("i1", **_P_seam)
        transport._inbound = [_signed_d("ops", "LOWER_CAPS", "", "2")]
        rc.run_cycle()
        assert ctl.active_caps().per_trade == Decimal("6")            # step_weekly bit active_caps
        assert ctl.active_caps().total_open_risk == Decimal("30")
        assert store.get("i1").status == "ACCEPTED"
        assert signer.placed[-1]["stake_usd"] == Decimal("6")        # clamped by the NEW caps

        # A LOWER_CAPS audit row landed (caps_swap via swap_caps) alongside the l8_command row.
        kinds = [r["kind"] for r in store.op_audit_log()]
        assert kinds.count("caps_swap") == 1
        assert ("l8_command", "l8_lower_caps") in [
            (r["kind"], r["reason"]) for r in store.op_audit_log()]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_s4_6_whole_slice_e2e_resume_lifts_the_halt_then_lower_caps_tightens_same_cycle_sizing -o addopts="" -q'
```
Expected: **GREEN** once D1â€“D8 + the S4.6b RESUME/LOWER_CAPS verbs are in place. The load-bearing assertion is `signer.placed[-1]["stake_usd"] == Decimal("6")` â€” it proves the drain-at-top tightening bites the SAME cycle's sizing (because `process_pending` reads `caps=self._controller.active_caps()` at line 118, AFTER the drain). If it comes back `12`, the drain is landing a cycle late (drain not at top) â€” fix `run_cycle` ordering (D7), do not weaken the assert.

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” assembly over D1â€“D8 + S4.6b. Fix any surfaced defect in its owning module.

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **773** passing.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6d D10: whole-slice e2e part 2 -- RESUME lifts the halt; LOWER_CAPS drains at the top and tightens the SAME cycle sizing to stake 6"'
```

---

### Task D11: WHOLE-SLICE E2E part 3 â€” `alerts_down_threshold` consecutive notify failures halt the loop

**Files:**
- Test: `tests/test_ers_telegram.py` (Modify â€” append; the final Â§8.3 e2e leg over the S4.6c `notify()` alerts-down halt)
- Modify: none (assembles the S4.6c notify counter + halt; expected GREEN)

- [ ] **Step 1: Write the failing test** (complete python code)

Append to `tests/test_ers_telegram.py`. This closes the whole-slice e2e â€” the alerts-down fail-safe. `notify()` is called directly (it is the operator-alert emit path; the loop calls it out-of-band). The fake transport's `send()` returns `False` to simulate a persistent alerts-down condition. Default `alerts_down_threshold=3`:

```python
def test_s4_6_whole_slice_e2e_alerts_down_threshold_notify_failures_halt_the_loop(tmp_path):
    # DESIGN-S4.6 Â§8.3 (part 3) + invariant 5: from a RUNNING loop, alerts_down_threshold (=3)
    # CONSECUTIVE failed notify() sends flip the loop to sticky HALTED(l8_alerts_down) -- the
    # master-design fail-safe. The 2nd failure must NOT yet halt (boundary), the 3rd does.
    # Kills: an off-by-one threshold (halting at 2 or never), or notify() re-raising into the
    # caller (it must be best-effort).
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        transport = _FakeTransport_d()
        transport.send_result = False                     # every send() fails (alerts down)
        tc = _tc_d(store, ctl, transport)                 # default alerts_down_threshold=3

        tc.notify("ping")                                 # fail 1
        tc.notify("ping")                                 # fail 2 -- still under threshold
        assert ctl.state() == _safety.RUNNING             # boundary: 2 < 3, no halt yet
        tc.notify("ping")                                 # fail 3 -- crosses the threshold
        assert ctl.state() == _safety.HALTED              # sticky fail-safe halt
        assert any(r["reason"] == "l8_alerts_down" for r in store.op_audit_log())
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** (exact command + expected failure)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py::test_s4_6_whole_slice_e2e_alerts_down_threshold_notify_failures_halt_the_loop -o addopts="" -q'
```
Expected: **GREEN** once S4.6c's `notify()` + counter + `set_state(HALTED, REASON_L8_ALERTS_DOWN)` are in place. It fails RED against an off-by-one (halt at 2, or `>` vs `>=`) or a non-resetting/over-eager counter. If S4.6c is correct this is an assembly pin; if it exposes a threshold bug, fix it in `telegram.py`.

- [ ] **Step 3: Minimal implementation** (complete python code)

None â€” assembly over S4.6c. Fix any surfaced defect in `telegram.py`.

- [ ] **Step 4: Task file green + FULL suite green** (exact commands)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_telegram.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Full suite: **774** passing. (Final S4.6d count = 763 baseline + 11 new tests across D1â€“D11.)

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_telegram.py && git commit -m "S4.6d D11: whole-slice e2e part 3 -- alerts_down_threshold consecutive notify failures halt the loop (fail-safe), boundary at 2 vs 3"'
```

---

**Sub-slice notes for the implementer / reviewer:**
- **Path correction:** the intent store lives at `src/polybot/ers/intent_store.py` (the brief/contract said "intent_store.py" â€” it is under `ers/`, not `core/`). `MonotonicStamper` is imported from `polybot.core.clock`.
- **Baseline arithmetic:** D adds exactly 11 tests (D1â€“D11), 763 â†’ 774. D2/D3/D5/D6/D8 are GREEN-from-birth boundary/pin tests whose RED is demonstrated via a temporary-mutant + pycache-sweep (per the convention in `test_ers_lossbreaker.py` D12 and `test_ers_anomaly.py`); D1/D4/D7 are genuine REDâ†’GREEN.
- **Sizing math (load-bearing in D10):** `_P_seam` at `_book("0.50")` sizes `stake_usd == caps.per_trade` (worst-case notional for a long). Default `per_trade == 12`; `step_weekly(RiskCaps())` gives `per_trade == 6`, `total_open_risk == 30`. `process_pending` reads `caps=self._controller.active_caps()` (controller.py line ~118) AFTER the drain, so a top-of-cycle LOWER_CAPS bites the same cycle.
- **Sacred surfaces:** `set_state`/`swap_caps`/`verdict` bodies unchanged (D uses them as-is); `propose_trade`/`record_decision`/`record_op_event` bodies unchanged in `intent_store.py` (D only ADDS the `blacklist` table + `record_blacklist`/`blacklist_log`); `run_cycle` gains only the two-line drain-at-top prepend, everything below byte-identical.
- **Two-stage review after D11 (per DESIGN Â§8.4):** spec-compliance pass + a pinned-opus mutation battery (mutate: the drain placement, the `IF NOT EXISTS`, the `commit()` in `record_blacklist`, the D4 kind-whitelist tuple, the `partition(":")` vs `split`, the alerts-down `>=` threshold), with a pycache sweep after every mutation revert; then the final whole-slice review. This CLOSES the S4 safety envelope â€” confirm before push.