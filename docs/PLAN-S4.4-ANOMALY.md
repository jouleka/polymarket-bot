# S4.4 L5 AnomalyMonitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the L5 anomaly kill-switch (S4.4 / POL-6): a `DrawdownBreaker`-shaped `AnomalyMonitor` with six sentinel seams, wired into `ERSController.run_cycle` ahead of `process_pending` — edge-triggered halt-first one-shot `cancel_all`, sticky halts, all shadow-only on the `PaperSigner`.

**Architecture:** New `src/polybot/ers/anomaly.py` (monitor + `ClockSkewSentinel` + `ApiStormSentinel`) + additive `None`-defaulting seams: `ERSController(anomaly=)`, `MarketStream.last_frame_at()`, `make_recon_provider` in `ers/reconcile.py`, 7 tighten-only hashed `RiskCaps` fields, 5 new `REASON_L5_*` constants. The authoritative spec is `docs/DESIGN-S4.4-ANOMALY.md` (§4 = the pinned contract block); this plan implements exactly that.

**Tech Stack:** Python 3.13, pytest, stdlib only (Decimal/dataclasses/deque) — no new dependencies.

---

## Execution notes (READ FIRST — every implementer)

- **Environment:** repo is WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot`, branch `pol-6-s4.4-anomaly` (already checked out). Run tests/git from Windows via `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`; edit files via UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...` (EISDIR = 9p glitch, retry). Tests: `./.venv/bin/pytest` — baseline **556 passing** before Task A1.
- **Strict TDD:** run each Step 2 and OBSERVE the RED (fail for the stated reason) before writing Step 3. One commit per RED→GREEN cycle. **Commit messages: NO Co-Authored-By trailer** (repo convention).
- **SACRED — never touch:** `evaluate_intent`/`validator.py`, `propose_trade`/the `IntentStore` chokepoint, `process_pending`'s signature + decision flow, `MonotonicStamper`, `heartbeat.py`, `supervisor.py`. Extend only via the seams this plan names.
- **Sub-slices run SERIALLY A → B → C → D → E** on the shared branch. Fragments were drafted against the pinned contract: when a Step-3 code block shows surrounding code that has since evolved (an earlier sub-slice added a consult block to `AnomalyMonitor.evaluate`), reconcile against the CURRENT file using the pinned severity order — `l5_clock_skew, l5_recon_mismatch, l5_canary_fail, l5_abnormal_book, l5_api_storm, l5_ws_down` — and NEVER delete an earlier sub-slice's block. If the reconciliation is at all ambiguous, STOP and report NEEDS_CONTEXT rather than improvise.
- **Suite counts:** each task states expected absolute test counts; treat them as per-sub-slice estimates. The authoritative verification is: the named new tests pass, the FULL suite is all green (exit 0), and no test was deleted/skipped. If an absolute count differs but everything is green, proceed; note it in your report.
- **Fail-closed doctrine:** under any ambiguity the correct behavior is DO NOT TRADE + surface the anomaly. When in doubt about a semantic, re-read `docs/DESIGN-S4.4-ANOMALY.md` §3/§6 before asking.

---

## Sub-slice A: S4.4a â€” the spine (l5_* reasons, AnomalyState + monitor skeleton, the `ERSController(anomaly=)` seam)

All tests live in NEW `tests/test_ers_anomaly.py`. Baseline before A1 = **556 passed**; after A11 = **572 passed** (16 new). The real `ClockSkewSentinel` is sub-slice B â€” the spine drives the monitor through a duck-typed `.skewed()` double. Nothing here touches `evaluate_intent`/the validator/`propose_trade`/`process_pending`'s signature; the only source files modified are `src/polybot/ers/safety.py` (constants), NEW `src/polybot/ers/anomaly.py`, and `src/polybot/ers/controller.py` (the pinned seam). Branch: `pol-6-s4.4-anomaly` (already checked out).

---

### Task A1: the five l5_* reason constants

**Files:**
- Modify: `src/polybot/ers/safety.py` (insert after line 38, `REASON_RESTART_RECONCILED = ...`)
- Test: `tests/test_ers_anomaly.py` (NEW file)

- [ ] **Step 1: Write the failing test** â€” create `tests/test_ers_anomaly.py`:

```python
"""L5 AnomalyMonitor -- the anomaly kill-switch spine (S4.4a / POL-6).

Sub-slice A: the l5_* reason constants, the AnomalyState/AnomalyMonitor skeleton driven by a
duck-typed skew-sentinel double (the real ClockSkewSentinel is S4.4b), the fail-closed
raising-seam rule, all-seams-None inertness, and the ERSController anomaly= seam --
edge-triggered halt-first one-shot cancel_all with exact op-audit rows, sticky semantics, and
a raising signer that never unwinds the halt. Clocks are injected; helpers are copied per
file per convention (no conftest)."""


def test_s4_4_l5_reason_constants_exist_with_exact_strings():
    # S4.4a defines the five NET-NEW l5_* reason codes (l5_recon_mismatch already exists from
    # S4.5). Free-form Decision.reason / op-audit strings, NO validator/schema change -- the
    # existing REASON_* convention (mirrors test_s4_5_reason_constants_exist).
    # MUTATION KILLED: renaming any constant or typo-ing its string (the controller reports
    # these verbatim as the halt reason).
    from polybot.ers import safety as _s
    assert _s.REASON_L5_CLOCK_SKEW == "l5_clock_skew"
    assert _s.REASON_L5_ABNORMAL_BOOK == "l5_abnormal_book"
    assert _s.REASON_L5_API_STORM == "l5_api_storm"
    assert _s.REASON_L5_WS_DOWN == "l5_ws_down"
    assert _s.REASON_L5_CANARY_FAIL == "l5_canary_fail"
    # The pre-existing S4.5 constant this slice consumes (guards accidental removal).
    assert _s.REASON_L5_RECON_MISMATCH == "l5_recon_mismatch"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed` â€” `AttributeError: module 'polybot.ers.safety' has no attribute 'REASON_L5_CLOCK_SKEW'`.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/safety.py`, insert directly after line 38 (`REASON_RESTART_RECONCILED = "restart_reconciled"   # ...`):

```python
# --- S4.4 reason codes (NET-NEW; the L5 AnomalyMonitor trigger vocabulary) --------------------
REASON_L5_CLOCK_SKEW = "l5_clock_skew"        # |wall - ntp| beyond tolerance (halts signing)
REASON_L5_ABNORMAL_BOOK = "l5_abnormal_book"  # crossed/locked mid, depth collapse, mid jump
REASON_L5_API_STORM = "l5_api_storm"          # 5xx / auth-failure storm within the window
REASON_L5_WS_DOWN = "l5_ws_down"              # WS silent beyond staleness (None frame = +inf age)
REASON_L5_CANARY_FAIL = "l5_canary_fail"      # signing canary failed/raised -- NEVER blind-retried
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `1 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `557 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_anomaly.py && git commit -m "feat(safety): S4.4a l5_* reason constants (clock_skew/abnormal_book/api_storm/ws_down/canary_fail)"'`

---

### Task A2: `ers/anomaly.py` â€” NONE/HALT vocab + frozen `AnomalyState`

**Files:**
- Create: `src/polybot/ers/anomaly.py`
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append to `tests/test_ers_anomaly.py`:

```python
# --- ers/anomaly.py: module vocab + AnomalyState ----------------------------------------------
import dataclasses

import pytest


def test_anomaly_module_action_vocab_is_none_and_halt_exact_strings():
    # The AnomalyState.action vocabulary, module-constant style mirroring breaker.py's
    # NONE/FREEZE_ADDS/FLATTEN. MUTATION KILLED: changing either constant's string (the
    # controller compares state.action == HALT by value).
    from polybot.ers import anomaly as _a
    assert _a.NONE == "NONE"
    assert _a.HALT == "HALT"


def test_anomaly_state_is_a_frozen_dataclass_with_action_and_triggers():
    # AnomalyState is immutable evidence (mirrors BreakerState: action + provenance tuple).
    # MUTATION KILLED: dropping frozen=True, or renaming the action/triggers fields.
    from polybot.ers.anomaly import HALT, AnomalyState
    state = AnomalyState(action=HALT, triggers=("l5_clock_skew",))
    assert state.action == "HALT"
    assert state.triggers == ("l5_clock_skew",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.action = "NONE"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `2 failed, 1 passed` â€” both new tests raise `ModuleNotFoundError: No module named 'polybot.ers.anomaly'`.

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/anomaly.py` (NOTE: this module must never contain the strings `set_state` or the resume-state name â€” Task A6 pins that structurally):

```python
"""L5 AnomalyMonitor -- the anomaly kill-switch (S4.4 / POL-6).

DrawdownBreaker-shaped: constructed with caps + an injected 0-arg ``clock`` returning float
monotonic SECONDS + one seam per trigger; every seam defaults to None == that trigger is
dormant (the data-gated pattern), so a bare monitor never fires. FAIL CLOSED: a wired seam
that RAISES inside evaluate fires its own trigger -- it never masks and never propagates.
STICKY (Fork 1): this module only ever REPORTS anomalies; recovery is operator-owned, so
nothing here touches the op-state machine.
"""

from dataclasses import dataclass

NONE = "NONE"
HALT = "HALT"


@dataclass(frozen=True)
class AnomalyState:
    action: str      # NONE | HALT
    triggers: tuple  # the l5_* reason strings that fired, severity order; () when NONE
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `3 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `559 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly.py && git commit -m "feat(anomaly): S4.4a module vocab NONE/HALT + frozen AnomalyState"'`

---

### Task A3: `AnomalyMonitor` skeleton â€” all seams None == inert

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (append after `AnomalyState`)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_monitor_with_all_seams_none_is_inert_and_returns_action_none():
    # Dormant-by-default (design Â§6.5): a bare AnomalyMonitor(caps, clock=...) with every
    # seam left None must NEVER fire, whatever positions/books look like -- the data-gated
    # pattern. MUTATION KILLED: any seam consult that fires when its seam is None (e.g.
    # dropping an `is not None` guard).
    from polybot.ers.anomaly import NONE as A_NONE, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0)
    state = monitor.evaluate((), {}.get)
    assert state.action == A_NONE
    assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 3 passed` â€” `ImportError: cannot import name 'AnomalyMonitor' from 'polybot.ers.anomaly'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/anomaly.py`:

```python
class AnomalyMonitor:
    """evaluate(positions, book_for) -> AnomalyState, once per controller cycle. Consults the
    wired seams in pinned severity order and collects ALL firing triggers; triggers[0] is the
    halt reason the consumer reports. S4.4a wires the skew seam; S4.4b-e add the rest."""

    def __init__(self, caps, *, clock, ws_last_frame_at=None, api_sentinel=None,
                 skew_sentinel=None, recon_provider=None, canary=None, dispute_flagger=None):
        self._caps = caps
        self._clock = clock                        # 0-arg -> float monotonic SECONDS
        self._ws_last_frame_at = ws_last_frame_at  # 0-arg -> stamper-domain ns | None (S4.4d)
        self._api_sentinel = api_sentinel          # ApiStormSentinel (S4.4b)
        self._skew_sentinel = skew_sentinel        # duck-typed .skewed() -> bool
        self._recon_provider = recon_provider      # 0-arg -> ReconResult | None (S4.4e)
        self._canary = canary                      # 0-arg -> bool (S4.4e scheduler)
        # DEFERRED seam (UMA dispute watch, design Â§3): stored + documented, NOT consulted
        # in S4.4 -- no dispute-ingestion source exists yet.
        self._dispute_flagger = dispute_flagger
        self._canary_last_run = None               # float | None: the canary scheduler's memory

    def evaluate(self, positions, book_for):
        triggers = []
        if not triggers:
            return AnomalyState(NONE, ())
        return AnomalyState(HALT, tuple(triggers))
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `4 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `560 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly.py && git commit -m "feat(anomaly): AnomalyMonitor skeleton -- all seams None == inert"'`

---

### Task A4: the skew-sentinel seam â€” fire / no-fire pair

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (the `evaluate` method + one import)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
class _SkewDouble:
    """Duck-typed skew-sentinel double (.skewed() -> bool); the real ClockSkewSentinel lands
    in S4.4b. Mutable so the sticky tests can CLEAR the anomaly between cycles."""

    def __init__(self, skewed):
        self.is_skewed = skewed

    def skewed(self):
        return self.is_skewed


def test_truthy_skew_sentinel_fires_halt_with_the_l5_clock_skew_trigger():
    # Fire side of the skew boundary pair. MUTATION KILLED: dropping the skew consult, or
    # appending the wrong reason string (the controller reports triggers[0] verbatim as the
    # set_state reason).
    from polybot.ers.anomaly import HALT as A_HALT, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_SkewDouble(True))
    state = monitor.evaluate((), {}.get)
    assert state.action == A_HALT
    assert state.triggers == ("l5_clock_skew",)


def test_falsy_skew_sentinel_keeps_action_none_with_no_triggers():
    # No-fire side of the pair (explicit boundary partner of the test above). MUTATION
    # KILLED: inverting the .skewed() check (`if not ...skewed()`).
    from polybot.ers.anomaly import NONE as A_NONE, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_SkewDouble(False))
    state = monitor.evaluate((), {}.get)
    assert state.action == A_NONE
    assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 5 passed` â€” the truthy test fails `AssertionError: assert 'NONE' == 'HALT'` (the skeleton never consults the seam). The falsy test is green from birth (the skeleton is inert) â€” its kill target is the inversion mutation, verified by the pair.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/anomaly.py`, add below the existing `from dataclasses import dataclass`:

```python
from polybot.ers.safety import REASON_L5_CLOCK_SKEW
```

and replace the whole `evaluate` method with:

```python
    def evaluate(self, positions, book_for):
        triggers = []
        # Severity slot 1 of the pinned order (skew, recon, canary, book, api, ws): clock
        # skew. Slots 2-6 land in S4.4b-e.
        if self._skew_sentinel is not None:
            if self._skew_sentinel.skewed():
                triggers.append(REASON_L5_CLOCK_SKEW)
        if not triggers:
            return AnomalyState(NONE, ())
        return AnomalyState(HALT, tuple(triggers))
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `6 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `562 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly.py && git commit -m "feat(anomaly): skew-sentinel seam consult (fire/no-fire boundary pair)"'`

---

### Task A5: the fail-closed raising-seam rule

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (the skew consult inside `evaluate`)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
class _RaisingSkew:
    """A wired sentinel that explodes -- per the FAIL-CLOSED SEAM RULE this IS the anomaly."""

    def skewed(self):
        raise RuntimeError("skew sentinel exploded")


def test_raising_skew_sentinel_fires_its_own_trigger_instead_of_propagating():
    # FAIL-CLOSED SEAM RULE (design Â§6.4): a wired sentinel that RAISES inside evaluate fires
    # its own trigger -- append + continue; never mask, never propagate. MUTATION KILLED:
    # letting the exception escape evaluate, or except-ing to a silent `pass`.
    from polybot.ers.anomaly import HALT as A_HALT, AnomalyMonitor
    from polybot.ers.caps import RiskCaps
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_RaisingSkew())
    state = monitor.evaluate((), {}.get)   # must NOT raise
    assert state.action == A_HALT
    assert state.triggers == ("l5_clock_skew",)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 6 passed` â€” the new test errors with `RuntimeError: skew sentinel exploded` (the consult is unwrapped).

- [ ] **Step 3: Minimal implementation** â€” replace the skew consult inside `evaluate` with the wrapped form (full method):

```python
    def evaluate(self, positions, book_for):
        triggers = []
        # Severity slot 1 of the pinned order (skew, recon, canary, book, api, ws): clock
        # skew. Slots 2-6 land in S4.4b-e.
        if self._skew_sentinel is not None:
            try:
                if self._skew_sentinel.skewed():
                    triggers.append(REASON_L5_CLOCK_SKEW)
            except Exception:
                # FAIL-CLOSED SEAM RULE: a raising sentinel IS the anomaly -- fire this
                # seam's trigger and continue to the next seam; never mask, never propagate.
                triggers.append(REASON_L5_CLOCK_SKEW)
        if not triggers:
            return AnomalyState(NONE, ())
        return AnomalyState(HALT, tuple(triggers))
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `7 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `563 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly.py && git commit -m "feat(anomaly): fail-closed raising-seam rule on the skew consult"'`

---

### Task A6: the STICKY structural pin â€” anomaly.py never names `set_state`/the resume state

**Files:**
- Test: `tests/test_ers_anomaly.py` (append; no production change â€” a source-scan pin in the FOLLOW-off style)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_anomaly_module_source_never_references_running_or_set_state():
    # STICKY pin (design Â§6.1, Fork 1; the detectors FOLLOW-off structural style): nothing in
    # ers/anomaly.py may ever transition op-state or even NAME the resume state -- the ONLY
    # automatic HALTED->resume in the system stays RestartReconciler's clean boot-reconcile.
    # MUTATION KILLED: any auto-resume (or any op-state mutation at all) creeping into the
    # monitor module.
    from pathlib import Path

    from polybot.ers import anomaly as _a
    src = Path(_a.__file__).read_text(encoding="utf-8")
    assert "set_state" not in src
    assert "RUNNING" not in src
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” a pin test is green from birth, so prove it CAN fail via a mutation check (append an offending line, watch the fail, auto-revert):

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && echo "# MUTATION: set_state" >> src/polybot/ers/anomaly.py && ./.venv/bin/pytest tests/test_ers_anomaly.py -q; git checkout -- src/polybot/ers/anomaly.py'`

Expected: `1 failed, 7 passed` â€” `AssertionError` on `assert "set_state" not in src`. The trailing `git checkout` restores the committed module (no stray MUTATION markers may survive).

- [ ] **Step 3: Minimal implementation** â€” none required: the A2-A5 module was written without those strings by construction. Verify the tree is clean of the mutation:

```python
# (no production code for this task -- the pin is the deliverable)
```

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git status --porcelain src/polybot/ers/anomaly.py'` â†’ empty output (module unmodified).

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `8 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `564 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly.py && git commit -m "test(anomaly): sticky structural pin -- module source never names set_state/the resume state"'`

---

### Task A7: the `ERSController(anomaly=)` seam â€” halt-FIRST one-shot cancel_all + exact audit rows

**Files:**
- Modify: `src/polybot/ers/controller.py` (import at line 16; `__init__` signature lines 20-21; seam assignment after line 37; `run_cycle` lines 47-56)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append (this block also introduces the per-file helper idioms copied from `tests/test_ers_controller.py` / `tests/test_ers_safety.py`):

```python
# --- ERSController anomaly= seam (the run_cycle kill-path wiring) -----------------------------
from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ingestion.orderbook import LocalBook


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _monitor(skew):
    from polybot.ers.anomaly import AnomalyMonitor
    return AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=skew)


def _rc(store, ctl, signer, *, anomaly):
    return ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                         signer=signer, controller=ctl, anomaly=anomaly, clock=lambda: 0)


class _StateSnoopingSigner(PaperSigner):
    """PaperSigner that records the op-state AT THE MOMENT cancel_all is called -- proves the
    gate closed (HALTED) BEFORE the de-risk fired."""

    def __init__(self, ctl):
        super().__init__()
        self._ctl = ctl
        self.state_at_cancel = []

    def cancel_all(self):
        self.state_at_cancel.append(self._ctl.state())
        super().cancel_all()


def test_new_anomaly_from_running_halts_first_then_cancels_once_with_exact_audit_rows(tmp_path):
    # Design Â§2 / invariant 2: on a NEW anomaly while RUNNING the controller (1) closes the
    # gate FIRST -- set_state(HALTED, reason=state.triggers[0]), audited by set_state -- THEN
    # (2) fires exactly ONE cancel_all and (3) writes exactly one kind="cancel_all" op-audit
    # row with reason=triggers[0], detail=",".join(triggers).
    # MUTATIONS KILLED: swapping the halt/cancel order (state_at_cancel would read RUNNING);
    # double-firing cancel_all; wrong reason/detail strings on either audit row.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = _StateSnoopingSigner(ctl)
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))

        rc.run_cycle()

        assert ctl.state() == _safety.HALTED               # the gate is closed...
        assert signer.state_at_cancel == [_safety.HALTED]  # ...and was ALREADY closed at cancel time
        assert len(signer.cancelled_all) == 1              # one-shot de-risk
        rows = store.op_audit_log()
        # EXACT op-audit sequence: setup transition, then halt-first, then the de-risk row.
        assert [(r["kind"], r["reason"], r["detail"]) for r in rows] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("state_change", "l5_clock_skew", _safety.HALTED),
            ("cancel_all", "l5_clock_skew", "l5_clock_skew"),
        ]


def test_anomaly_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Design Â§6.5 dormant-by-default: an ERSController WITHOUT the anomaly kwarg (the None
    # default) trades exactly as before S4.4 -- ACCEPT, no cancel_all, no anomaly audit rows.
    # Expected GREEN from birth: it pins the seam's None default (the 556-test baseline is
    # the wider proof). MUTATION KILLED: making the seam mandatory, or consulting/de-risking
    # when the monitor is None.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)   # anomaly unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 9 passed` â€” the halt-first test fails with `TypeError: ERSController.__init__() got an unexpected keyword argument 'anomaly'`. (The `anomaly_none_default` pin is green from birth, as documented in its comment.)

- [ ] **Step 3: Minimal implementation** â€” three edits to `src/polybot/ers/controller.py`. Replace line 16:

```python
from polybot.ers.anomaly import HALT
from polybot.ers.safety import HALTED
from polybot.ers.service import process_pending
```

Replace the `__init__` signature (lines 20-21) â€” `anomaly=None` appended after `fill_sink=None` per the pinned contract:

```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, clock):
```

Insert directly after `self._fill_sink = fill_sink` (line 37):

```python
        # anomaly (S4.4a seam): the opt-in L5 AnomalyMonitor consulted each cycle AHEAD of
        # process_pending. anomaly=None (the default) == today's behavior byte-for-byte.
        self._anomaly = anomaly
```

Replace the whole `run_cycle` method (lines 47-56):

```python
    def run_cycle(self):
        """One cadence tick: beat (if wired) -> L5 anomaly consult (if wired) ->
        process_pending(controller=...). Returns the updated portfolio (threaded for the
        next cycle)."""
        if self._heartbeat is not None:
            self._heartbeat.beat()
        if self._anomaly is not None:
            # L5 (S4.4): ALWAYS evaluated when wired (keeps the monitor's per-token
            # prev-state warm every cycle). On HALT: the gate closes FIRST (set_state audits
            # the transition), THEN the one-shot de-risk + its own audit row.
            state = self._anomaly.evaluate(self._portfolio.positions, self._book_for)
            if state.action == HALT:
                self._controller.set_state(HALTED, reason=state.triggers[0])
                self._signer.cancel_all()
                self._store.record_op_event(kind="cancel_all", reason=state.triggers[0],
                                            detail=",".join(state.triggers))
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio, caps=self._caps,
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink)
        return self._portfolio
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `10 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `566 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_anomaly.py && git commit -m "feat(controller): anomaly= seam -- halt-first one-shot cancel_all + exact op-audit rows"'`

---

### Task A8: edge-triggered â€” an already-HALTED loop never re-fires

**Files:**
- Modify: `src/polybot/ers/controller.py` (the anomaly guard inside `run_cycle`)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_already_halted_loop_never_refires_cancel_all_or_state_change(tmp_path):
    # Edge-triggered (design Â§2): the monitor evaluates every cycle, but an ALREADY-HALTED
    # loop is never re-de-risked and never re-audited -- no audit spam, no cancel_all churn
    # against the standing GTD exits. Start = boot HALTED (unclean_restart), anomaly firing.
    # MUTATION KILLED: dropping the op-state edge guard entirely.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)   # boot: HALTED
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert signer.cancelled_all == []      # no de-risk fired from HALTED
        assert store.op_audit_log() == []      # no state_change row, no cancel_all row


def test_anomaly_still_firing_on_the_next_cycle_does_not_refire_the_one_shot(tmp_path):
    # Edge-triggered, cycle 2: after the halt, a STILL-firing anomaly must not re-fire
    # cancel_all or append further audit rows -- exactly one halt + one de-risk, ever.
    # MUTATION KILLED: level-triggered re-firing on every cycle the sentinel stays skewed.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()     # fires: halt + ONE cancel_all
        rc.run_cycle()     # still skewed -- must be a no-op on the kill path
        assert len(signer.cancelled_all) == 1
        kinds = [r["kind"] for r in store.op_audit_log()]
        assert kinds.count("cancel_all") == 1
        assert kinds.count("state_change") == 2    # clean_reconcile + the ONE l5 halt
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `2 failed, 10 passed` â€” `AssertionError: assert [{'cancelled': 'working_entries'}] == []` (the HALTED loop was re-de-risked) and `assert 2 == 1` on the cancel count (level-triggered re-fire).

- [ ] **Step 3: Minimal implementation** â€” in `run_cycle`, replace the guard line

```python
            if state.action == HALT:
```

with:

```python
            # EDGE-triggered: never re-fire on an existing HALTED (no audit spam, no
            # cancel_all churn against the standing GTD exits).
            if state.action == HALT and self._controller.state() != HALTED:
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `12 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `568 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_anomaly.py && git commit -m "feat(controller): edge-triggered anomaly guard -- already-HALTED never re-fires"'`

---

### Task A9: guard boundary â€” FLATTENING is not preempted; PAUSED escalates

**Files:**
- Modify: `src/polybot/ers/controller.py` (tighten the guard to the pinned `in (RUNNING, PAUSED)`; extend the safety import)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_flattening_in_flight_is_not_preempted_by_an_anomaly(tmp_path):
    # Design Â§2: FLATTENING is a STRONGER de-risk already in flight -- the anomaly path must
    # not preempt it. The cycle proceeds to process_pending where the op-FLATTEN verdict
    # de-risks (flatten + cancel working entries) and settles HALTED on its own (I1); the
    # anomaly path contributes NO l5 state_change and NO kind="cancel_all" row.
    # MUTATION KILLED: widening the edge guard to preempt FLATTENING -- which would SKIP the
    # flatten de-risk entirely (strictly riskier).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.FLATTENING, reason=_safety.REASON_OP_FLATTEN)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert len(signer.flattened) == 1          # the op-FLATTEN de-risk ran (empty book OK)
        assert len(signer.cancelled_all) == 1      # from the FLATTEN path ONLY
        rows = store.op_audit_log()
        assert [r["kind"] for r in rows].count("cancel_all") == 0   # anomaly one-shot did NOT fire
        assert not any(r["reason"] == "l5_clock_skew" for r in rows)
        assert ctl.state() == _safety.HALTED       # settled by FLATTENING itself (I1)


def test_paused_loop_escalates_to_halted_on_an_anomaly(tmp_path):
    # Design Â§2: PAUSED is a LIVE loop (blocks new trades only) -- an anomaly must still
    # escalate it to the sticky HALTED + the one-shot de-risk. Expected GREEN once the guard
    # is (RUNNING, PAUSED); it exists to KILL the over-tightened (RUNNING,)-only guard
    # mutation -- verified by the Step-4 mutation check.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
        rows = store.op_audit_log()
        assert ("cancel_all", "l5_clock_skew") in [(r["kind"], r["reason"]) for r in rows]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 13 passed` â€” the FLATTENING test fails `AssertionError: assert 0 == 1` on `len(signer.flattened)`: the A8 `!= HALTED` guard lets the anomaly preempt FLATTENING (it halts first, so `process_pending` never runs the flatten de-risk). The PAUSED test is green from birth (documented in its comment).

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/controller.py`, replace the safety import line with:

```python
from polybot.ers.safety import HALTED, PAUSED, RUNNING
```

and replace the guard with the pinned form:

```python
            # EDGE-triggered: act only from a LIVE loop (RUNNING/PAUSED) -- never re-fire on
            # an existing HALTED (no audit spam, no cancel_all churn against the standing GTD
            # exits) and never preempt FLATTENING (a stronger de-risk already in flight; it
            # settles HALTED on its own).
            if state.action == HALT and self._controller.state() in (RUNNING, PAUSED):
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green** (plus the PAUSED mutation check)

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `14 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `570 passed`

Mutation check for the PAUSED pin: temporarily change `in (RUNNING, PAUSED)` to `in (RUNNING,)` (Edit), run `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ expected `1 failed` (`test_paused_loop_escalates...`, `assert 'PAUSED' == 'HALTED'`); then revert with `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git checkout -- src/polybot/ers/controller.py'` â€” wait: the guard change of Step 3 is not yet committed, so revert the mutation by re-applying Step 3's exact guard line via Edit instead of git checkout. Re-run both commands above â†’ `14 passed` / `570 passed`.

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_anomaly.py && git commit -m "feat(controller): anomaly guard = (RUNNING, PAUSED) -- FLATTENING not preempted, PAUSED escalates"'`

---

### Task A10: sticky after the anomaly clears + the next intent REJECTs with the l5 reason

**Files:**
- Test: `tests/test_ers_anomaly.py` (append; no production change â€” behavior already emerges from A7-A9 + the S4.1 `SafetyController`, pinned here)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_halt_is_sticky_after_the_anomaly_clears_and_next_intent_rejects_with_the_l5_reason(tmp_path):
    # Fork 1 / design Â§6.1 STICKY: the anomaly CLEARING does not resume the loop -- op-state
    # stays HALTED with the stored l5 reason, and an intent proposed AFTER the halt is
    # REJECTED with Decision.reason == "l5_clock_skew" (the controller's stored reason
    # surfaces verbatim through the untouched verdict path, Â§6.6). Recovery is operator-owned.
    # MUTATION KILLED: any auto-resume branch in run_cycle (see the Step-2 mutation check),
    # and a generic reason string masking the specific l5_* one.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        skew = _SkewDouble(True)
        signer = PaperSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(skew))
        rc.run_cycle()                       # cycle 1: anomaly -> halt + one-shot de-risk
        assert ctl.state() == _safety.HALTED

        skew.is_skewed = False               # the anomaly CLEARS...
        store.propose_trade("i1", **_P)      # ...and a fresh intent arrives
        rc.run_cycle()                       # cycle 2

        assert ctl.state() == _safety.HALTED             # ...but the halt is STICKY
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "l5_clock_skew"
        assert len(signer.cancelled_all) == 1            # and the one-shot stayed one-shot
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” the sticky behavior already emerges, so prove the test CAN fail via the auto-resume mutation the design names. Temporarily insert (via Edit) after the `except`-free `if state.action == HALT and ...:` block's last line in `run_cycle`, at the same indent as that `if`:

```python
            elif self._controller.state() == HALTED:
                # MUTATION: auto-resume on clear (the sticky test must kill this)
                self._controller.set_state(RUNNING, reason="anomaly_cleared")
```

Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ expected `1 failed` (`test_halt_is_sticky...`: `assert 'ACCEPTED' == 'REJECTED'` â€” the cleared anomaly resumed the loop and the intent traded). Revert the mutation: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git checkout -- src/polybot/ers/controller.py'` (controller.py is committed as of A9, so checkout restores it exactly).

- [ ] **Step 3: Minimal implementation** â€” none required (the pin is the deliverable):

```python
# (no production code for this task -- A7-A9's wiring + the S4.1 SafetyController already
#  satisfy it; the test pins Fork 1 stickiness + the l5 Decision.reason surface)
```

Verify clean: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git status --porcelain src/'` â†’ empty output.

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `15 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `571 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly.py && git commit -m "test(controller): sticky halt after the anomaly clears + intent rejects with the l5 reason"'`

---

### Task A11: a raising `cancel_all` is audited as FAILED and never unwinds the halt or the cycle

**Files:**
- Modify: `src/polybot/ers/controller.py` (wrap the de-risk in the pinned try/except)
- Test: `tests/test_ers_anomaly.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
class _RaisingCancelSigner(PaperSigner):
    """cancel_all raises (venue/RPC down at the worst moment): the halt must already be in
    place and must SURVIVE; the failure is audited; the cycle continues."""

    def cancel_all(self):
        raise RuntimeError("venue rejected cancelAll")


def test_raising_cancel_all_is_audited_as_failed_and_never_unwinds_the_halt_or_the_cycle(tmp_path):
    # Design Â§2 / invariant 2: a raising signer must NOT unwind the halt or kill the cycle --
    # the gate closed FIRST, the failure lands in op_audit as detail="FAILED: ...", and
    # process_pending still runs (the pending intent is REJECTED under the l5 reason; the
    # standing GTD exits are the backstop). MUTATION KILLED: letting the exception propagate
    # out of run_cycle (the S4.3 supervisor would SIGKILL a healthy-but-unlucky loop), and
    # auditing an unconditional success detail.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = _RaisingCancelSigner()
        rc = _rc(store, ctl, signer, anomaly=_monitor(_SkewDouble(True)))

        rc.run_cycle()                                   # must NOT raise

        assert ctl.state() == _safety.HALTED             # the halt held
        cancel_rows = [r for r in store.op_audit_log() if r["kind"] == "cancel_all"]
        assert len(cancel_rows) == 1
        assert cancel_rows[0]["reason"] == "l5_clock_skew"
        assert cancel_rows[0]["detail"] == "FAILED: venue rejected cancelAll"
        # The cycle SURVIVED to process_pending: the intent is blocked under the l5 reason.
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "l5_clock_skew"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'`

Expected: `1 failed, 15 passed` â€” the new test errors with `RuntimeError: venue rejected cancelAll` propagating out of `run_cycle` (the de-risk is unwrapped).

- [ ] **Step 3: Minimal implementation** â€” in `run_cycle`, replace the three-line de-risk body under the guard with the pinned try/except (the full anomaly block, final form):

```python
        if self._anomaly is not None:
            # L5 (S4.4): ALWAYS evaluated when wired (keeps the monitor's per-token
            # prev-state warm every cycle). On HALT: the gate closes FIRST (set_state audits
            # the transition), THEN the one-shot BEST-EFFORT de-risk + its own audit row.
            state = self._anomaly.evaluate(self._portfolio.positions, self._book_for)
            # EDGE-triggered: act only from a LIVE loop (RUNNING/PAUSED) -- never re-fire on
            # an existing HALTED (no audit spam, no cancel_all churn against the standing GTD
            # exits) and never preempt FLATTENING (a stronger de-risk already in flight; it
            # settles HALTED on its own).
            if state.action == HALT and self._controller.state() in (RUNNING, PAUSED):
                self._controller.set_state(HALTED, reason=state.triggers[0])
                try:
                    self._signer.cancel_all()
                    self._store.record_op_event(kind="cancel_all", reason=state.triggers[0],
                                                detail=",".join(state.triggers))
                except Exception as exc:
                    # A raising signer must NOT unwind the halt or kill the cycle -- audit the
                    # failure; the pre-staged GTD exits are the backstop.
                    self._store.record_op_event(kind="cancel_all", reason=state.triggers[0],
                                                detail=f"FAILED: {exc}")
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly.py -q'` â†’ `16 passed`
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ `572 passed`

- [ ] **Step 5: Commit**

`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_anomaly.py && git commit -m "feat(controller): raising cancel_all audited as FAILED -- halt + cycle survive"'`

---

## Sub-slice S4.4b: caps + pure sentinels

> Precondition: S4.4a has landed on `pol-6-s4.4-anomaly` â€” `src/polybot/ers/anomaly.py` exists with `NONE`/`HALT`/`AnomalyState`/`AnomalyMonitor` (all seams `None`-defaulting) and `ers/safety.py` has the 5 new `REASON_L5_*` constants. Before B4â€“B9, **Read `src/polybot/ers/anomaly.py` in full** to confirm the spine's attribute names (`self._skew_sentinel`, `self._api_sentinel`, `self._clock` â€” the repo's underscore convention) and the shape of `evaluate` (`now = self._clock()`; `triggers: list`; `return AnomalyState(HALT if triggers else NONE, tuple(triggers))`). UNC reads intermittently throw EISDIR â€” retry.

### Task B1: The 7 new anomaly RiskCaps fields â€” defaults + content-hash coverage
**Files:** Modify `src/polybot/ers/caps.py` (insert after line 62, the comment closing the `reconcile_settle_window_seconds` block, before `__post_init__` at line 64). Test: create `tests/test_ers_anomaly_sentinels.py`.
- [ ] **Step 1: Write the failing test** â€” create the new test file:

```python
"""Tests for the S4.4 / POL-6 L5 anomaly caps + pure sentinels (DESIGN-S4.4-ANOMALY Â§3-Â§5).

The 7 new RiskCaps thresholds are tighten-only, _verify-checked, content-hashed envelope
fields; ClockSkewSentinel and ApiStormSentinel are the pure, clock-injected L5 seams that
the AnomalyMonitor consults in pinned severity order. All time values here are injected
floats (monotonic seconds); no test touches a real clock.
"""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps


def test_anomaly_caps_defaults_construct_and_carry_the_design_values():
    # Kills: a missing field declaration or a wrong default constant (design Â§5 table).
    caps = RiskCaps()
    assert caps.midpoint_jump_halt == Decimal("0.15")
    assert caps.depth_collapse_fraction == Decimal("0.8")
    assert caps.depth_collapse_min_prev_shares == Decimal("1000")
    assert caps.ws_staleness_halt_seconds == 30
    assert caps.api_5xx_storm_count == 5
    assert caps.api_auth_storm_count == 2
    assert caps.api_storm_window_seconds == 60


def test_anomaly_caps_changes_are_content_hash_tamper_evident():
    # Kills: declaring a threshold as a plain class attribute instead of a dataclass field
    # (asdict would skip it and the signed envelope's hash would NOT change on tamper).
    base = RiskCaps().content_hash()
    assert RiskCaps(ws_staleness_halt_seconds=15).content_hash() != base
    assert RiskCaps(midpoint_jump_halt=Decimal("0.10")).content_hash() != base
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: `AttributeError: 'RiskCaps' object has no attribute 'midpoint_jump_halt'` (first test) and `TypeError: RiskCaps.__init__() got an unexpected keyword argument 'ws_staleness_halt_seconds'` (second test). 2 failed.
- [ ] **Step 3: Minimal implementation** â€” in `caps.py`, insert after line 62 (immediately before `def __post_init__`):

```python
    # S4.4 / POL-6 L5 anomaly thresholds (DESIGN-S4.4-ANOMALY Â§5). Tighten-only + hashed;
    # _verify range checks join below. Book thresholds are Decimal (exact prob/share math).
    midpoint_jump_halt: Decimal = Decimal("0.15")             # |mid - prev_mid| >= x -> l5_abnormal_book
    depth_collapse_fraction: Decimal = Decimal("0.8")         # top-of-book depth drop >= frac vs prev
    depth_collapse_min_prev_shares: Decimal = Decimal("1000") # noise floor for the collapse check
    ws_staleness_halt_seconds: int = 30                       # last-WS-frame age -> l5_ws_down
    api_5xx_storm_count: int = 5                              # >= N statuses >=500 in window -> l5_api_storm
    api_auth_storm_count: int = 2                             # >= N of {401,403} in window -> l5_api_storm
    api_storm_window_seconds: int = 60                        # the storm counting window
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (2 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (556 pre-S4.4 baseline + S4.4a's tests + 2, 0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/caps.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B1: the 7 L5 anomaly RiskCaps fields (defaults + content-hash coverage)"'`

### Task B2: `_verify` range checks on the three Decimal book thresholds
**Files:** Modify `src/polybot/ers/caps.py` (`_verify`, append after the strictly-positive-int loop ending at line 151 â€” post-B1 the loop sits ~7 lines lower; anchor on the loop, not the number). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_midpoint_jump_halt_of_zero_is_rejected_and_the_default_accepted():
    # Boundary pair, lower edge of (0, 1): x == 0 must FAIL construction; 0.15 is in-range.
    # Kills: dropping the lower bound from the midpoint_jump_halt range check.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("0"))
    RiskCaps(midpoint_jump_halt=Decimal("0.15"))  # must not raise


def test_midpoint_jump_halt_of_one_is_rejected_because_a_mid_is_a_probability():
    # Boundary pair, upper edge of (0, 1): x == 1 must FAIL (a probability mid can never
    # jump a full 1.0 -> the trigger would be vacuous). Kills: writing <= 1 instead of < 1.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("1"))


def test_depth_collapse_fraction_of_zero_is_rejected_but_one_is_accepted():
    # Boundary pair for (0, 1]: 0 rejected; 1 ("all prev depth gone") is the legal tightest
    # setting and MUST construct. Kills: writing < 1 instead of <= 1, or dropping the lower bound.
    with pytest.raises(ValueError, match="depth_collapse_fraction"):
        RiskCaps(depth_collapse_fraction=Decimal("0"))
    RiskCaps(depth_collapse_fraction=Decimal("1"))  # must not raise


def test_depth_collapse_min_prev_shares_of_zero_is_rejected():
    # (> 0): a zero noise floor would arm the collapse check on dust-depth books.
    # Kills: dropping the strictly-positive check on the noise floor.
    with pytest.raises(ValueError, match="depth_collapse_min_prev_shares"):
        RiskCaps(depth_collapse_min_prev_shares=Decimal("0"))
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: 4 failures, each `Failed: DID NOT RAISE <class 'ValueError'>`.
- [ ] **Step 3: Minimal implementation** â€” in `_verify`, append after the strictly-positive-int `for name in (...)` loop:

```python
        # --- S4.4 L5 anomaly book thresholds (DESIGN-S4.4-ANOMALY Â§5) ---
        if not (Decimal(0) < self.midpoint_jump_halt < Decimal(1)):
            raise ValueError(
                f"midpoint_jump_halt must be in (0, 1) -- a mid is a probability, "
                f"got {self.midpoint_jump_halt}"
            )
        if not (Decimal(0) < self.depth_collapse_fraction <= Decimal(1)):
            raise ValueError(
                f"depth_collapse_fraction must be in (0, 1], got {self.depth_collapse_fraction}"
            )
        if self.depth_collapse_min_prev_shares <= 0:
            raise ValueError(
                f"depth_collapse_min_prev_shares must be > 0, "
                f"got {self.depth_collapse_min_prev_shares}"
            )
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (6 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/caps.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B2: _verify range checks on the L5 book thresholds (boundary pairs)"'`

### Task B3: The four new int caps join the strictly-positive-int `_verify` loop
**Files:** Modify `src/polybot/ers/caps.py` (the `for name in ("consecutive_loss", ...)` loop, originally lines 147â€“151 â€” extend its tuple). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_each_anomaly_int_cap_of_zero_fails_verify():
    # All four join the existing strictly-positive-int loop; a zero window/count/staleness
    # would make its check vacuous (0 events always "storm", any frame age always "stale").
    # Kills: leaving any one name out of the _verify loop tuple.
    for field in ("ws_staleness_halt_seconds", "api_5xx_storm_count",
                  "api_auth_storm_count", "api_storm_window_seconds"):
        with pytest.raises(ValueError, match=field):
            RiskCaps(**{field: 0})
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q -k each_anomaly_int_cap'`
  Expected: 1 failed â€” `Failed: DID NOT RAISE <class 'ValueError'>` (on the first field).
- [ ] **Step 3: Minimal implementation** â€” replace the loop's tuple so it reads:

```python
        for name in ("consecutive_loss", "new_positions_per_hour", "new_positions_per_day",
                     "clock_skew_tolerance_seconds", "signing_canary_interval_seconds",
                     "dead_man_switch_timeout_seconds", "reconcile_settle_window_seconds",
                     "ws_staleness_halt_seconds", "api_5xx_storm_count",
                     "api_auth_storm_count", "api_storm_window_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (7 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/caps.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B3: the four L5 int caps join the strictly-positive _verify loop"'`

### Task B4: `ClockSkewSentinel` â€” strict-> boundary pair + symmetry
**Files:** Modify `src/polybot/ers/anomaly.py` (add the `ClockSkewSentinel` class after `AnomalyState`, before `AnomalyMonitor` â€” mirrors the pinned module map order). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” add to the import block `from polybot.ers.anomaly import ClockSkewSentinel`, then append:

```python
def test_clock_skew_of_exactly_the_tolerance_is_not_skewed():
    # Boundary pair (strict >): |wall - ntp| == tolerance (2s default) must NOT trip.
    # Kills: mutating > to >= in ClockSkewSentinel.skewed.
    sentinel = ClockSkewSentinel(wall_clock=lambda: 1_000_002.0,
                                 ntp_ref=lambda: 1_000_000.0, caps=RiskCaps())
    assert sentinel.skewed() is False


def test_clock_skew_just_over_the_tolerance_is_skewed():
    # Boundary pair partner: 2.5s > 2s tolerance trips.
    # Kills: deleting the comparison / hardcoding skewed False.
    sentinel = ClockSkewSentinel(wall_clock=lambda: 1_000_002.5,
                                 ntp_ref=lambda: 1_000_000.0, caps=RiskCaps())
    assert sentinel.skewed() is True


def test_clock_skew_is_symmetric_when_the_wall_clock_runs_behind_ntp():
    # wall BEHIND ntp by 2.5s trips too; behind by exactly 2s does not (same strict edge).
    # Kills: dropping abs() -- a signed compare only catches one direction of skew.
    behind = ClockSkewSentinel(wall_clock=lambda: 1_000_000.0,
                               ntp_ref=lambda: 1_000_002.5, caps=RiskCaps())
    assert behind.skewed() is True
    behind_at_edge = ClockSkewSentinel(wall_clock=lambda: 1_000_000.0,
                                       ntp_ref=lambda: 1_000_002.0, caps=RiskCaps())
    assert behind_at_edge.skewed() is False
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: collection error â€” `ImportError: cannot import name 'ClockSkewSentinel' from 'polybot.ers.anomaly'`.
- [ ] **Step 3: Minimal implementation** â€” in `ers/anomaly.py`:

```python
class ClockSkewSentinel:
    """L5 clock-skew seam (design Â§3 #4): pure compare of two injected 0-arg refs, both
    returning float unix-seconds (real NTP/chrony ref is deploy-time wiring). Strictly
    GREATER than ``caps.clock_skew_tolerance_seconds`` trips; symmetric via abs()."""

    def __init__(self, *, wall_clock, ntp_ref, caps):
        self._wall_clock = wall_clock
        self._ntp_ref = ntp_ref
        self._caps = caps

    def skewed(self):
        return abs(self._wall_clock() - self._ntp_ref()) > self._caps.clock_skew_tolerance_seconds
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (10 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B4: ClockSkewSentinel (strict-gt boundary pair + abs symmetry)"'`

### Task B5: `ApiStormSentinel` â€” record + the 5xx threshold pair
**Files:** Modify `src/polybot/ers/anomaly.py` (add `ApiStormSentinel` after `ClockSkewSentinel`; add `from collections import deque` to the module imports if S4.4a hasn't already). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” extend the anomaly import to `from polybot.ers.anomaly import ApiStormSentinel, ClockSkewSentinel`, then append:

```python
def test_four_5xx_responses_in_the_window_do_not_storm():
    # Boundary pair (fivexx >= api_5xx_storm_count == 5): FOUR is under the threshold.
    # Kills: loosening the count compare or hardcoding storming True.
    sentinel = ApiStormSentinel(RiskCaps())
    for t in (0.0, 1.0, 2.0, 3.0):
        sentinel.record(500, now=t)
    assert sentinel.storming(10.0) is False


def test_five_mixed_5xx_responses_in_the_window_storm():
    # Boundary pair partner: exactly FIVE statuses >= 500 (mixed 500/502/503/504) at the
    # threshold storms. Kills: mutating >= to > on the count, and any 5xx filter that
    # matches only the literal 500 instead of status >= 500.
    sentinel = ApiStormSentinel(RiskCaps())
    for t, status in ((0.0, 500), (1.0, 502), (2.0, 503), (3.0, 504), (4.0, 500)):
        sentinel.record(status, now=t)
    assert sentinel.storming(10.0) is True
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: collection error â€” `ImportError: cannot import name 'ApiStormSentinel' from 'polybot.ers.anomaly'`.
- [ ] **Step 3: Minimal implementation** â€” in `ers/anomaly.py` (ensure `from collections import deque` is in the module imports):

```python
class ApiStormSentinel:
    """L5 API error-storm seam (design Â§3 #3): the (deploy-time) API caller records every
    response status via ``record``; the monitor polls ``storming(now)``. Windowed deque of
    ``(now_s, int(status))`` in the monitor's monotonic-seconds clock domain.
    Auth counting + window pruning arrive in the next two TDD steps."""

    def __init__(self, caps):
        self._caps = caps
        self._events = deque()  # (now_s, int(status))

    def record(self, status, *, now):
        self._events.append((now, int(status)))

    def storming(self, now):
        fivexx = sum(1 for _, s in self._events if s >= 500)
        return fivexx >= self._caps.api_5xx_storm_count
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (12 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B5: ApiStormSentinel record + 5xx threshold boundary pair"'`

### Task B6: `ApiStormSentinel` â€” auth threshold pair + non-auth 4xx inert
**Files:** Modify `src/polybot/ers/anomaly.py` (`ApiStormSentinel.storming` only). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_one_auth_failure_in_the_window_does_not_storm():
    # Boundary pair (auth >= api_auth_storm_count == 2): a single 401 is under.
    # Kills: loosening the auth count compare or hardcoding storming True.
    sentinel = ApiStormSentinel(RiskCaps())
    sentinel.record(401, now=0.0)
    assert sentinel.storming(5.0) is False


def test_two_auth_failures_storm_and_403_counts_like_401():
    # Boundary pair partner: 401 + 403 == exactly 2 auth fails at the threshold storms.
    # Kills: mutating >= to > on the auth count, and an auth filter matching only 401.
    sentinel = ApiStormSentinel(RiskCaps())
    sentinel.record(401, now=0.0)
    sentinel.record(403, now=1.0)
    assert sentinel.storming(5.0) is True


def test_non_auth_4xx_statuses_never_count_toward_either_storm():
    # 404/429/400 are ordinary client noise: NOT auth failures, NOT 5xx -- even eight of
    # them must not fire. Kills: widening the auth filter to any 4xx (400 <= s < 500) or
    # widening the 5xx filter to s >= 400.
    sentinel = ApiStormSentinel(RiskCaps())
    for t, status in enumerate((404, 429, 400, 404, 429, 400, 404, 429)):
        sentinel.record(status, now=float(t))
    assert sentinel.storming(8.0) is False
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: 1 failed â€” `test_two_auth_failures_storm_and_403_counts_like_401` with `AssertionError: assert False is True` (B5's impl has no auth counting; the two no-fire tests pass by construction â€” the RED half of this pair is the two-auth fire).
- [ ] **Step 3: Minimal implementation** â€” replace `ApiStormSentinel.storming`:

```python
    def storming(self, now):
        fivexx = sum(1 for _, s in self._events if s >= 500)
        auth = sum(1 for _, s in self._events if s in (401, 403))
        return (fivexx >= self._caps.api_5xx_storm_count
                or auth >= self._caps.api_auth_storm_count)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (15 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B6: ApiStormSentinel auth-storm pair + non-auth 4xx inert"'`

### Task B7: `ApiStormSentinel` â€” inclusive window prune (breaker-style boundary)
**Files:** Modify `src/polybot/ers/anomaly.py` (`ApiStormSentinel.storming` only). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_event_at_exactly_now_minus_window_is_kept_inclusive_boundary():
    # Inclusive-boundary pin, mirroring the DrawdownBreaker deque: an event with
    # now - t == window (60s) is still IN the window, so 5 old 5xx at t=0 still storm
    # at now=60. Kills: pruning with >= (now - t >= window would drop the boundary entry).
    sentinel = ApiStormSentinel(RiskCaps())
    for _ in range(5):
        sentinel.record(500, now=0.0)
    assert sentinel.storming(60.0) is True


def test_event_just_older_than_the_window_is_pruned_and_the_storm_clears():
    # Boundary pair partner: at now=61 the t=0 events are (61 - 0) > 60 -> pruned -> no
    # storm. Kills: deleting the prune entirely (an API storm would then NEVER clear).
    sentinel = ApiStormSentinel(RiskCaps())
    for _ in range(5):
        sentinel.record(500, now=0.0)
    assert sentinel.storming(61.0) is False
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: 1 failed â€” `test_event_just_older_than_the_window_is_pruned_and_the_storm_clears` with `AssertionError: assert True is False` (no prune exists yet; the kept-at-boundary test passes by construction â€” the RED half of this pair is the prune).
- [ ] **Step 3: Minimal implementation** â€” replace `ApiStormSentinel.storming` (prune-then-count; keep when `now - t <= window`, the breaker's inclusive convention):

```python
    def storming(self, now):
        window = self._caps.api_storm_window_seconds
        while self._events and now - self._events[0][0] > window:
            self._events.popleft()
        fivexx = sum(1 for _, s in self._events if s >= 500)
        auth = sum(1 for _, s in self._events if s in (401, 403))
        return (fivexx >= self._caps.api_5xx_storm_count
                or auth >= self._caps.api_auth_storm_count)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (17 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B7: ApiStormSentinel inclusive window prune (boundary pair)"'`

### Task B8: Wire both sentinels into `AnomalyMonitor.evaluate` in pinned severity order
**Files:** Modify `src/polybot/ers/anomaly.py` (`AnomalyMonitor.evaluate` â€” Read the S4.4a spine first; the consult sequence must end up in the pinned order skew â†’ recon â†’ canary â†’ abnormal-book â†’ api â†’ ws, with consults for not-yet-built seams simply absent). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” extend imports to `from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor, ApiStormSentinel, ClockSkewSentinel` and add `from polybot.ers.safety import REASON_L5_API_STORM, REASON_L5_CLOCK_SKEW`, then append:

```python
def _no_books(token_id):
    """book_for stub: no book for any token (the abnormal-book check skips absent books)."""
    return None


def _burst_5xx_sentinel():
    """An ApiStormSentinel pre-loaded with a storming burst: 5x 500 at t=0..4 seconds."""
    sentinel = ApiStormSentinel(RiskCaps())
    for t in range(5):
        sentinel.record(500, now=float(t))
    return sentinel


def test_monitor_with_a_skewed_clock_sentinel_halts_with_l5_clock_skew():
    # The REAL ClockSkewSentinel wired through the skew_sentinel= seam fires the trigger.
    # Kills: dropping the skew consult from evaluate (state would be NONE).
    skew = ClockSkewSentinel(wall_clock=lambda: 100.0, ntp_ref=lambda: 0.0, caps=RiskCaps())
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=skew)
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_CLOCK_SKEW in state.triggers


def test_monitor_with_a_storming_api_sentinel_halts_with_l5_api_storm():
    # The REAL ApiStormSentinel wired through the api_sentinel= seam fires the trigger.
    # Kills: dropping the api consult from evaluate.
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 10.0, api_sentinel=_burst_5xx_sentinel())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_API_STORM in state.triggers


def test_monitor_api_consult_passes_its_own_clock_now_into_the_storm_window():
    # Same burst (t=0..4), two monitor clocks: at now=30 the burst is in-window -> HALT;
    # at now=100 it has aged out (100-4 > 60) -> NONE with empty triggers.
    # Kills: consulting storming() with anything other than the monitor clock's now
    # (a hardcoded 0 would keep the aged burst "in-window" forever).
    fresh = AnomalyMonitor(RiskCaps(), clock=lambda: 30.0, api_sentinel=_burst_5xx_sentinel())
    assert fresh.evaluate((), _no_books).action == HALT
    aged = AnomalyMonitor(RiskCaps(), clock=lambda: 100.0, api_sentinel=_burst_5xx_sentinel())
    aged_state = aged.evaluate((), _no_books)
    assert aged_state.action == NONE
    assert aged_state.triggers == ()


def test_clock_skew_fires_ahead_of_api_storm_in_the_triggers_tuple():
    # SEVERITY ORDER (pinned): when BOTH fire, triggers is most-severe-first and
    # triggers[0] -- the set_state reason -- is l5_clock_skew.
    # Kills: swapping the skew/api consult order in evaluate.
    skew = ClockSkewSentinel(wall_clock=lambda: 100.0, ntp_ref=lambda: 0.0, caps=RiskCaps())
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 10.0,
                             skew_sentinel=skew, api_sentinel=_burst_5xx_sentinel())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert state.triggers[0] == REASON_L5_CLOCK_SKEW
    assert state.triggers == (REASON_L5_CLOCK_SKEW, REASON_L5_API_STORM)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: the api and ordering tests fail with `AssertionError: assert 'NONE' == 'HALT'` (no api consult exists). If S4.4a's spine already consults the skew seam, the first test is already green â€” the RED must come from the api + ordering tests; if the spine used a different fake seam, the skew test also fails with `assert 'NONE' == 'HALT'`.
- [ ] **Step 3: Minimal implementation** â€” ensure `ers/anomaly.py` imports `from polybot.ers.safety import REASON_L5_API_STORM, REASON_L5_CLOCK_SKEW` (extend S4.4a's existing safety import). In `AnomalyMonitor.evaluate` â€” whose S4.4a spine reads `now = self._clock()`, builds a `triggers` list, and returns `AnomalyState(HALT if triggers else NONE, tuple(triggers))` â€” place these two blocks so skew is the FIRST consult in the method and api sits after every S4.4a-existing consult except ws (pinned order: skew, recon, canary, abnormal-book, api, ws). If the spine already has a skew consult, keep it in place (its semantics are identical) and only add the api block:

```python
        # consult #1 (pinned severity order): clock skew -> l5_clock_skew
        if self._skew_sentinel is not None:
            if self._skew_sentinel.skewed():
                triggers.append(REASON_L5_CLOCK_SKEW)
```

```python
        # consult #5 (pinned severity order): API 5xx/auth storm -> l5_api_storm
        if self._api_sentinel is not None:
            if self._api_sentinel.storming(now):
                triggers.append(REASON_L5_API_STORM)
```

  (Bare consults are the deliberate TDD minimum â€” B9 adds the contract-mandated fail-closed try/except around both within this same sub-slice. Verify the spine's attribute names `self._skew_sentinel`/`self._api_sentinel`/`self._clock` by reading the file first; use whatever S4.4a named them.)
- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (21 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed â€” S4.4a's all-seams-None inert + spine tests must stay green)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B8: wire skew + api sentinels into evaluate in pinned severity order"'`

### Task B9: Fail-closed seam consults â€” a RAISING sentinel fires its own trigger
**Files:** Modify `src/polybot/ers/anomaly.py` (`AnomalyMonitor.evaluate` â€” the two consult blocks from B8 only). Test: `tests/test_ers_anomaly_sentinels.py` (append).
- [ ] **Step 1: Write the failing test** â€” append:

```python
class _RaisingSkewStub:
    """Duck-typed skew seam whose consult raises (e.g. the NTP ref is unreachable)."""

    def skewed(self):
        raise RuntimeError("ntp ref unreachable")


class _RaisingApiStub:
    """Duck-typed api seam whose consult raises (e.g. the health feed wedged)."""

    def storming(self, now):
        raise RuntimeError("api health feed wedged")


def test_a_raising_skew_sentinel_fails_closed_and_fires_l5_clock_skew():
    # FAIL-CLOSED SEAM RULE: a wired seam that RAISES fires its own trigger -- it never
    # masks and never propagates. Kills: removing the try/except around the skew consult
    # (this test would then ERROR with RuntimeError instead of asserting HALT).
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=_RaisingSkewStub())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_CLOCK_SKEW in state.triggers


def test_a_raising_api_sentinel_fails_closed_and_fires_l5_api_storm():
    # Kills: removing the try/except around the api consult.
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, api_sentinel=_RaisingApiStub())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_API_STORM in state.triggers


def test_a_raising_skew_seam_does_not_mask_a_later_api_storm():
    # append + CONTINUE: the raising skew consult must not short-circuit the api consult;
    # BOTH triggers land, still severity-ordered. Kills: an early return (or re-raise)
    # inside the skew except branch.
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 10.0,
                             skew_sentinel=_RaisingSkewStub(),
                             api_sentinel=_burst_5xx_sentinel())
    state = monitor.evaluate((), _no_books)
    assert state.triggers == (REASON_L5_CLOCK_SKEW, REASON_L5_API_STORM)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'`
  Expected: all 3 new tests ERROR with `RuntimeError: ntp ref unreachable` / `RuntimeError: api health feed wedged` escaping `evaluate` â€” the exact defect (the seam propagates instead of failing closed). If S4.4a's spine already wrapped its skew consult, only the api-related tests go red; the api RED is mandatory.
- [ ] **Step 3: Minimal implementation** â€” replace B8's two bare consult blocks with the pinned fail-closed shape (append + continue, never mask, never propagate):

```python
        # consult #1 (pinned severity order): clock skew -> l5_clock_skew.
        # FAIL-CLOSED: a raising seam IS the anomaly -- fire and move to the next seam.
        if self._skew_sentinel is not None:
            try:
                if self._skew_sentinel.skewed():
                    triggers.append(REASON_L5_CLOCK_SKEW)
            except Exception:
                triggers.append(REASON_L5_CLOCK_SKEW)
```

```python
        # consult #5 (pinned severity order): API 5xx/auth storm -> l5_api_storm.
        # FAIL-CLOSED: a raising seam IS the anomaly -- fire and move to the next seam.
        if self._api_sentinel is not None:
            try:
                if self._api_sentinel.storming(now):
                    triggers.append(REASON_L5_API_STORM)
            except Exception:
                triggers.append(REASON_L5_API_STORM)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_sentinels.py -q'` (24 passed)
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` (0 failed)
- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_sentinels.py && git commit -m "S4.4b B9: fail-closed skew/api seam consults (raise fires trigger, never masks)"'`

---

## Sub-slice C: S4.4c â€” abnormal book (crossed/locked, midpoint jump, depth collapse)

**Prerequisites:** S4.4a spine (`AnomalyMonitor` exists in `src/polybot/ers/anomaly.py` with `evaluate(positions, book_for)` collecting firing reasons into a `triggers` list in the pinned severity order; `self._caps = caps` breaker-mirror) and S4.4b caps fields (`midpoint_jump_halt`, `depth_collapse_fraction`, `depth_collapse_min_prev_shares`) are already on the branch. `anomaly.py` is net-new from S4.4a, so anchors below are structural (named methods/slots), not line numbers. All abnormal-book checks are internal to the monitor (no seam); the monitor under test is constructed bare â€” caps + clock only.

---

### Task C1: Structural check â€” non-stale crossed/locked/empty-side book fires `l5_abnormal_book`

**Files:**
- Create: `tests/test_ers_anomaly_book.py`
- Modify: `src/polybot/ers/anomaly.py` (imports; `AnomalyMonitor.evaluate` severity slot 4 â€” immediately BEFORE the api-storm consult; new private method `_check_abnormal_book`)

- [ ] **Step 1: Write the failing test**

```python
"""S4.4c -- L5 abnormal-book checks (DESIGN-S4.4-ANOMALY.md Â§3 trigger 1).

Driven purely through positions + book_for with REAL LocalBook instances; the monitor
is constructed bare (caps + clock only) because these checks need no seam.
"""

from decimal import Decimal

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_ABNORMAL_BOOK
from polybot.ers.validator import OpenPosition
from polybot.ingestion.orderbook import LocalBook


def _monitor():
    """Bare monitor: caps + clock only (0-arg monotonic-SECONDS clock, injected)."""
    return AnomalyMonitor(RiskCaps(), clock=lambda: 0.0)


def _pos(token_id, *, frozen=False):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal("8"), matrix_cold=False, token_id=token_id,
                        entry_price=Decimal("0.50"), frozen=frozen)


def _book(*, bid=None, ask=None, bid_size="500", ask_size="500"):
    """Fresh LocalBook from one full snapshot (apply_book marks it NON-stale).
    None on a side = that side empty."""
    bids = [{"price": bid, "size": bid_size}] if bid is not None else []
    asks = [{"price": ask, "size": ask_size}] if ask is not None else []
    book = LocalBook()
    book.apply_book({"bids": bids, "asks": asks})
    return book


def test_non_stale_crossed_book_fires_l5_abnormal_book():
    # Kills: deleting the structural midpoint()-is-None check entirely.
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")  # bid > ask -> crossed -> midpoint None
    assert book.is_stale() is False and book.midpoint() is None  # precondition sanity
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_locked_book_fires_l5_abnormal_book():
    # Kills: weakening the LocalBook contract's bid >= ask to bid > ask (locked = bid == ask).
    mon = _monitor()
    book = _book(bid="0.50", ask="0.50")
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_empty_ask_side_fires_l5_abnormal_book():
    # Kills: only checking crossed prices and skipping the empty-side midpoint-None case.
    mon = _monitor()
    book = _book(bid="0.40")  # asks empty; apply_book still marks the book non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_stale_crossed_book_does_not_fire_stale_is_breaker_domain():
    # Kills: dropping the is_stale() gate (stale books belong to validator book_stale /
    # breaker stale_mark, NOT L5 -- design Â§0 'abnormal-book checks run on NON-stale books only').
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    book.mark_stale()
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_frozen_position_book_is_still_checked_and_fires():
    # Kills: copying the breaker's 'if pos.frozen: continue' -- anomaly checks book
    # STRUCTURE, frozen positions still have books (pinned contract: skip frozen? NO).
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    state = mon.evaluate([_pos("t1", frozen=True)], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_healthy_book_fires_nothing_action_none_triggers_empty():
    # Kills: inverting the midpoint()-is-None condition (firing on every VALID book).
    mon = _monitor()
    book = _book(bid="0.49", ask="0.51")  # mid 0.50, both sides present, non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_missing_book_none_is_skipped_silently():
    # Kills: treating an ABSENT book as abnormal (book None = validator no_book domain),
    # or calling methods on None (AttributeError would escape evaluate).
    mon = _monitor()
    state = mon.evaluate([_pos("t1")], lambda token: None)
    assert state.action == NONE
    assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
```

Expected: `4 failed, 3 passed` â€” the crossed / locked / empty-ask / frozen tests each fail with `AssertionError: assert 'NONE' == 'HALT'` (the spine's `evaluate` has no abnormal-book consult yet, so it returns `AnomalyState(NONE, ())`). The stale / healthy / missing-book guards pass (they pin no-fire behavior). If it fails with `ImportError` instead, S4.4a/b are not on the branch â€” stop, do not proceed.

- [ ] **Step 3: Minimal implementation**

In `src/polybot/ers/anomaly.py`:

1. Ensure the module imports include the reason constant (add to the existing `polybot.ers.safety` import if the spine already has one):

```python
from polybot.ers.safety import REASON_L5_ABNORMAL_BOOK
```

2. In `AnomalyMonitor.evaluate`, in severity slot 4 â€” after whichever of the skew/recon/canary consults exist at this point in the build order, and immediately BEFORE the api-storm consult â€” insert:

```python
        # Severity slot 4: abnormal book -- internal check over positions + book_for, no seam.
        self._check_abnormal_book(positions, book_for, triggers)
```

3. Add the method to `AnomalyMonitor`:

```python
    def _check_abnormal_book(self, positions, book_for, triggers):
        """L5 trigger 1, structural leg (DESIGN-S4.4 Â§3): a HELD token whose NON-stale book
        has no usable midpoint (crossed/locked/empty side) is an integrity anomaly.
        Stale books are SKIPPED (validator book_stale / breaker stale_mark own those);
        absent books are SKIPPED (validator no_book domain). Frozen positions are NOT
        skipped -- this checks book structure, not P&L."""
        for pos in positions:
            book = book_for(pos.token_id)
            if book is None:
                continue
            if book.is_stale():
                continue
            if book.midpoint() is None:
                triggers.append(REASON_L5_ABNORMAL_BOOK)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```

Expected: `7 passed` in the file; full suite 0 failures (556 pre-S4.4 baseline + the S4.4a/b additions + these 7).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_book.py && git commit -m "S4.4c: non-stale crossed/locked/empty-side book fires l5_abnormal_book (stale + absent books skipped; frozen positions still checked)"'
```

---

### Task C2: Midpoint-jump halt â€” per-token prev-mid memory, `>=` boundary, stale-gap preservation

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (`AnomalyMonitor.__init__` â€” add `_prev_mid`; replace `_check_abnormal_book`)
- Test: `tests/test_ers_anomaly_book.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_ers_anomaly_book.py`)

```python
def test_first_observation_of_token_never_fires_midpoint_jump():
    # Kills: seeding prev-mid with a default (e.g. 0 -> |0.40 - 0| >= 0.15 would false-fire
    # the very first time a token is seen). First observation is memory-building ONLY.
    mon = _monitor()
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))  # mid 0.40
    assert state.action == NONE
    assert state.triggers == ()


def test_midpoint_jump_of_exactly_the_threshold_0_15_fires():
    # Boundary pair, AT threshold: design says |mid - prev_mid| >= midpoint_jump_halt (0.15).
    # 0.40 -> 0.55 is EXACTLY 0.15. Kills: '>=' -> '>' on the jump compare.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))          # mid 0.40
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.54", ask="0.56"))  # mid 0.55
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_midpoint_jump_just_under_the_threshold_does_not_fire():
    # Boundary pair, JUST UNDER: 0.40 -> 0.549 = 0.149 < 0.15. Kills: loosening the
    # threshold or comparing against the wrong caps field.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))            # mid 0.40
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.539", ask="0.559"))  # mid 0.549
    assert state.action == NONE
    assert state.triggers == ()


def test_midpoint_drop_of_exactly_the_threshold_0_15_fires():
    # Kills: dropping abs() -- a DOWNWARD jump (0.55 -> 0.40) is exactly as anomalous.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.54", ask="0.56"))          # mid 0.55
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))  # mid 0.40
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_stale_interlude_preserves_prev_mid_so_drift_across_the_gap_still_fires():
    # Kills: updating/clearing per-token memory on a stale cycle. The last VALID mid (0.50)
    # must stay the baseline across the gap: 0.65 - 0.50 = 0.15 fires. A mutant that books
    # the stale book's would-be mid (0.57) sees only 0.08 and stays silent.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51"))          # mid 0.50
    stale = _book(bid="0.56", ask="0.58")                                            # would-be mid 0.57
    stale.mark_stale()
    gap = mon.evaluate([_pos("t1")], lambda token: stale)
    assert gap.action == NONE                                                        # stale cycle inert
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.64", ask="0.66"))  # mid 0.65
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
```

Expected: `3 failed, 9 passed` â€” the at-threshold jump, the downward jump, and the stale-gap test fail with `AssertionError: assert 'NONE' == 'HALT'` (no prev-mid memory exists yet). The first-observation and just-under guards pass now and pin the boundary after Step 3.

- [ ] **Step 3: Minimal implementation**

In `src/polybot/ers/anomaly.py`:

1. At the end of `AnomalyMonitor.__init__`, after the seam assignments, add:

```python
        self._prev_mid = {}   # token_id -> last VALID (non-stale) midpoint observed (S4.4c)
```

2. Replace `_check_abnormal_book` entirely with:

```python
    def _check_abnormal_book(self, positions, book_for, triggers):
        """L5 trigger 1 (DESIGN-S4.4 Â§3): structural (crossed/locked/empty side) +
        midpoint-jump legs. Per-token prev-mid memory: FIRST observation never fires the
        jump; prev updates ONLY after comparisons and ONLY on a valid non-stale mid, so a
        stale interlude preserves the last VALID baseline. Jump fires at
        |mid - prev_mid| >= caps.midpoint_jump_halt (the exact-0.15 boundary test pins >=)."""
        for pos in positions:
            token = pos.token_id
            book = book_for(token)
            if book is None:
                continue
            if book.is_stale():
                continue
            mid = book.midpoint()
            if mid is None:
                triggers.append(REASON_L5_ABNORMAL_BOOK)  # crossed/locked/empty side
                continue
            prev_mid = self._prev_mid.get(token)
            if prev_mid is not None and abs(mid - prev_mid) >= self._caps.midpoint_jump_halt:
                triggers.append(REASON_L5_ABNORMAL_BOOK)
            self._prev_mid[token] = mid  # AFTER comparisons; valid non-stale mids only
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```

Expected: `12 passed` in the file; full suite 0 failures.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_book.py && git commit -m "S4.4c: midpoint-jump halt at >= caps.midpoint_jump_halt with per-token prev-mid memory (first observation inert; stale interlude keeps the last valid baseline)"'
```

---

### Task C3: Depth-collapse halt â€” `<=` boundary, 1000-share noise floor, prev-depth across a stale gap

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (`AnomalyMonitor.__init__` â€” add `_prev_depth`; replace `_check_abnormal_book`)
- Test: `tests/test_ers_anomaly_book.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_ers_anomaly_book.py`)

```python
def test_first_observation_of_token_never_fires_depth_collapse():
    # Pins: cycle 1 on a huge book is memory-building only (no prev-depth to compare).
    # Kills: seeding prev-depth with a comparable default.
    mon = _monitor()
    state = mon.evaluate([_pos("t1")],
                         lambda token: _book(bid="0.49", ask="0.51",
                                             bid_size="5000", ask_size="5000"))
    assert state.action == NONE
    assert state.triggers == ()


def test_depth_collapse_to_exactly_the_80_percent_threshold_fires():
    # Boundary pair, AT threshold: depth <= prev * (1 - depth_collapse_fraction) with
    # prev >= depth_collapse_min_prev_shares. 1000 -> 200 shares = exactly 80% gone, and
    # prev sits EXACTLY on the 1000-share floor. Kills: '<=' -> '<' on the collapse
    # compare AND '>=' -> '>' on the noise floor. Prices unchanged -> no jump interference.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="100"))  # depth 200
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_depth_drop_to_just_over_the_80_percent_threshold_does_not_fire():
    # Boundary pair, JUST OVER: 1000 -> 201 shares survives (200 is the line).
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="101"))  # depth 201
    assert state.action == NONE
    assert state.triggers == ()


def test_prev_depth_below_the_noise_floor_full_evaporation_does_not_fire():
    # Noise floor (Fork 2): prev depth 999 < depth_collapse_min_prev_shares 1000, so even a
    # near-total evaporation (999 -> 2, book still validly two-sided) is NOISE, not L5.
    # Kills: dropping the min_prev_shares guard.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="499.5", ask_size="499.5"))  # depth 999
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="1", ask_size="1"))  # depth 2
    assert state.action == NONE
    assert state.triggers == ()


def test_stale_interlude_preserves_prev_depth_so_collapse_across_the_gap_still_fires():
    # Kills: updating prev-depth on a stale cycle. top_of_book() is NOT stale-gated
    # (orderbook.py), so a naive impl could book the stale depth (500) and then see
    # 200 > 500 * 0.2 = 100 -> silent. The preserved baseline 1000 gives 200 <= 200 -> HALT.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    stale = _book(bid="0.49", ask="0.51", bid_size="250", ask_size="250")             # depth 500
    stale.mark_stale()
    gap = mon.evaluate([_pos("t1")], lambda token: stale)
    assert gap.action == NONE
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="100"))  # depth 200
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
```

Expected: `2 failed, 15 passed` â€” the at-threshold collapse and the stale-gap collapse tests fail with `AssertionError: assert 'NONE' == 'HALT'` (no depth check exists yet). First-observation / just-over / noise-floor guards pass and pin the boundary after Step 3.

- [ ] **Step 3: Minimal implementation**

In `src/polybot/ers/anomaly.py`:

1. Ensure the module has `from decimal import Decimal` at the top (add if the spine does not already import it).
2. At the end of `AnomalyMonitor.__init__`, directly under `self._prev_mid = {}`, add:

```python
        self._prev_depth = {}  # token_id -> top-of-book depth at that same valid observation
```

3. Replace `_check_abnormal_book` entirely with:

```python
    def _check_abnormal_book(self, positions, book_for, triggers):
        """L5 trigger 1 (DESIGN-S4.4 Â§3): structural + depth-collapse + midpoint-jump legs.
        Depth = top-of-book bid_size + ask_size; collapse fires when prev_depth >=
        caps.depth_collapse_min_prev_shares (the >= floor is pinned by the exactly-1000 test)
        AND depth <= prev_depth * (1 - caps.depth_collapse_fraction) (the <= compare is
        pinned by the exactly-200 test). Prev memory updates AFTER comparisons and ONLY on
        a valid non-stale mid -- a stale interlude preserves the last VALID baseline."""
        for pos in positions:
            token = pos.token_id
            book = book_for(token)
            if book is None:
                continue
            if book.is_stale():
                continue
            mid = book.midpoint()
            if mid is None:
                triggers.append(REASON_L5_ABNORMAL_BOOK)  # crossed/locked/empty side
                continue
            _bid, bid_size, _ask, ask_size = book.top_of_book()
            depth = ((bid_size if bid_size is not None else Decimal("0"))
                     + (ask_size if ask_size is not None else Decimal("0")))
            prev_depth = self._prev_depth.get(token)
            if (prev_depth is not None
                    and prev_depth >= self._caps.depth_collapse_min_prev_shares
                    and depth <= prev_depth * (Decimal(1) - self._caps.depth_collapse_fraction)):
                triggers.append(REASON_L5_ABNORMAL_BOOK)
            prev_mid = self._prev_mid.get(token)
            if prev_mid is not None and abs(mid - prev_mid) >= self._caps.midpoint_jump_halt:
                triggers.append(REASON_L5_ABNORMAL_BOOK)
            self._prev_mid[token] = mid      # AFTER comparisons; valid non-stale mids only
            self._prev_depth[token] = depth
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```

Expected: `17 passed` in the file; full suite 0 failures.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_book.py && git commit -m "S4.4c: depth-collapse halt (>=80 pct top-of-book drop over a >=1000-share prev floor) with per-token prev-depth memory"'
```

---

### Task C4: `l5_abnormal_book` fires ONCE per cycle â€” token dedupe + single append

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (replace `_check_abnormal_book` â€” final form)
- Test: `tests/test_ers_anomaly_book.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_ers_anomaly_book.py`)

```python
def test_two_positions_on_the_same_token_fire_l5_abnormal_book_once():
    # Pinned contract: check every position's token, DEDUPE tokens. Kills: iterating
    # positions without a seen-set (a shared crossed book double-appends the trigger).
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")  # crossed
    state = mon.evaluate([_pos("t1"), _pos("t1")], lambda token: book)
    assert state.action == HALT
    assert state.triggers.count(REASON_L5_ABNORMAL_BOOK) == 1


def test_simultaneous_jump_and_collapse_fire_l5_abnormal_book_once():
    # Pinned contract: all three checks fire the SAME trigger string ONCE, not three times.
    # Cycle 2 trips BOTH the jump (0.50 -> 0.65 = 0.15 >= 0.15) and the collapse
    # (1000 -> 200 <= 200). Kills: appending per-condition instead of once per cycle.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # mid 0.50 depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.64", ask="0.66",
                                                           bid_size="100", ask_size="100"))  # mid 0.65 depth 200
    assert state.action == HALT
    assert state.triggers.count(REASON_L5_ABNORMAL_BOOK) == 1
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
```

Expected: `2 failed, 17 passed` â€” both new tests fail with `AssertionError: assert 2 == 1` (the C3 implementation appends once per firing position/condition, so the trigger appears twice).

- [ ] **Step 3: Minimal implementation**

In `src/polybot/ers/anomaly.py`, replace `_check_abnormal_book` entirely with its final form:

```python
    def _check_abnormal_book(self, positions, book_for, triggers):
        """L5 trigger 1 (DESIGN-S4.4 Â§3): crossed/locked/empty-side, depth-collapse and
        midpoint-jump on HELD tokens with NON-stale books. Tokens are DEDUPED (many
        positions can share one token -- its book is checked once per cycle) and all three
        checks fire the SAME reason string at most ONCE per cycle (the count==1 tests pin
        both). Frozen positions are NOT skipped (book structure, not P&L). Per-token
        prev-mid/prev-depth memory updates AFTER comparisons and ONLY on a valid non-stale
        mid, so first observation never fires jump/collapse and a stale interlude preserves
        the last VALID baseline. Stale books -> breaker/validator domain; absent books ->
        validator no_book domain."""
        abnormal = False
        seen = set()
        for pos in positions:
            token = pos.token_id
            if token in seen:
                continue  # dedupe: one structural check per token per cycle
            seen.add(token)
            book = book_for(token)
            if book is None:
                continue
            if book.is_stale():
                continue
            mid = book.midpoint()
            if mid is None:
                abnormal = True  # non-stale yet mid-less = crossed/locked/empty side
                continue
            _bid, bid_size, _ask, ask_size = book.top_of_book()
            depth = ((bid_size if bid_size is not None else Decimal("0"))
                     + (ask_size if ask_size is not None else Decimal("0")))
            prev_depth = self._prev_depth.get(token)
            if (prev_depth is not None
                    and prev_depth >= self._caps.depth_collapse_min_prev_shares
                    and depth <= prev_depth * (Decimal(1) - self._caps.depth_collapse_fraction)):
                abnormal = True
            prev_mid = self._prev_mid.get(token)
            if prev_mid is not None and abs(mid - prev_mid) >= self._caps.midpoint_jump_halt:
                abnormal = True
            self._prev_mid[token] = mid      # AFTER comparisons; valid non-stale mids only
            self._prev_depth[token] = depth
        if abnormal:
            triggers.append(REASON_L5_ABNORMAL_BOOK)  # once per cycle, never per check
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_book.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```

Expected: `19 passed` in the file; full suite 0 failures (556 pre-S4.4 baseline + S4.4a/b additions + 19 here).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_book.py && git commit -m "S4.4c: l5_abnormal_book fires once per cycle -- token dedupe plus single append across the three book checks"'
```

---

## Sub-slice D: WS health (S4.4d â€” `last_frame_at` accessors + the `l5_ws_down` seam path)

> Build-order preconditions (already on `pol-6-s4.4-anomaly` when D starts): S4.4a spine (`src/polybot/ers/anomaly.py` with `AnomalyMonitor.__init__` accepting/storing every seam incl. `ws_last_frame_at`, `REASON_L5_WS_DOWN` in `src/polybot/ers/safety.py`) and S4.4b caps (`ws_staleness_halt_seconds: int = 30` on `RiskCaps`). Re-read `src/polybot/ers/anomaly.py` before Tasks D4/D5 â€” the code below uses the contract's mirror-shape attribute names (`self._caps`, `self._clock`, `self._ws_last_frame_at`).
>
> Clock domains (pinned, repeated in every monitor test): the monitor's `clock=` is float monotonic **SECONDS** (`time.monotonic`); `MarketStream` frame stamps are `MonotonicStamper`-domain **NANOSECONDS** (`time.monotonic_ns`) â€” the *same* monotonic family, so `age_s = now_s - last_frame_at_ns / 1e9`. Tests inject both explicitly (e.g. `clock=lambda: 100.0` with a frame stamp of `60_000_000_000` ns = 40 s age).
>
> Decision (from reading `market_stream.py` in full): the real-venue-frame stamping sites are (1) the `book` snapshot path (line 106), (2) the pre-snapshot archived tracked delta (line 142), (3) the applied `price_change` per-asset path (line 149). Benign-ignored event types (`last_trade_price` / `tick_size_change`, line 99â€“100) never stamp and therefore do NOT refresh health (conservative â€” only bookable venue frames prove liveness; errs toward staleness = fail-closed). Synthetic-event stamps in `_emit_synthetic` (line 186) are derived events, NOT venue frames â€” they must never count.

### Task D1: `MarketStream.last_frame_at()` â€” non-consuming accessor, book-snapshot path
**Files:**
- Modify: `src/polybot/ingestion/market_stream.py` (`__init__` after line 61 `self._clean_progress = False`; book path line 106; new methods after `consume_clean_progress`, line 95)
- Test: `tests/test_market_stream.py` (append at end of file)

- [ ] **Step 1: Write the failing test**

```python
# --- S4.4d: non-consuming WS-health read last_frame_at() -----------------------
# Clock-domain note: stamps are MonotonicStamper-domain NANOSECONDS
# (time.monotonic_ns family); the L5 monitor converts age_s = now_s - stamp/1e9.


def test_last_frame_at_is_none_before_any_frame():
    """Kills: initializing _last_frame_at to 0/now instead of None -- a stream that
    never saw a frame must read as None so the WS sentinel's wired-but-silent
    (+inf age) fail-closed path fires."""
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    assert stream.last_frame_at() is None


def test_last_frame_at_returns_the_book_snapshot_dispatch_stamp():
    """Kills: recording a FRESH stamper stamp instead of THE dispatched frame's
    observed_at (they would differ -- every stamp is unique)."""
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    observed_at = stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))

    assert stream.last_frame_at() == observed_at


def test_last_frame_at_is_non_consuming_and_does_not_clear_the_consume_flags():
    """Kills: implementing last_frame_at with the read-and-clear consume_* pattern,
    or routing it through consume_resync_request/consume_clean_progress state."""
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))  # clean delta

    first = stream.last_frame_at()
    second = stream.last_frame_at()

    assert first is not None and first == second     # repeated reads: same value
    assert stream.consume_clean_progress() is True   # clean-progress flag survived the reads

    stream.ingest(_price_change(("A", "0.615", "BUY", "50", "0.70", "0.62")))  # gap
    stream.last_frame_at()                           # a health read between gap and consume
    assert stream.consume_resync_request() is True   # the resync request survived too


def test_last_frame_at_not_refreshed_by_benign_ignored_event_types():
    """Kills: stamping/recording in the _BENIGN_IGNORED early-return. last_trade_price
    is recognized-but-unbooked; it never stamps, so it must not refresh health
    (conservative: only bookable venue frames prove the socket is alive)."""
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))

    result = stream.ingest({"event_type": "last_trade_price", "asset_id": "A"})

    assert result is None
    assert stream.last_frame_at() is None
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_market_stream.py -q -k last_frame_at'
```
Expected: `4 failed` â€” every test with `AttributeError: 'MarketStream' object has no attribute 'last_frame_at'`.

- [ ] **Step 3: Minimal implementation**

In `__init__`, directly after `self._clean_progress = False` (line 61):

```python
        self._last_frame_at = None  # stamper-ns of the last dispatched REAL venue frame (S4.4d)
```

After `consume_clean_progress` (below line 95), add:

```python
    def last_frame_at(self):
        """Stamper-ns ``observed_at`` of the last dispatched REAL venue frame;
        ``None`` before any frame (the L5 WS sentinel treats wired-but-None as
        +inf age = down). NON-consuming, unlike ``consume_resync_request`` /
        ``consume_clean_progress``: the health check reads it every cycle.
        Benign-ignored event types (``last_trade_price`` / ``tick_size_change``)
        never stamp, so they do not refresh health -- conservative: only bookable
        venue frames prove liveness (errs toward staleness, i.e. fail-closed).
        """
        return self._last_frame_at

    def _stamp_frame(self):
        # Stamp a REAL venue frame at dispatch and record it for last_frame_at().
        # Synthetic events (_emit_synthetic) keep calling the stamper directly:
        # derived events are NOT venue frames and must never refresh WS health.
        observed_at = self._stamper.stamp()
        self._last_frame_at = observed_at
        return observed_at
```

In the `book` path, change line 106:

```python
        observed_at = self._stamp_frame()  # stamp at dispatch, before book mutation
```
(was `observed_at = self._stamper.stamp()  # stamp at dispatch, before book mutation`).

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_market_stream.py -q -k last_frame_at'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```
Expected: `4 passed`; full suite 0 failed (556 baseline + S4.4aâ€“c's tests already on the branch + these 4).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ingestion/market_stream.py tests/test_market_stream.py && git commit -m "S4.4d: MarketStream.last_frame_at() non-consuming WS-health read (book path)"'
```

### Task D2: `last_frame_at` covers both `price_change` dispatch paths; synthetic stamps excluded
**Files:**
- Modify: `src/polybot/ingestion/market_stream.py` (pre-snapshot stamp, line 142; applied-delta stamp, line 149)
- Test: `tests/test_market_stream.py` (append at end of file; `_detector` helper already exists at line 396)

- [ ] **Step 1: Write the failing test**

```python
def test_last_frame_at_advances_to_the_applied_price_change_stamp():
    """Kills: recording only in the book-snapshot path (leaving the applied
    price_change dispatch site on the raw stamper)."""
    stream = MarketStream(MonotonicStamper(clock=lambda: 1))
    stream.ingest(_book("A", [("0.60", "100")], [("0.62", "100")]))
    snapshot_stamp = stream.last_frame_at()

    stamps = stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

    assert stream.last_frame_at() == stamps[-1]
    assert stream.last_frame_at() > snapshot_stamp


def test_last_frame_at_advances_on_a_pre_snapshot_archived_delta():
    """A SUBSCRIBED asset's delta landing before its snapshot is stamped+archived but
    never APPLIED -- it is still a real venue frame, so it proves the socket is alive
    and must refresh health. Kills: recording only in the applied-delta branch."""
    seen = []
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=seen.append, asset_ids=["A"])

    stamps = stream.ingest(_price_change(("A", "0.61", "BUY", "50", "0.61", "0.62")))

    assert stream.book_for("A") is None           # not applied (no baseline)...
    assert stream.last_frame_at() == stamps[-1]   # ...but health still refreshed


def test_last_frame_at_records_the_venue_frame_stamp_not_the_synthetic_stamp():
    """Kills: recording inside _emit_synthetic -- a DERIVED event would masquerade
    as venue liveness. Health must equal the triggering venue frame's observed_at,
    never the synthetic event's own (later) stamp."""
    market, synth = [], []
    det = _detector(large_print_size="5000", min_evaporation_size="1000000")
    stream = MarketStream(MonotonicStamper(clock=lambda: 1), sink=market.append,
                          detector=det, synthetic_sink=synth.append)
    stream.ingest(_book("A", [("0.60", "8000")], [("0.62", "100")]))

    stream.ingest(_price_change(("A", "0.60", "BUY", "500", "0.60", "0.62")))  # -> large_print

    assert [o.event_type for o in synth] == ["large_print"]   # a synthetic DID fire
    assert stream.last_frame_at() == market[-1].observed_at   # health == the venue frame's stamp
    assert stream.last_frame_at() < synth[0].observed_at      # NOT the later synthetic stamp
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_market_stream.py -q -k last_frame_at'
```
Expected: `3 failed, 4 passed` â€” the three new tests fail with `AssertionError` on their `stream.last_frame_at() == ...` line (the applied test still reads the earlier book-snapshot stamp; the pre-snapshot test reads `None`; the synthetic test reads the book stamp instead of `market[-1].observed_at`). D1's four stay green.

- [ ] **Step 3: Minimal implementation**

In `_ingest_price_change`, change the pre-snapshot archive site (line 142):

```python
                    observed_at = self._stamp_frame()
```
(was `observed_at = self._stamper.stamp()`), and the applied-delta site (line 149):

```python
            observed_at = self._stamp_frame()  # stamp before mutation, per tracked asset
```
(was `observed_at = self._stamper.stamp()  # stamp before mutation, per tracked asset`).

`_emit_synthetic` (line 186) is deliberately left on `self._stamper.stamp()` â€” the mutation the third test kills.

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_market_stream.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```
Expected: test file all passed (`7` new + the pre-existing ones); full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ingestion/market_stream.py tests/test_market_stream.py && git commit -m "S4.4d: last_frame_at covers every real venue-frame dispatch; synthetic/benign excluded"'
```

### Task D3: `ShardedMarketCollector.last_frame_at()` â€” fail-closed min across shards
**Files:**
- Modify: `src/polybot/ingestion/sharding.py` (new method after `book_for`, line 81)
- Test: `tests/test_sharding.py` (append at end of file; reuses the module's `FakeTransport`, `_book_frame`, `_connect_from` helpers)

- [ ] **Step 1: Write the failing test**

```python
# --- S4.4d: collector-level WS health = the LAGGING shard's health -------------


def test_collector_last_frame_at_is_none_before_any_frame():
    """Kills: initializing to 0/now -- an unstarted collector must read as
    never-saw-a-frame so the L5 wired-but-silent path fires."""
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(_connect_from([]), stamper, ["A", "B"],
                                       max_assets_per_shard=1)

    assert collector.last_frame_at() is None


def test_collector_last_frame_at_is_the_min_across_shards():
    """Collector health is the OLDEST shard stamp: one lagging shard defines the
    whole collector (fail-closed). Kills: min()->max() (the freshest shard would
    mask a lagging sibling) and any single-shard read."""
    observed = []
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([_book_frame("B", "0.40", "0.45")])
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"],
        sink=lambda obs: observed.append(obs.observed_at), max_assets_per_shard=1,
    )

    asyncio.run(collector.run(max_connections=1))

    assert len(observed) == 2 and len(set(observed)) == 2
    assert collector.last_frame_at() == min(observed)  # the OLDER shard stamp, not the newer


def test_collector_last_frame_at_is_none_when_any_shard_has_no_frame_yet():
    """Fail-closed: a shard that never received a frame = +inf staleness for the
    WHOLE collector, not 'min of the shards that did'. Kills: skipping None shards
    in the aggregation."""
    t0 = FakeTransport([_book_frame("A", "0.60", "0.62")])
    t1 = FakeTransport([])  # shard B connects+subscribes but never receives a frame
    stamper = MonotonicStamper(clock=lambda: 1)
    collector = ShardedMarketCollector(
        _connect_from([t0, t1]), stamper, ["A", "B"], max_assets_per_shard=1,
    )

    asyncio.run(collector.run(max_connections=1))

    assert collector.book_for("A") is not None   # shard A DID stream
    assert collector.last_frame_at() is None     # but shard B's silence wins (fail-closed)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_sharding.py -q -k last_frame_at'
```
Expected: `3 failed` â€” every test with `AttributeError: 'ShardedMarketCollector' object has no attribute 'last_frame_at'`.

- [ ] **Step 3: Minimal implementation**

In `sharding.py`, after `book_for` (line 81), add:

```python
    def last_frame_at(self):
        """MIN of the shard streams' ``last_frame_at`` (stamper-ns): collector
        health is the LAGGING shard's health. ``None`` if there are no shards or
        if ANY shard has not seen a frame yet -- one dead/silent shard means the
        collector cannot vouch for the whole universe (fail-closed; the L5 WS
        sentinel reads None as +inf age = down). Non-consuming.
        """
        if not self._shards:
            return None
        stamps = [stream.last_frame_at() for stream, _socket in self._shards]
        if any(stamp is None for stamp in stamps):
            return None
        return min(stamps)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_sharding.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```
Expected: test file all passed; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ingestion/sharding.py tests/test_sharding.py && git commit -m "S4.4d: ShardedMarketCollector.last_frame_at() = fail-closed min across shards"'
```

### Task D4: `l5_ws_down` seam path â€” dormant / wired-but-silent / raising, severity-last
**Files:**
- Create: `tests/test_ers_anomaly_ws.py`
- Modify: `src/polybot/ers/anomaly.py` (`AnomalyMonitor.evaluate` â€” insert the ws consult AFTER the `l5_api_storm` consult, i.e. as the LAST seam in severity order, before the triggersâ†’state return; read the file first and confirm the spine's attribute names `self._caps` / `self._clock` / `self._ws_last_frame_at` and that `REASON_L5_WS_DOWN` is imported from `polybot.ers.safety` â€” add it to the existing import line if S4.4a did not)

- [ ] **Step 1: Write the failing test**

```python
"""S4.4d / POL-6 -- L5 AnomalyMonitor: the l5_ws_down seam path.

CLOCK DOMAINS (pinned; the S4.5 lesson): the monitor's clock= is float monotonic
SECONDS (time.monotonic in prod); MarketStream frame stamps are MonotonicStamper-
domain NANOSECONDS (time.monotonic_ns) -- the SAME monotonic family, so
age_s = now_s - last_frame_at_ns / 1e9. Tests inject BOTH explicitly
(e.g. clock=lambda: 100.0 with a frame stamp of 60_000_000_000 ns = 40 s age).
"""

import types

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_CLOCK_SKEW, REASON_L5_WS_DOWN


def _no_books(_token):
    """book_for stub: the ws path never touches books (positions stay empty)."""
    return None


def _monitor(**seams):
    """A monitor at now=100.0 monotonic-SECONDS; every unlisted seam stays None (dormant)."""
    return AnomalyMonitor(RiskCaps(), clock=seams.pop("clock", lambda: 100.0), **seams)


def _skewed_sentinel():
    """Duck-typed skew sentinel (.skewed() -> bool), always skewed."""
    return types.SimpleNamespace(skewed=lambda: True)


def test_ws_seam_none_keeps_the_ws_trigger_dormant():
    """Kills: consulting the seam without the `is not None` guard -- calling a None
    seam raises TypeError, and the fail-closed except would then fire a false
    l5_ws_down on every bare monitor."""
    state = _monitor().evaluate((), _no_books)

    assert state.action == NONE
    assert state.triggers == ()


def test_ws_wired_but_silent_none_stamp_fires_ws_down():
    """A WIRED callable returning None = never saw a frame = +inf age -> down
    (mirrors the heartbeat's fail-closed stance). Kills: treating None as 'skip'
    (the recon-seam semantic) instead of 'fire'."""
    state = _monitor(ws_last_frame_at=lambda: None).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_raising_seam_fails_closed_and_fires_ws_down():
    """FAIL-CLOSED SEAM RULE: a raising seam IS the anomaly it guards. Kills:
    letting the exception propagate out of evaluate, or masking it silently."""
    def _boom():
        raise RuntimeError("socket introspection exploded")

    state = _monitor(ws_last_frame_at=_boom).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_down_is_collected_after_clock_skew_in_severity_order():
    """SEVERITY ORDER: ws is the LAST consult, so triggers[0] (the set_state reason)
    must be the skew when both fire. Kills: appending ws ahead of the other seams."""
    state = _monitor(skew_sentinel=_skewed_sentinel(),
                     ws_last_frame_at=lambda: None).evaluate((), _no_books)

    assert state.action == HALT
    assert state.triggers == (REASON_L5_CLOCK_SKEW, REASON_L5_WS_DOWN)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_ws.py -q'
```
Expected: `3 failed, 1 passed` â€” the silent/raising/severity tests fail with `AssertionError` (`state.action == HALT` is False: the spine has no ws consult yet, so `NONE`/`(l5_clock_skew,)` comes back). `test_ws_seam_none_keeps_the_ws_trigger_dormant` passes by construction â€” it is the pre-existing spine invariant this task must not break, and after Step 3 it kills the dropped-`is not None`-guard mutant.

- [ ] **Step 3: Minimal implementation**

In `AnomalyMonitor.evaluate`, insert as the LAST seam consult (after the `l5_api_storm` block, before the collected-triggers return; ws is lowest severity):

```python
        # --- l5_ws_down (LAST in severity order) --------------------------------
        # Clock domains: self._clock is float monotonic SECONDS (time.monotonic);
        # the seam returns MonotonicStamper-domain NANOSECONDS (time.monotonic_ns)
        # -- the same monotonic family (DESIGN-S4.4 Â§2). Age compare lands in D5.
        if self._ws_last_frame_at is not None:
            try:
                if self._ws_last_frame_at() is None:
                    # Wired but never saw a frame: +inf age -> down (fail-closed).
                    triggers.append(REASON_L5_WS_DOWN)
            except Exception:
                # FAIL-CLOSED SEAM RULE: a raising seam fires its own trigger.
                triggers.append(REASON_L5_WS_DOWN)
```

If `REASON_L5_WS_DOWN` is not already in `anomaly.py`'s `from polybot.ers.safety import ...` line, add it there.

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_ws.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```
Expected: `4 passed`; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_ws.py && git commit -m "S4.4d: AnomalyMonitor l5_ws_down seam - dormant/silent/raising fail-closed paths"'
```

### Task D5: `l5_ws_down` staleness compare â€” nsâ†’s conversion + strict-`>` boundary pair
**Files:**
- Modify: `src/polybot/ers/anomaly.py` (extend the D4 ws block in `evaluate` with the age compare)
- Test: `tests/test_ers_anomaly_ws.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_ws_age_exactly_at_the_staleness_cap_does_not_fire():
    """Boundary pair, at-threshold half: the compare is STRICT `age_s > cap`.
    Clock domains: monitor clock = 100.0 monotonic SECONDS; frame stamp =
    70_000_000_000 ns = 70.0 s -> age exactly 30.0 s == ws_staleness_halt_seconds
    (30, the S4.4b default). Kills: mutating `>` to `>=`. (100.0 - 70.0 == 30.0 is
    exact in binary floats -- no epsilon flake.)"""
    state = _monitor(ws_last_frame_at=lambda: 70_000_000_000).evaluate((), _no_books)

    assert state.action == NONE
    assert state.triggers == ()


def test_ws_age_just_over_the_staleness_cap_fires_ws_down():
    """Boundary pair, just-over half: stamp 69_000_000_000 ns = 69.0 s ->
    age 31.0 s > 30. Kills: mutating `>` to `<`, deleting the age compare, or
    dropping the /1e9 (age would be 100.0 - 6.9e10, hugely negative -> no fire)."""
    state = _monitor(ws_last_frame_at=lambda: 69_000_000_000).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_stamp_is_nanoseconds_and_must_be_divided_to_seconds():
    """Pins the ns->s conversion (age_s = now_s - last_ns / 1e9): stamp
    60_000_000_000 ns = 60.0 s against clock 100.0 s -> 40 s age -> fires.
    Monitor clock (monotonic seconds) and stamper ns are the SAME monotonic
    family; both are injected explicitly here. Kills: any mutant that compares
    in the wrong unit (raw-ns age is negative and never fires)."""
    state = _monitor(ws_last_frame_at=lambda: 60_000_000_000).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_fresh_frame_within_the_cap_does_not_fire():
    """Stamp 99_000_000_000 ns = 99.0 s -> age 1.0 s -> quiet. Kills: comparing the
    converted STAMP itself against the cap (99.0 > 30 would fire) instead of the
    AGE, and inverting the compare."""
    state = _monitor(ws_last_frame_at=lambda: 99_000_000_000).evaluate((), _no_books)

    assert state.action == NONE
    assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_ws.py -q'
```
Expected: `2 failed, 6 passed` â€” `just_over` and `nanoseconds` fail with `AssertionError` (`state.action == HALT` is False: D4's block fires only on `None`, no age compare exists yet). The two no-fire tests pass trivially pre-implementation and become load-bearing boundary/unit pins once the compare lands. D4's four stay green.

- [ ] **Step 3: Minimal implementation**

In `AnomalyMonitor.evaluate`, replace the D4 ws block with the full compare (exact old block from D4 Step 3):

```python
        # --- l5_ws_down (LAST in severity order) --------------------------------
        # Clock domains: self._clock is float monotonic SECONDS (time.monotonic);
        # the seam returns MonotonicStamper-domain NANOSECONDS (time.monotonic_ns)
        # -- the same monotonic family (DESIGN-S4.4 Â§2), so
        # age_s = now_s - last_ns / 1e9 is exact (the stamper's +1ns uniqueness
        # nudges are noise at a 30s tolerance).
        if self._ws_last_frame_at is not None:
            try:
                last = self._ws_last_frame_at()
                if last is None:
                    # Wired but never saw a frame: +inf age -> down (fail-closed).
                    triggers.append(REASON_L5_WS_DOWN)
                elif self._clock() - (last / 1e9) > self._caps.ws_staleness_halt_seconds:
                    # STRICT >: age exactly AT the cap does not fire (boundary pair).
                    triggers.append(REASON_L5_WS_DOWN)
            except Exception:
                # FAIL-CLOSED SEAM RULE: a raising seam fires its own trigger.
                triggers.append(REASON_L5_WS_DOWN)
```

- [ ] **Step 4: Run the test â€” PASS; run the full suite â€” all green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_ws.py -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'
```
Expected: `8 passed`; full suite 0 failed (556 baseline + S4.4aâ€“c + the 14 new S4.4d tests: 7 market_stream + 3 sharding + 8 anomaly-ws... note the anomaly-ws file totals 8, giving 18 new across D1â€“D5).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/anomaly.py tests/test_ers_anomaly_ws.py && git commit -m "S4.4d: l5_ws_down staleness compare - ns->s conversion + strict > boundary"'
```

---

## Sub-slice E: recon cadence + canary scheduler + dispute stub + the Â§8.3 e2e

**Builds on:** S4.4aâ€“d already landed on `pol-6-s4.4-anomaly` (the `AnomalyMonitor` spine with all seam kwargs stored breaker-style as `self._caps` / `self._clock` / `self._recon_provider` / `self._canary` / `self._dispute_flagger`, a `triggers` list collected in the pinned severity order with `action = HALT if triggers else NONE`, the `REASON_L5_*` constants in `ers/safety.py`, the new `RiskCaps` fields, and `ERSController(anomaly=)` with the edge-triggered one-shot halt/cancel/audit). All tests go in NEW `tests/test_ers_anomaly_recon_canary.py`. Verified against the current tree: `ReconResult`/`ThreeWayReconciler`/leg parsers (`src/polybot/ers/reconcile.py:16-201`), `IntentStore.record_op_event`/`op_audit_log`/`fills_log` (`intent_store.py:194-231`), `PaperSigner` lists + `cancel_all` marker `{"cancelled": "working_entries"}` (`service.py:279-319`), `SafetyController.set_state` audits `("state_change", reason, op_state)` (`safety.py:74-87`), `OpenPosition` (`validator.py:36-49`), `Envelope` (`core/models.py:30-46`), `LocalBook.apply_book` sets `_stale=False` (`ingestion/orderbook.py:21-25`).

---

### Task E1: `make_recon_provider` â€” shadow (wallet=None) short-circuit never scans the event store

**Files:**
- Create: `tests/test_ers_anomaly_recon_canary.py`
- Modify: `src/polybot/ers/reconcile.py` (append after `ThreeWayReconciler.reconcile`, end of file, after current line 201)

- [ ] **Step 1: Write the failing test** â€” create the test file with the full helper preamble plus the first test:

```python
"""S4.4e (POL-6): per-cycle reconcile cadence + signing-canary scheduler + dispute stub + the e2e.

Pins make_recon_provider (the 0-arg recon_provider seam factory; the shadow short-circuit is
proven with a RAISING event store), the AnomalyMonitor recon consult (DIVERGED and any UNKNOWN
status fire l5_recon_mismatch; OK/DORMANT/SETTLING do not; a raising provider fires -- the
fail-closed seam rule), the canary scheduler (first evaluate due, `>=` interval re-due, at most
one call per cycle, falsy/raise -> l5_canary_fail, NEVER blind-retried), the inert
dispute_flagger stub seam, and the DESIGN-S4.4 Â§8.3 e2e on the real assembly. Helpers are
module-level copies (no conftest); clocks are injected 0-arg callables; money is Decimal from
string literals.
"""

import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.core.models import Envelope
from polybot.ers import safety as _safety
from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    ReconResult,
    ThreeWayReconciler,
    make_recon_provider,
)
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition
from polybot.ingestion.orderbook import LocalBook

WALLET = "0xcafe000000000000000000000000000000000001"


# --- module-level helpers (per-file copies by convention; no conftest) ------------------------

def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _pos(token="t1"):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal("8"), matrix_cold=False, token_id=token,
                        entry_price=Decimal("0.50"), frozen=False)


def _recon(status):
    return ReconResult(status=status, divergences=(), onchain_confirmed_exposure=Decimal("0"),
                       settling_tokens=(), triggers=())


def _clock_box(start=0.0):
    """Injected 0-arg monitor clock (float monotonic SECONDS) + the mutable box that advances it."""
    box = {"now": float(start)}
    return (lambda: box["now"]), box


def _fill(token, side, shares, at, *, intent="i1"):
    # Mirrors IntentStore.fills_log() row shape (S4.5a): Decimals already converted.
    return {"at": at, "intent_id": intent, "token_id": token, "condition_id": "0xcond",
            "event_id": "evt", "side": side, "shares": Decimal(shares),
            "price_exec": Decimal("0.50"), "worst_case_risk": Decimal(shares) * Decimal("0.50")}


class _FillsStore:
    """Stub of the ONE IntentStore method make_recon_provider reads: fills_log()."""

    def __init__(self, rows=()):
        self._rows = list(rows)

    def fills_log(self):
        return list(self._rows)


class _EventStore:
    """Stub of the ONE event-store method make_recon_provider reads: all() -> Envelopes."""

    def __init__(self, envelopes=()):
        self._envelopes = list(envelopes)

    def all(self):
        return list(self._envelopes)


class _RaisingEventStore:
    """Proves the wallet=None short-circuit: ANY scan of the event store blows the test up."""

    def all(self):
        raise AssertionError("event_store.all() must not be scanned when wallet is None")


def _positions_env(asset, size, *, eid_suffix="0xwallet"):
    # Mirrors data_api.py: content is json.dumps(item); event_id is "/positions:<id>".
    item = {"asset": asset, "size": size, "conditionId": "0xcond"}
    return Envelope(source="data-api", source_tier="DATA",
                    event_id=f"/positions:{eid_suffix}", observed_at=1,
                    content=json.dumps(item, sort_keys=True, default=str))


def _chain_env(event, *, eid="0xtx:0"):
    # Mirrors polygon.py: content is json.dumps({"log": log, "event": event}).
    return Envelope(source="polygon-chain", source_tier="CHAIN", event_id=eid,
                    observed_at=1, content=json.dumps({"log": {}, "event": event},
                                                      sort_keys=True, default=str))


def _single(frm, to, token, value):
    return {"kind": "transfer_single", "operator": "0xop", "from": frm, "to": to,
            "token_id": token, "value": value}


# --- Task E1: make_recon_provider shadow short-circuit ----------------------------------------

def test_make_recon_provider_wallet_none_short_circuits_to_dormant_without_scanning_event_store():
    """Shadow path (wallet=None): the provider must call reconciler.reconcile({}, {}, None,
    wallet=None, now=clock_ns()) WITHOUT touching the event store -- the RAISING event store
    kills the mutation that drops the short-circuit and always builds the three legs."""
    provider = make_recon_provider(_FillsStore(), _RaisingEventStore(),
                                   ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=None, clock_ns=lambda: 0)
    result = provider()
    assert result.status == DORMANT
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: collection error â€” `ImportError: cannot import name 'make_recon_provider' from 'polybot.ers.reconcile'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/reconcile.py` (end of file). Deliberately minimal: only the shadow branch exists, so Task E2's wallet-set tests stay RED until E2:

```python
def make_recon_provider(store, event_store, reconciler, *, wallet, clock_ns):
    """Bind the per-cycle reconcile cadence (S4.4e) into the 0-arg ``recon_provider=`` seam the
    AnomalyMonitor consults. ``clock_ns`` is a 0-arg callable in the MonotonicStamper
    monotonic-ns domain (ReconResult's settle window lives there -- NOT the monitor's
    float-seconds clock). Shadow (wallet=None) short-circuits STRAIGHT to the reconciler's
    DORMANT path without scanning the event store -- cheap enough to run every cycle until a
    POL-4 wallet exists."""
    def _provider():
        return reconciler.reconcile({}, {}, None, wallet=None, now=clock_ns())
    return _provider
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `1 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed (556 pre-S4.4 baseline + all S4.4aâ€“d tests + this one)

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/reconcile.py && git commit -m "feat(anomaly): make_recon_provider shadow short-circuit -- wallet=None never scans the event store (S4.4e)"'`

---

### Task E2: `make_recon_provider` â€” a real wallet builds the three legs

**Files:**
- Modify: `src/polybot/ers/reconcile.py` (the `_provider` body added in E1, ~lines 204â€“212 post-E1)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E2: make_recon_provider with a real wallet builds the three legs ---------------------

def test_make_recon_provider_with_wallet_agreement_across_three_legs_is_ok():
    """wallet set: the provider folds internal_balances(store.fills_log(), in_session=True) +
    clob_balances(event_store.all()) + onchain_balances(event_store.all(), wallet=wallet) and
    hands them to the reconciler. 5 internal shares vs 5 on-chain shares (raw 5_000_000 /
    10**6) agree -> OK. Kills the mutation that keeps the E1 shadow short-circuit for a real
    wallet (that would report DORMANT, not OK)."""
    store = _FillsStore([_fill("42", "BUY", "5", at=0)])
    events = _EventStore([_positions_env("42", "5"),
                          _chain_env(_single("0xseller", WALLET, "42", "5000000"))])
    provider = make_recon_provider(store, events, ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=WALLET, clock_ns=lambda: 200_000_000_000)
    assert provider().status == OK


def test_make_recon_provider_with_wallet_unexplained_internal_excess_is_diverged():
    """wallet set: 5 internal shares with NO on-chain transfer is a $5 delta over the $0.50
    tolerance, and now (200s in ns) is far past the fill's settle window (fill at=0, window
    90s) -> DIVERGED with the token named. Kills the mutation that drops/swaps the internal
    leg (an empty internal fold would read OK)."""
    store = _FillsStore([_fill("42", "BUY", "5", at=0)])
    events = _EventStore([_positions_env("42", "0", eid_suffix="0xother")])
    provider = make_recon_provider(store, events, ThreeWayReconciler(caps=RiskCaps()),
                                   wallet=WALLET, clock_ns=lambda: 200_000_000_000)
    result = provider()
    assert result.status == DIVERGED
    assert [d.token_id for d in result.divergences] == ["42"]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: 2 failed â€” both with `AssertionError: assert 'DORMANT' == 'OK'` / `assert 'DORMANT' == 'DIVERGED'` (the E1 stub always short-circuits).

- [ ] **Step 3: Minimal implementation** â€” replace the `_provider` body in `make_recon_provider` (docstring unchanged):

```python
    def _provider():
        if wallet is None:
            return reconciler.reconcile({}, {}, None, wallet=None, now=clock_ns())
        envelopes = event_store.all()  # ONE scan per cycle feeds BOTH external legs
        return reconciler.reconcile(
            internal_balances(store.fills_log(), in_session=True),
            clob_balances(envelopes),
            onchain_balances(envelopes, wallet=wallet),
            wallet=wallet, now=clock_ns())
    return _provider
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `3 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/reconcile.py && git commit -m "feat(anomaly): make_recon_provider builds the three reconcile legs for a real wallet (S4.4e)"'`

---

### Task E3: monitor recon consult â€” DIVERGED fires, benign statuses do not

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (file lands in S4.4a â€” no stable line refs; anchor: inside `AnomalyMonitor.evaluate`, immediately AFTER the `l5_clock_skew` consult and BEFORE the canary/abnormal-book sections, per the pinned severity order; plus the module imports)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E3: the recon_provider seam in AnomalyMonitor.evaluate ------------------------------

def test_recon_seam_diverged_status_fires_l5_recon_mismatch_halt():
    """A recon_provider returning DIVERGED must HALT with l5_recon_mismatch as triggers[0]
    (the set_state reason). Kills: dropping the recon consult from evaluate entirely."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: _recon(DIVERGED))
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert state.triggers[0] == "l5_recon_mismatch"


def test_recon_seam_ok_dormant_and_settling_statuses_do_not_fire():
    """OK / DORMANT / SETTLING are the three benign reconcile outcomes (the settle window
    exists precisely so in-flight fills don't false-halt); none may fire. Kills: inverting
    the status check so a healthy reconcile halts the loop."""
    for status in (OK, DORMANT, SETTLING):
        clock, _ = _clock_box()
        monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: _recon(status))
        state = monitor.evaluate((), {}.get)
        assert state.action == NONE, f"{status} must not fire"
        assert state.triggers == ()


def test_recon_seam_absent_provider_is_dormant():
    """recon_provider=None (the default) keeps the trigger dormant -- the data-gated seam
    pattern. Kills: consulting a None seam (TypeError on the call)."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock)
    assert monitor.evaluate((), {}.get).action == NONE


def test_recon_seam_provider_returning_none_result_is_skipped():
    """A wired provider yielding None (no result this cycle) is a SKIP -- not a fire, not a
    crash. Kills: unconditional r.status attribute access on a None result."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: None)
    assert monitor.evaluate((), {}.get).action == NONE
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: 1 failed â€” `test_recon_seam_diverged_status_fires_l5_recon_mismatch_halt` with `AssertionError: assert 'NONE' == 'HALT'` (the spine stores the seam but nothing consults it yet). The three no-fire tests pass against the S4.4a spine â€” the RED is the fire.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/anomaly.py`: add to the module imports

```python
from polybot.ers.reconcile import DIVERGED
```

  (and make sure `REASON_L5_RECON_MISMATCH` is in the existing `from polybot.ers.safety import ...` line), then insert into `AnomalyMonitor.evaluate`, immediately after the `l5_clock_skew` consult:

```python
        # --- l5_recon_mismatch (S4.4e): per-cycle three-way reconcile cadence ----------------
        # Severity slot 2: after l5_clock_skew, before l5_canary_fail (the pinned order).
        if self._recon_provider is not None:
            r = self._recon_provider()
            if r is not None and r.status == DIVERGED:
                triggers.append(REASON_L5_RECON_MISMATCH)
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `7 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/anomaly.py && git commit -m "feat(anomaly): recon_provider seam -- DIVERGED fires l5_recon_mismatch; OK/DORMANT/SETTLING benign (S4.4e)"'`

---

### Task E4: recon consult fails closed â€” unknown status and raising provider both fire

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (the recon consult block added in E3 + the reconcile import line)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E4: recon consult fail-closed (unknown status + raising seam) -----------------------

def test_recon_seam_unknown_status_string_fails_closed_and_fires():
    """An UNRECOGNIZED ReconResult.status must be treated as a mismatch (design invariant 4:
    unknown status -> DIVERGED). Kills: the E3 `status == DIVERGED` equality surviving
    instead of the not-in-{OK, DORMANT, SETTLING} allowlist."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=lambda: _recon("GARBLED"))
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert "l5_recon_mismatch" in state.triggers


def test_recon_seam_raising_provider_fires_the_trigger_instead_of_propagating():
    """The fail-closed seam rule: a RAISING recon provider IS an anomaly -- evaluate fires
    l5_recon_mismatch and the exception never escapes. Kills: dropping the per-seam
    try/except so a wedged reconcile backend kills the cycle UNhalted."""
    def _boom():
        raise RuntimeError("recon backend wedged")

    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, recon_provider=_boom)
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert "l5_recon_mismatch" in state.triggers
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: 2 failed â€” the unknown-status test with `AssertionError: assert 'NONE' == 'HALT'`; the raising test ERRORS with `RuntimeError: recon backend wedged` propagating out of `evaluate`.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/anomaly.py`: change the reconcile import to

```python
from polybot.ers.reconcile import DORMANT, OK, SETTLING
```

  and replace the E3 recon consult block with the final fail-closed form:

```python
        # --- l5_recon_mismatch (S4.4e): per-cycle three-way reconcile cadence ----------------
        # Severity slot 2: after l5_clock_skew, before l5_canary_fail (the pinned order).
        # Fail-closed BOTH ways: an unknown status is a mismatch; a raising provider fires
        # its own trigger (append + continue -- never mask, never propagate).
        if self._recon_provider is not None:
            try:
                r = self._recon_provider()
                if r is not None and r.status not in (OK, DORMANT, SETTLING):
                    triggers.append(REASON_L5_RECON_MISMATCH)
            except Exception:
                triggers.append(REASON_L5_RECON_MISMATCH)
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `9 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/anomaly.py && git commit -m "feat(anomaly): recon seam fails closed -- unknown status + raising provider both fire (S4.4e)"'`

---

### Task E5: canary scheduler â€” first-evaluate due, `>=` interval boundary, one call per cycle

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (`AnomalyMonitor.__init__` â€” add the scheduler state; `evaluate` â€” insert the canary consult after the recon consult, before the abnormal-book section)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append the spy helper (module-level, below the existing helpers) and the tests:

```python
# --- Task E5: the signing-canary scheduler ------------------------------------------------------

def _counting_canary(result=True):
    """0-arg canary spy: counts invocations, returns `result` (True == healthy signing path)."""
    calls = {"n": 0}

    def canary():
        calls["n"] += 1
        return result
    return canary, calls


def test_canary_first_evaluate_is_due_and_calls_the_canary_exactly_once():
    """_canary_last_run starts None -> the FIRST evaluate is due and calls the canary ONCE
    (never twice within a cycle); a healthy True keeps action NONE. Kills: initializing
    last_run to clock() so the first canary silently never runs."""
    canary, calls = _counting_canary(result=True)
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=canary)
    state = monitor.evaluate((), {}.get)
    assert calls["n"] == 1
    assert state.action == NONE


def test_canary_just_under_the_interval_is_not_redue():
    """Boundary pair, low side: elapsed 299s < signing_canary_interval_seconds (300) -> the
    second evaluate must NOT re-call the canary. Kills: dropping the interval gate and
    re-running the canary every cycle."""
    canary, calls = _counting_canary(result=True)
    clock, box = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=canary)
    monitor.evaluate((), {}.get)      # t=0: due -> 1 call, last_run=0
    box["now"] = 299.0                # just under the 300s interval
    monitor.evaluate((), {}.get)
    assert calls["n"] == 1


def test_canary_at_exactly_the_interval_boundary_is_redue():
    """Boundary pair, at-threshold side: elapsed == interval IS due (the pinned `>=`).
    Kills: a `>` off-by-one that skips the exact-cadence tick."""
    canary, calls = _counting_canary(result=True)
    clock, box = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=canary)
    monitor.evaluate((), {}.get)      # t=0: due -> 1 call, last_run=0
    box["now"] = 300.0                # exactly the interval
    monitor.evaluate((), {}.get)
    assert calls["n"] == 2


def test_canary_absent_seam_is_dormant():
    """canary=None (the default): no scheduling, no call, no fire -- the data-gated seam
    pattern. Kills: unconditionally invoking a None canary (TypeError)."""
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock)
    assert monitor.evaluate((), {}.get).action == NONE
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: 3 failed â€” the first/just-under/at-boundary tests with `AssertionError: assert 0 == 1` (and `0 == 2`): no scheduler exists, the canary is never called. The absent-seam test passes against the spine.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/anomaly.py`: add to `AnomalyMonitor.__init__` (with the other seam storage lines):

```python
        # Canary scheduler state (S4.4e): None -> the FIRST evaluate is due.
        self._canary_last_run = None
```

  then insert into `evaluate`, immediately after the recon consult (healthy path only â€” E6 adds the failure semantics):

```python
        # --- l5_canary_fail (S4.4e): the signing-canary scheduler ----------------------------
        # Severity slot 3: after l5_recon_mismatch, before l5_abnormal_book (the pinned order).
        # Due when never run or >= caps.signing_canary_interval_seconds since the last run;
        # at most ONE call per evaluate.
        if self._canary is not None:
            now_s = self._clock()
            if (self._canary_last_run is None
                    or (now_s - self._canary_last_run)
                    >= self._caps.signing_canary_interval_seconds):
                self._canary_last_run = now_s
                self._canary()
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `13 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/anomaly.py && git commit -m "feat(anomaly): signing-canary scheduler -- first-due + >= interval boundary pair (S4.4e)"'`

---

### Task E6: canary failure â€” falsy/raise fires `l5_canary_fail`, never blind-retried, severity order

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (the canary consult block added in E5)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E6: canary failure semantics (falsy / raise / no blind retry / severity order) -------

def test_canary_falsy_return_fires_l5_canary_fail():
    """A due canary returning falsy is a signing failure -> HALT with exactly the
    l5_canary_fail trigger. Kills: discarding the canary's return value."""
    canary, _calls = _counting_canary(result=False)
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=canary)
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert state.triggers == ("l5_canary_fail",)


def test_canary_raising_fires_l5_canary_fail_and_never_propagates():
    """The fail-closed seam rule for the canary: a RAISING canary fires the trigger and the
    exception never escapes evaluate. Kills: dropping the try/except around the call."""
    def _boom():
        raise RuntimeError("signer wedged mid-canary")

    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=_boom)
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert state.triggers == ("l5_canary_fail",)


def test_canary_failure_is_never_blind_retried_before_the_next_interval():
    """DESIGN Â§3 #6: NEVER blind-retry. After a failing canary at t=0, an immediate evaluate
    at t=1 must NOT re-call it -- last_run was stamped even though it FAILED; re-due only at
    t >= 300. Kills: stamping last_run only on success, which loops a failing canary every
    cycle."""
    canary, calls = _counting_canary(result=False)
    clock, box = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, canary=canary)
    monitor.evaluate((), {}.get)      # t=0: due, FAILS -> trigger fired, 1 call
    box["now"] = 1.0
    monitor.evaluate((), {}.get)      # not re-due: no blind retry of the failed canary
    assert calls["n"] == 1


def test_recon_mismatch_outranks_canary_fail_in_the_triggers_order():
    """evaluate collects ALL firing triggers in the pinned severity order, so a cycle where
    BOTH recon and canary fail reports ("l5_recon_mismatch", "l5_canary_fail") -- triggers[0]
    becomes the set_state reason. Kills: a consult-order swap or a first-trigger
    early-return."""
    canary, _calls = _counting_canary(result=False)
    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock,
                             recon_provider=lambda: _recon(DIVERGED), canary=canary)
    state = monitor.evaluate((), {}.get)
    assert state.action == HALT
    assert state.triggers == ("l5_recon_mismatch", "l5_canary_fail")
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'`
  - Expected: 3 failed/errored â€” falsy test `AssertionError: assert 'NONE' == 'HALT'` (return value discarded); raising test ERRORS with `RuntimeError: signer wedged mid-canary`; order test fails `assert ('l5_recon_mismatch',) == ('l5_recon_mismatch', 'l5_canary_fail')`. The no-blind-retry test already passes (E5 stamps before calling) â€” it pins that property against regression.

- [ ] **Step 3: Minimal implementation** â€” replace the E5 canary block in `evaluate` with the final form (add `REASON_L5_CANARY_FAIL` to the `from polybot.ers.safety import ...` line):

```python
        # --- l5_canary_fail (S4.4e): the signing-canary scheduler ----------------------------
        # Severity slot 3: after l5_recon_mismatch, before l5_abnormal_book (the pinned order).
        # Due when never run or >= caps.signing_canary_interval_seconds since the last run; at
        # most ONE call per evaluate. last_run is stamped BEFORE the call so a failing/raising
        # canary is NEVER blind-retried (DESIGN Â§3 #6); falsy return OR raise -> the trigger.
        if self._canary is not None:
            now_s = self._clock()
            if (self._canary_last_run is None
                    or (now_s - self._canary_last_run)
                    >= self._caps.signing_canary_interval_seconds):
                self._canary_last_run = now_s
                try:
                    healthy = self._canary()
                except Exception:
                    healthy = False
                if not healthy:
                    triggers.append(REASON_L5_CANARY_FAIL)
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `17 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py src/polybot/ers/anomaly.py && git commit -m "feat(anomaly): canary falsy/raise fires l5_canary_fail, never blind-retried; recon outranks canary (S4.4e)"'`

---

### Task E7: dispute_flagger stub â€” stored, never consulted (inert seam pin)

**Files:**
- Modify: `src/polybot/ers/anomaly.py` (only if the S4.4a spine did not store the kwarg â€” see Step 3; `evaluate` is NEVER changed by this task)
- Test: `tests/test_ers_anomaly_recon_canary.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E7: the inert dispute_flagger stub seam ----------------------------------------------

def test_dispute_flagger_seam_is_stored_but_never_consulted_by_evaluate():
    """S4.4 ships the dispute_flagger SEAM only (no dispute-ingestion source exists): the
    monitor stores it and evaluate NEVER calls it. A flagger that raises on ANY call, with a
    held position and a healthy non-stale book (so every wired check path actually runs),
    still yields NONE. Kills the mutation that activates the dead branch: any S4.4 evaluate
    path consulting the stub would raise the AssertionError."""
    def _never(token_id):
        raise AssertionError("dispute_flagger must not be consulted in S4.4")

    clock, _ = _clock_box()
    monitor = AnomalyMonitor(RiskCaps(), clock=clock, dispute_flagger=_never)
    state = monitor.evaluate((_pos("t1"),), {"t1": _book("0.50")}.get)
    assert state.action == NONE
    assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py::test_dispute_flagger_seam_is_stored_but_never_consulted_by_evaluate -q'`
  - Expected: this is the contract PIN for a seam the S4.4a spine already stores, so it normally PASSES on first run â€” its RED is the mutation it kills (a future evaluate consulting the stub raises the `AssertionError` inside `_never`). The only legitimate first-run FAILURE is `TypeError: __init__() got an unexpected keyword argument 'dispute_flagger'`, which means the spine drifted from the pinned contract â€” fix per Step 3.

- [ ] **Step 3: Minimal implementation** â€” none on the green path (test-only pin). If Step 2 hit the `TypeError`, add the kwarg + storage to `AnomalyMonitor.__init__` exactly per the pinned contract signature:

```python
        # S4.4 stub seam (DESIGN Â§3): stored, NEVER consulted by evaluate. A future slice with
        # a real dispute-ingestion source wires it to set OpenPosition.frozen (freeze-not-halt).
        self._dispute_flagger = dispute_flagger
```

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `18 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py && git commit -m "test(anomaly): pin the inert dispute_flagger stub seam (S4.4e)"'` (add `src/polybot/ers/anomaly.py` to the `git add` only if Step 3's storage line was needed)

---

### Task E8: THE E2E â€” DIVERGED mid-run halts, cancels once, sticky after the anomaly clears

**Files:**
- Test: `tests/test_ers_anomaly_recon_canary.py` (append; test-only â€” the DESIGN-S4.4 Â§8.3 acceptance pin over the real assembly)

- [ ] **Step 1: Write the failing test** â€” append:

```python
# --- Task E8: the DESIGN-S4.4 Â§8.3 acceptance e2e ----------------------------------------------

def test_e2e_recon_diverged_mid_run_halts_cancels_once_and_stays_sticky_after_clear(tmp_path):
    """The full assembly (real IntentStore + SafetyController + ERSController + PaperSigner +
    wired AnomalyMonitor): cycle 1 trades while the reconcile is OK; a DIVERGED reconcile on
    cycle 2 halts FIRST (HALTED, reason l5_recon_mismatch), fires cancel_all exactly once,
    keeps the placed record AND the staged protective GTD exit intact, and writes the exact
    op-audit rows; cycle 3 -- the reconcile now OK again -- proves the STICKY invariant: a
    fresh intent is REJECTed under l5_recon_mismatch, the state is still HALTED, and the
    de-risk did NOT re-fire. Kills (integration level): dropping the edge guard (cycle-3
    re-fire), swapping the halt/cancel order (audit rows out of order), letting a raising
    path unwind the halt, and ANY auto-resume (a cycle-3 ACCEPT)."""
    store = _store(tmp_path)
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        results = [_recon(OK), _recon(DIVERGED), _recon(OK)]   # scripted per-cycle reconcile
        clock, _ = _clock_box()
        monitor = AnomalyMonitor(RiskCaps(), clock=clock,
                                 recon_provider=lambda: results.pop(0))
        signer = PaperSigner()
        book_for = {"t1": _book("0.50"), "t2": _book("0.50")}.get
        rc = ERSController(store=store, book_for=book_for, caps=RiskCaps(), signer=signer,
                           controller=ctl, anomaly=monitor, clock=lambda: 0)

        # Cycle 1 (recon OK): healthy -- the proposed intent trades.
        store.propose_trade("i1", **_P)
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert [o["intent_id"] for o in signer.placed] == ["i1"]
        # Stage a protective GTD exit by hand (the S4.2 primitive) so the kill can prove
        # cancel_all keeps it standing.
        signer.place_gtd_bracket(_pos("t1"), exit_price=Decimal("0.30"), expiry=999)

        # Cycle 2 (recon DIVERGED): halt FIRST, then exactly one best-effort cancel_all.
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert signer.cancelled_all == [{"cancelled": "working_entries"}]   # fired ONCE
        assert [o["intent_id"] for o in signer.placed] == ["i1"]            # record intact
        assert [g["token_id"] for g in signer.gtd_exits] == ["t1"]          # GTD exit SURVIVES
        rows = [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()]
        assert rows == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l5_recon_mismatch", "HALTED"),          # set_state audits FIRST
            ("cancel_all", "l5_recon_mismatch", "l5_recon_mismatch"),  # detail = ",".join(triggers)
        ]

        # Cycle 3 (recon OK again): STICKY -- no auto-resume; the fresh intent is rejected
        # under the specific l5 reason and the de-risk is NOT re-fired.
        store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="m2", event_id="e2"))
        rc.run_cycle()
        assert store.get("i2").status == "REJECTED"
        assert store.get("i2").decision_reason == "l5_recon_mismatch"
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
    finally:
        store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py::test_e2e_recon_diverged_mid_run_halts_cancels_once_and_stays_sticky_after_clear -q'`
  - Expected: with E1â€“E7 and S4.4aâ€“d all landed this acceptance pin PASSES on first run â€” that is its purpose; observe the GREEN. Any FAILURE names a broken predecessor and must be fixed in the task that owns it, not here: `assert 'RUNNING' == 'HALTED'` on cycle 2 means E3/E4's recon consult is missing; a wrong `rows` list means the S4.4a controller halt/cancel/audit order regressed; `len(signer.cancelled_all) == 1` failing with 2 means the edge guard regressed.

- [ ] **Step 3: Minimal implementation** â€” no production code. This task is test-only: the implementation IS E1â€“E7 plus the S4.4a controller wiring, and this test locks their composition (the design's Â§8.3 acceptance criterion + safety invariants 1â€“3).

- [ ] **Step 4: Run the test - PASS; run the full suite - all green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_anomaly_recon_canary.py -q'` â†’ `19 passed`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'` â†’ 0 failed (556 pre-S4.4 baseline + all S4.4aâ€“d tests + the 19 in this file)

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_anomaly_recon_canary.py && git commit -m "test(anomaly): S4.4 Â§8.3 e2e -- DIVERGED mid-run halts, cancels once, GTD survives, sticky after clear (S4.4e)"'`