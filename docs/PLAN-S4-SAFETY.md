# S4 / POL-6 — Safety Envelope (Kill Path: S4.1–S4.3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the autonomous kill path in shadow and prove it against a deliberately-wedged process — an operational-state machine that can `KILL/PAUSE/HALT/FLATTEN` the loop ahead of the L7 breaker (S4.1), the de-risk primitives + GTD brackets + new `RiskCaps` fields + a refuse-to-start self-test (S4.2), and a **separate-process out-of-band supervisor** with a file heartbeat and its own signer that hard-kills a wedged ERS and de-risks (S4.3, the acceptance gate).

**Architecture:** A `SafetyController` op-state machine is consulted at the TOP of `process_pending` via a new `controller=` kwarg (precedence `KILL > op-FLATTENING > L7-FLATTEN > FREEZE > NONE`); a new `ERSController` runloop owns it + drives the cadence (beat/evaluate/process). Durable state (op-state, fills, counters) persists in new append-only `IntentStore` tables. The out-of-band supervisor is a SEPARATE OS PROCESS holding a DISTINCT signer, watching a fate-isolated FILE heartbeat. `evaluate_intent`/the validator/`propose_trade`'s INSERT-only chokepoint are UNCHANGED; `RiskCaps`/`IntentStore`/`process_pending`/`PaperSigner` are extended additively (`controller=None`/`gtd_for=None` == today). Runs SHADOW-ONLY on the `PaperSigner` (live cancelAll/GTD/feeds deferred to POL-4). Spec: [`DESIGN-S4-SAFETY.md`](DESIGN-S4-SAFETY.md).

**Tech Stack:** Python 3.11+ (src-layout, `pythonpath=["src"]`), `Decimal` money math, SQLite (WAL) for durable state, `multiprocessing` (fork) + a file heartbeat for the wedged-process gate, `pytest` via `./.venv/bin/pytest`, no `pytest-asyncio`. Clocks injected for deterministic TDD; fail-closed throughout.

---

## Sub-slice ordering & dependencies

Build in order — each sub-slice is its own strict-TDD slice + a pinned-`model:opus` `superpowers:code-reviewer` pass. **S4.3 hard-depends on S4.1 + S4.2.**

- **S4.1** — `SafetyController` op-state + the `process_pending` `controller=` gate + `REASON_*` + `ERSController` scaffold + the IntentStore op/kill audit table + the facade-sweep extension. *(foundation)*
- **S4.2** — `Signer` Protocol + `PaperSigner` de-risk methods (`cancel_all`/`place_gtd_bracket`/`run_canary`) + `ers/gtd.py` + new `RiskCaps` fields + `daily_pending_ceiling` wiring + `ers/startup_selftest.py`. *(de-risk primitives)*
- **S4.3** — `ers/heartbeat.py` + `ers/supervisor.py` (`OutOfBandSupervisor` + `WedgedSigner`) + **the subprocess-backed wedged-process acceptance gate**. *(THE headline; needs S4.1 `ERSController` + S4.2 `cancel_all`/GTD)*

## TDD discipline

Each unit is built RED→GREEN→commit, **observing each true RED** (watch it fail for the right reason). Pass-by-construction cycles (where behavior is satisfied once a prior cycle's code lands) are flagged in-step — confirm the clean PASS and still commit; never weaken a test or fake a RED. The S4.3 acceptance gate's pure-unit tests use an injected clock (deterministic); the subprocess integration test uses a SHORT real `dead_man_switch_timeout` (override the cap) so it's bounded — and SIGKILL (a wedged interpreter swallows SIGTERM).

## Reconciled deviations from the design (accepted, baked into the tasks)

1. **No distinct `KILL` op-state** — the 4-state vocab is `RUNNING/PAUSED/HALTED/FLATTENING`; a "kill" is `set_state(HALTED, reason=REASON_L8_KILL)`. **REFINEMENT (do this):** `SafetyController` stores the current `(op_state, reason)` and `verdict()` returns the **specific stored reason** (`l8_kill`/`l8_paused`/`unclean_restart`/`op_flatten`) as the `Decision.block_reason` — NOT a generic state name. This matches the design's distinct §6 reason codes + gives the audit trail the real cause. (The drafter flagged this as a one-line change; it IS in scope.)
2. **`Signer` Protocol lives in `ers/signer.py`** (a net-new module), not `safety.py` (avoids coupling with S4.1's op-state machine).
3. **`derive_bracket(decision, position, *, caps, expiry, standing_exit_total=Decimal(0))`** — extended with `expiry` (a GTD order needs one) + `standing_exit_total` (required to enforce `aggregate standing-exit ≤ total_open_risk` — a per-call bracket can't know the aggregate otherwise).
4. **`process_pending(..., gtd_for=None)`** — GTD staging is wired via a new opt-in callable kwarg (default `None` == today), matching the `breaker=`/`pipeline=`/`controller=` seam pattern, keeping `expiry` injectable. The `ERSController.run_cycle` binds both `controller=` and `gtd_for=`.
5. **Placeholder caps defaults** (`signing_canary_interval_seconds=300`, `dead_man_switch_timeout_seconds=30`, `reconcile_tolerance=Decimal("0.50")`) — consistent with the design; `content_hash` auto-covers them. The S4.3 acceptance test overrides `dead_man_switch_timeout_seconds` to a short value for boundedness.

## Opus review checkpoints (team standard)

Dispatch a `superpowers:code-reviewer` with **`model: opus`** pinned after each sub-slice (at minimum after S4.1 and S4.3); re-review after any safety-critical fix. Probe especially: the precedence (op-block can't be overwritten by a weaker L7 verdict), the `cancel_all`-vs-GTD semantics (DESIGN §9 — `cancel_all` must KEEP the protective GTD exit brackets), and the subprocess gate's fidelity (real kill landed via `exitcode`; de-risk on the supervisor's OWN signer; brackets survive).

## Open risks for the Opus review to probe (DESIGN §9)

- **`cancel_all` vs the GTD exits:** must cancel WORKING ENTRY orders but KEEP the protective GTD EXIT brackets — a `cancel_all` that kills the exits would *increase* risk on a wedge. Encoded in S4.2 + asserted (the `gtd_exits` survive a `cancel_all`); flagged as a hard requirement for the live POL-4 signer.
- **Subprocess test fidelity/flakiness:** heartbeat write/read races, WSL `os.kill` semantics, deterministic timing (injected clock for `.decide`; short real timeout for the integration test).
- **op-FLATTENING vs L7-FLATTEN precedence/naming:** two distinct FLATTEN concepts; the op-block must dominate and can't be conflated.
- **Persistence correctness:** the new op/fill tables append-only + crash-consistent (shared stamper ordering); a half-written row can't corrupt the op-state read.
- **`daily_pending_ceiling` wiring** must not double-count vs the L7 breaker or the validator's existing caps.
- **Startup self-test scope:** the codeable-now checks (caps `content_hash`, pUSD address, struct hashes) genuinely gate startup; the deferred checks (allowances, real sign-canary) are clearly seams, not silently skipped.

---

## Sub-slice S4.1: SafetyController op-state + loop gate

Builds the operational kill-path control surface that sits *above* the S3 L7 breaker: an op-state machine (`ers/safety.py`), an append-only op/kill audit table in `IntentStore`, the `process_pending(controller=...)` gate (precedence `KILL > op_flatten > l7_flatten > l7_freeze > none`; `controller=None` == today), the long-lived `ERSController` runloop scaffold (starts HALTED, beats the heartbeat then drives `process_pending`), and the `test_ers_facade.py` structural-sweep extension proving the Hermes facade exposes no kill/pause/cancel/set_state surface.

**Runner (all tasks):** every `pytest` invocation runs natively under WSL —
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest <path>::<test> -v'`.
The repo `pyproject.toml` sets `pythonpath=["src"]`, so imports are `from polybot...`. Money is `Decimal` from strings; clocks are injected; everything fails closed.

**Observe each RED.** For every cycle below, run the test and *watch it fail for the stated reason* before writing implementation. None of these cycles is pass-by-construction (each new test targets a symbol or behavior that does not yet exist — the first run is an `ImportError`/`AttributeError` or a behavioral assertion failure, exactly as called out in the "Expected" line).

---

### Task 1: IntentStore op/kill append-only audit table

The `SafetyController` (Task 2) records every op-state transition + kill/pause/flatten event to a durable, append-only table mirroring `intent_audit` (`AUTOINCREMENT` + shared `MonotonicStamper.stamp()`). Build the store surface first so the controller has a real durable-state handle.

**Files:**
- Modify: `src/polybot/ers/intent_store.py`
- Test: `tests/test_ers_intent_store.py` (append new tests; do not disturb existing ones)

- [ ] **Step 1: Write the failing test — record_op_event appends ordered rows**

Append to `tests/test_ers_intent_store.py`. If the file lacks the import header, the block below is self-contained (it imports what it needs at top of the test).

```python
# --- S4.1: op/kill append-only audit table (POL-6) -------------------------------------------
from decimal import Decimal  # noqa: F401 (harmless if already imported at top of file)
from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _op_store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_op_event_appends_ordered_rows(tmp_path):
    with _op_store(str(tmp_path / "i.db")) as store:
        store.record_op_event(kind="state_change", reason="unclean_restart", detail="boot")
        store.record_op_event(kind="kill", reason="l8_kill")  # detail defaults to ""
        store.record_op_event(kind="flatten", reason="op_flatten", detail="2 positions")

        rows = store.op_audit_log()
        assert [r["kind"] for r in rows] == ["state_change", "kill", "flatten"]
        assert [r["reason"] for r in rows] == ["unclean_restart", "l8_kill", "op_flatten"]
        assert rows[0]["detail"] == "boot" and rows[1]["detail"] == ""
        # Each row carries the shared monotonic stamp, strictly increasing in id-order.
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 3
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_record_op_event_appends_ordered_rows -v'`
  - Expected: **FAIL** with `AttributeError: 'IntentStore' object has no attribute 'record_op_event'`.

- [ ] **Step 3: Write minimal implementation**

In `src/polybot/ers/intent_store.py`, add the table-creation SQL inside `__init__` right after the `intent_audit` `CREATE TABLE` block (before `self._conn.commit()`):

```python
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS op_audit (
                op_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                at     INTEGER NOT NULL,
                kind   TEXT    NOT NULL,
                reason TEXT    NOT NULL,
                detail TEXT    NOT NULL
            )
            """
        )
```

Then add these two methods to the `IntentStore` class (place them right after `audit_log`):

```python
    def record_op_event(self, *, kind, reason, detail=""):
        """Append an IMMUTABLE op/kill/heartbeat audit row (S4.1). ``kind`` in
        {state_change, kill, pause, flatten, heartbeat}; ``reason`` is a REASON_* code or a
        free-form string. Append-only + the shared monotonic stamp, mirroring intent_audit, so
        the restart-reconcile (S4.5) can replay the op timeline crash-consistently."""
        self._conn.execute(
            "INSERT INTO op_audit (at, kind, reason, detail) VALUES (?, ?, ?, ?)",
            (self._stamper.stamp(), kind, reason, detail),
        )
        self._conn.commit()

    def op_audit_log(self):
        rows = self._conn.execute(
            "SELECT at, kind, reason, detail FROM op_audit ORDER BY op_id"
        ).fetchall()
        return [{"at": r[0], "kind": r[1], "reason": r[2], "detail": r[3]} for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_record_op_event_appends_ordered_rows -v'`
  - Expected: **PASS**.

- [ ] **Step 5: Write the failing test — op_audit_log survives a close/reopen (durability)**

Append to `tests/test_ers_intent_store.py`:

```python
def test_op_audit_log_persists_across_restart(tmp_path):
    db = str(tmp_path / "i.db")
    with _op_store(db) as store:
        store.record_op_event(kind="pause", reason="l8_paused", detail="operator")
    # Reopen the SAME path with a FRESH stamper -- the row must survive (append-only + committed).
    with _op_store(db) as reopened:
        rows = reopened.op_audit_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "pause" and rows[0]["reason"] == "l8_paused"
        # A new event after restart appends AFTER the persisted one (id ordering, not stamp clock).
        reopened.record_op_event(kind="state_change", reason="unclean_restart")
        rows = reopened.op_audit_log()
        assert [r["kind"] for r in rows] == ["pause", "state_change"]
```

- [ ] **Step 6: Run test to verify it passes (regression — table already built)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_op_audit_log_persists_across_restart -v'`
  - Expected: **PASS** (the `CREATE TABLE IF NOT EXISTS` + per-write commit from Step 3 already make this hold). NOTE: this is a deliberate pass-by-construction durability assertion — it pins crash-consistency (a §9 open-risk) rather than driving new code. Confirm it passes; do NOT add code to satisfy it.

- [ ] **Step 7: Commit**
  - `wsl -d Ubuntu -- bash -lc "cd ~/projects/polymarket-bot && git add src/polybot/ers/intent_store.py tests/test_ers_intent_store.py && git commit -m 'feat(ers): add append-only op/kill audit table to IntentStore (S4/POL-6)'"`

---

### Task 2: ers/safety.py — op-state vocab, REASON_*, OpVerdict, SafetyController

**Files:**
- Create: `src/polybot/ers/safety.py`
- Test: `tests/test_ers_safety.py` (new)

- [ ] **Step 1: Write the failing test — constants, OpVerdict shape, and default HALTED state**

Create `tests/test_ers_safety.py`:

```python
"""SafetyController op-state machine + the loop-gate verdict (S4.1 / POL-6).

The controller is the operational kill surface that sits ABOVE the L7 breaker: it holds the
op-state (RUNNING/PAUSED/HALTED/FLATTENING -- where FLATTENING is operator/L5/L6-driven, DISTINCT
from breaker.py's drawdown FLATTEN), the swappable active-caps reference, and a durable-state
handle (the IntentStore). verdict(portfolio, signer) is consulted at the TOP of process_pending;
it fails closed and audits every operator transition. Clocks are injected; money is Decimal.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ers import safety
from polybot.ers.safety import OpVerdict, SafetyController


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _ctl(tmp_path, store, *, caps=None, clock=lambda: 0):
    return SafetyController(caps=caps or RiskCaps(), store=store, clock=clock)


def test_op_state_vocab_and_reason_constants():
    # The op-state vocabulary (FLATTENING is distinct from breaker.py FLATTEN).
    assert safety.RUNNING == "RUNNING"
    assert safety.PAUSED == "PAUSED"
    assert safety.HALTED == "HALTED"
    assert safety.FLATTENING == "FLATTENING"
    # The S4.1 reason codes (free-form Decision.reason strings; NO validator change).
    assert safety.REASON_L8_KILL == "l8_kill"
    assert safety.REASON_L8_PAUSED == "l8_paused"
    assert safety.REASON_OP_FLATTEN == "op_flatten"
    assert safety.REASON_UNCLEAN_RESTART == "unclean_restart"


def test_controller_starts_halted_and_blocks_with_unclean_restart(tmp_path):
    # A fresh controller starts HALTED (crash/restart default; RUNNING only after a clean
    # reconcile in S4.5) -> verdict blocks the loop with the unclean_restart reason.
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        assert ctl.state() == safety.HALTED
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert isinstance(v, OpVerdict)
        assert v.action == safety.HALTED
        assert v.block_reason == safety.REASON_UNCLEAN_RESTART
        assert v.derisk is None
        assert "halted" in v.triggers


def test_active_caps_returns_the_held_reference(tmp_path):
    with _store(tmp_path) as store:
        caps = RiskCaps()
        ctl = _ctl(tmp_path, store, caps=caps)
        assert ctl.active_caps() is caps
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_safety.py -v'`
  - Expected: **FAIL** with `ModuleNotFoundError: No module named 'polybot.ers.safety'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/polybot/ers/safety.py`:

```python
"""Operational safety controller + op-state machine (S4.1 / POL-6).

The SafetyController is the operational kill surface consulted at the TOP of process_pending
(new ``controller=`` seam, the same additive pattern as ``breaker=`` / ``pipeline=``). It holds
the op-state, the swappable active-caps reference, and a durable-state handle (the IntentStore,
for the append-only op/kill audit). Its ``verdict`` fails CLOSED and dominates the L7 breaker:
the loop precedence is KILL > op_flatten > l7_flatten > l7_freeze > none.

FLATTENING here is the operator/L5/L6-driven op-state -- DISTINCT from breaker.py's drawdown
FLATTEN action. Crash/restart starts HALTED; RUNNING is only entered after a clean reconcile
(S4.5). Clocks are injected for deterministic TDD; money is Decimal.
"""

from dataclasses import dataclass

# --- op-state vocabulary (NET-NEW; FLATTENING != breaker.py FLATTEN) -------------------------
RUNNING = "RUNNING"
PAUSED = "PAUSED"
HALTED = "HALTED"
FLATTENING = "FLATTENING"

# --- S4.1 reason codes (free-form Decision.reason strings; NO validator/schema change) -------
REASON_L8_KILL = "l8_kill"
REASON_L8_PAUSED = "l8_paused"
REASON_OP_FLATTEN = "op_flatten"
REASON_UNCLEAN_RESTART = "unclean_restart"


@dataclass(frozen=True)
class OpVerdict:
    """The op-state verdict read at the top of process_pending (mirrors breaker.BreakerState).

    ``action`` is the current op-state; ``block_reason`` is the Decision.reason to reject every
    pending intent under (None => the loop proceeds to the L7 breaker); ``derisk`` is the de-risk
    primitive the loop must fire on the signer (``op_flatten`` => flatten + cancel_all), None
    otherwise; ``triggers`` is the audit/debug provenance tuple."""
    action: str
    block_reason: str | None
    derisk: str | None
    triggers: tuple


class SafetyController:
    def __init__(self, *, caps, store, clock):
        # Starts HALTED: a fresh/restarted controller never trades until an operator (or, in
        # S4.5, a clean restart-reconcile) transitions it to RUNNING. Fail closed.
        self._caps = caps
        self._store = store
        self._clock = clock
        self._state = HALTED

    def state(self):
        return self._state

    def active_caps(self):
        # The swappable RiskCaps reference (the S4.7 ramp-DOWN ratchet replaces it atomically).
        return self._caps

    def set_state(self, op_state, *, reason):
        """Operator/L8-driven transition. Appends an immutable op-audit row, then swaps the
        in-memory op-state. Audit-before-mutate so a crash mid-call leaves an explanation."""
        self._store.record_op_event(kind="state_change", reason=reason, detail=op_state)
        self._state = op_state

    def verdict(self, portfolio, signer):
        """Consulted FIRST in process_pending. Fail-closed mapping of op-state -> OpVerdict:

          HALTED     -> block (unclean_restart): the loop never trades from a halted controller.
          PAUSED     -> block (l8_paused).
          FLATTENING -> block (op_flatten) AND de-risk: signal the exit + cancel working entries,
                        exactly as the L7-FLATTEN short-circuit does, but ahead of it (dominates).
          RUNNING    -> no block (None): the loop falls through to the L7 breaker unchanged.

        A KILL is modelled as the HALTED op-state reached via set_state(HALTED, reason=l8_kill);
        the dedicated REASON_L8_KILL path is exposed via set_state's audit + (RUNNING->)HALTED so
        an explicit kill records l8_kill while still blocking. Here verdict reports the CURRENT
        op-state's block; the kill reason is carried in the audit row written by set_state."""
        if self._state == HALTED:
            return OpVerdict(HALTED, REASON_UNCLEAN_RESTART, None, ("halted",))
        if self._state == PAUSED:
            return OpVerdict(PAUSED, REASON_L8_PAUSED, None, ("paused",))
        if self._state == FLATTENING:
            # De-risk on the ERS's signer ahead of the breaker (op-FLATTEN dominates L7-FLATTEN):
            # signal the exit, then cancel WORKING ENTRY orders (the GTD exit brackets stay).
            signer.flatten(portfolio.positions)
            signer.cancel_all()
            self._store.record_op_event(
                kind="flatten", reason=REASON_OP_FLATTEN,
                detail=f"{len(portfolio.positions)} positions")
            return OpVerdict(FLATTENING, REASON_OP_FLATTEN, REASON_OP_FLATTEN, ("op_flatten",))
        # RUNNING -> no op-block; the loop proceeds to the L7 breaker unchanged.
        return OpVerdict(RUNNING, None, None, ())
```

NOTE: `signer.cancel_all()` is added to `PaperSigner` in sub-slice S4.2. For this sub-slice's tests, the FLATTENING/precedence tests below inject a local `_RecordingSigner` that implements `flatten` + `cancel_all`, so S4.1 does not depend on the S4.2 commit landing first.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_safety.py -v'`
  - Expected: **PASS** (3 tests).

- [ ] **Step 5: Write the failing test — set_state transitions, audits, and unblocks RUNNING; FLATTENING de-risks**

Append to `tests/test_ers_safety.py`:

```python
class _RecordingSigner:
    """A minimal Signer double for S4.1 (S4.2 adds these to PaperSigner). Records flatten +
    cancel_all so we can assert op-FLATTEN de-risks on the ERS's own signer."""

    def __init__(self):
        self.flattened = []
        self.cancelled_all = []

    def flatten(self, positions):
        self.flattened.append(tuple(p.token_id for p in positions))

    def cancel_all(self):
        self.cancelled_all.append("cancel_all")


def _pos(token):
    return OpenPosition("m", "e", "s", "c", Decimal("12"), False,
                        token_id=token, entry_price=Decimal("0.50"))


def test_set_state_running_unblocks_and_audits(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.RUNNING, reason="clean_reconcile")
        assert ctl.state() == safety.RUNNING
        v = ctl.verdict(Portfolio(nav=Decimal("300")), _RecordingSigner())
        assert v.action == safety.RUNNING
        assert v.block_reason is None and v.derisk is None
        # The transition was audited (audit-before-mutate).
        rows = store.op_audit_log()
        assert rows[-1]["kind"] == "state_change"
        assert rows[-1]["reason"] == "clean_reconcile" and rows[-1]["detail"] == safety.RUNNING


def test_pause_blocks_with_l8_paused_and_does_not_derisk(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.PAUSED, reason=safety.REASON_L8_PAUSED)
        signer = _RecordingSigner()
        v = ctl.verdict(Portfolio(nav=Decimal("300"), positions=(_pos("A"),)), signer)
        assert v.action == safety.PAUSED and v.block_reason == safety.REASON_L8_PAUSED
        assert v.derisk is None
        # PAUSE blocks NEW trades but never flattens existing ones.
        assert signer.flattened == [] and signer.cancelled_all == []


def test_flattening_blocks_op_flatten_and_derisks_on_the_signer(tmp_path):
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.FLATTENING, reason=safety.REASON_OP_FLATTEN)
        signer = _RecordingSigner()
        portfolio = Portfolio(nav=Decimal("300"), positions=(_pos("A"), _pos("B")))
        v = ctl.verdict(portfolio, signer)
        assert v.action == safety.FLATTENING
        assert v.block_reason == safety.REASON_OP_FLATTEN
        assert v.derisk == safety.REASON_OP_FLATTEN
        # Op-FLATTEN signals the exit AND cancels working entry orders on the ERS's own signer.
        assert signer.flattened == [("A", "B")]
        assert signer.cancelled_all == ["cancel_all"]
        # And it audited a flatten event.
        kinds = [r["kind"] for r in store.op_audit_log()]
        assert "flatten" in kinds


def test_kill_via_set_state_halts_and_records_l8_kill(tmp_path):
    # An explicit operator KILL = set_state(HALTED, reason=l8_kill): the audit row carries the
    # kill reason while the op-state blocks the loop. (verdict reports the HALTED block.)
    with _store(tmp_path) as store:
        ctl = _ctl(tmp_path, store)
        ctl.set_state(safety.RUNNING, reason="clean_reconcile")
        ctl.set_state(safety.HALTED, reason=safety.REASON_L8_KILL)
        assert ctl.state() == safety.HALTED
        v = ctl.verdict(Portfolio(nav=Decimal("300")), _RecordingSigner())
        assert v.block_reason == safety.REASON_UNCLEAN_RESTART  # HALTED block reason
        # The kill reason is in the audit trail.
        assert any(r["reason"] == safety.REASON_L8_KILL for r in store.op_audit_log())
```

- [ ] **Step 6: Run test to verify it passes (FLATTENING de-risk drives new behavior)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_safety.py -v'`
  - Expected: **PASS** (7 tests). The `set_state` + FLATTENING de-risk paths were implemented in Step 3; these tests confirm the audit-before-mutate ordering and the flatten+cancel_all de-risk land. If any FAIL, re-read Step 3's `verdict`/`set_state` before patching.

- [ ] **Step 7: Commit**
  - `wsl -d Ubuntu -- bash -lc "cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_safety.py && git commit -m 'feat(ers): SafetyController op-state machine + fail-closed verdict (S4/POL-6)'"`

---

### Task 3: process_pending controller= gate (precedence ahead of the L7 breaker)

**Files:**
- Modify: `src/polybot/ers/service.py`
- Test: `tests/test_ers_service.py` (append new tests)

- [ ] **Step 1: Write the failing test — controller=None is verbatim today (regression guard)**

Append to `tests/test_ers_service.py`:

```python
# --- S4.1: SafetyController loop gate (controller= kwarg) -------------------------------------
from polybot.ers import safety as _safety
from polybot.ers.safety import SafetyController


def _running_controller(tmp_path, **kw):
    """A controller already transitioned to RUNNING (so it does not block the loop)."""
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0, **kw)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl, store


def test_controller_none_is_exactly_todays_accept_path(tmp_path):
    # The S4.1 seam is purely additive: controller omitted (None) => identical to slice-3/S6.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, controller=None)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")


def test_running_controller_lets_the_accept_path_through(tmp_path):
    # A RUNNING controller imposes no op-block -> the loop falls through to the normal ACCEPT.
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i1").status == "ACCEPTED"
            assert [o["token_id"] for o in signer.placed] == ["t1"]
    finally:
        ctl_store.close()
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py::test_controller_none_is_exactly_todays_accept_path tests/test_ers_service.py::test_running_controller_lets_the_accept_path_through -v'`
  - Expected: **FAIL** with `TypeError: process_pending() got an unexpected keyword argument 'controller'`.

- [ ] **Step 3: Write minimal implementation**

In `src/polybot/ers/service.py`, change the `process_pending` signature to add `controller=None`:

```python
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None):
```

Then replace the existing `block_reason` setup block (the lines `block_reason = None` through the `elif state.action == FREEZE_ADDS:` branch) with the version below, which consults the controller FIRST so the op-state dominates the L7 breaker (`KILL > op_flatten > l7_flatten > l7_freeze > none`):

```python
    # 1. Op-state gate (S4.1): consulted FIRST so a KILL/PAUSE/op-FLATTEN op-state dominates the
    #    L7 breaker. controller=None => exactly today's behavior (the existing tests stay green).
    #    Precedence: KILL/PAUSE/op_flatten (controller) > l7_flatten > l7_freeze > none.
    block_reason = None
    if controller is not None:
        op = controller.verdict(portfolio, signer)
        if op.block_reason is not None:
            # The controller already fired any de-risk (op-FLATTEN -> signer.flatten/cancel_all)
            # inside verdict(); here we just dominate the loop with its block_reason.
            block_reason = op.block_reason

    # 2. L7 drawdown breaker (EXISTING, unchanged) -- only consulted if the op-state did NOT
    #    already block, so op_flatten can never be overwritten by a weaker l7_freeze/none.
    if block_reason is None and breaker is not None:
        state = breaker.evaluate(portfolio.positions, book_for)
        if state.action == FLATTEN:
            signer.flatten(portfolio.positions)
            block_reason = "l7_flatten"
        elif state.action == FREEZE_ADDS:
            block_reason = "l7_freeze"
```

The downstream per-intent loop (`for intent in store.pending(): ... if block_reason is not None: decision = Decision("REJECT", None, None, block_reason)`) is unchanged — it already rejects every pending intent with the dominant reason.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py::test_controller_none_is_exactly_todays_accept_path tests/test_ers_service.py::test_running_controller_lets_the_accept_path_through -v'`
  - Expected: **PASS** (2 tests).

- [ ] **Step 5: Write the failing test — KILL/PAUSE short-circuits ahead of the L7 breaker; op_flatten dominates l7_freeze; KILL dominates everything**

Append to `tests/test_ers_service.py`:

```python
def _halted_controller(tmp_path):
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # starts HALTED
    return ctl, store


def test_halted_controller_blocks_ahead_of_an_otherwise_clean_loop(tmp_path):
    # A HALTED controller blocks EVERY pending intent with unclean_restart, before any sizing.
    ctl, ctl_store = _halted_controller(tmp_path)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i1").status == "REJECTED"
            assert store.get("i1").decision_reason == "unclean_restart"
            assert signer.placed == []
    finally:
        ctl_store.close()


def test_op_flatten_dominates_an_l7_freeze(tmp_path):
    # The controller is FLATTENING; the L7 breaker (if it ran) would only FREEZE_ADDS. Op-FLATTEN
    # must dominate: the reason is op_flatten (NOT l7_freeze), and the breaker is never consulted.
    ctl, ctl_store = _halted_controller(tmp_path)
    ctl.set_state(_safety.FLATTENING, reason=_safety.REASON_OP_FLATTEN)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            caps = RiskCaps()
            # A position marked into the L7 FREEZE band (drawdown ~$19.20) -- the breaker WOULD
            # set l7_freeze, but the op-state blocks first.
            portfolio = Portfolio(nav=Decimal("300"), positions=(_open("P", "0.50", "24"),))
            books = {"t1": _book("0.50"), "P": _book("0.12", bid="0.08")}
            signer = PaperSigner()
            process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps,
                            signer=signer, controller=ctl,
                            breaker=DrawdownBreaker(caps, clock=lambda: 0))
            assert store.get("i1").status == "REJECTED"
            assert store.get("i1").decision_reason == "op_flatten"  # NOT l7_freeze
            assert signer.placed == []
            # Op-FLATTEN de-risked via the controller (flatten signalled on the ERS's signer).
            assert signer.flattened  # the op-flatten exit was signalled through the seam
    finally:
        ctl_store.close()


def test_explicit_kill_dominates_an_l7_flatten(tmp_path):
    # The controller is HALTED via an explicit KILL; even a position that WOULD trip the L7
    # FLATTEN must be blocked under the op reason (the op-state is read first; the breaker is
    # never consulted). Pin that the kill reason dominates and no l7_flatten leaks through.
    ctl, ctl_store = _halted_controller(tmp_path)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    ctl.set_state(_safety.HALTED, reason=_safety.REASON_L8_KILL)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            caps = RiskCaps()
            positions = (_open("P1", "0.50", "18"), _open("P2", "0.50", "18"))
            portfolio = Portfolio(nav=Decimal("300"), positions=positions)
            books = {"t1": _book("0.50"),
                     "P1": _book("0.06", bid="0.04"), "P2": _book("0.06", bid="0.04")}
            signer = PaperSigner()
            process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps,
                            signer=signer, controller=ctl,
                            breaker=DrawdownBreaker(caps, clock=lambda: 0))
            # HALTED (via KILL) blocks; the reason is the op-block, NOT l7_flatten.
            assert store.get("i1").decision_reason == "unclean_restart"
            assert store.get("i1").decision_reason != "l7_flatten"
            assert signer.placed == []
            # HALTED does NOT itself de-risk (only FLATTENING does), so the breaker's flatten
            # never ran -- nothing was signalled to exit.
            assert signer.flattened == []
            # The kill is in the op-audit trail.
            assert any(r["reason"] == _safety.REASON_L8_KILL for r in ctl_store.op_audit_log())
    finally:
        ctl_store.close()
```

- [ ] **Step 6: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py::test_halted_controller_blocks_ahead_of_an_otherwise_clean_loop tests/test_ers_service.py::test_op_flatten_dominates_an_l7_freeze tests/test_ers_service.py::test_explicit_kill_dominates_an_l7_flatten -v'`
  - Expected: **PASS** (3 tests). These exercise the precedence ordering written in Step 3 (op-block consulted first; breaker only if `block_reason is None`).

- [ ] **Step 7: Run the FULL existing service suite to prove the 448-test regression guard holds**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py -q'`
  - Expected: **PASS** (all pre-existing service tests + the new S4.1 ones). `controller=None` must leave every prior test green.

- [ ] **Step 8: Commit**
  - `wsl -d Ubuntu -- bash -lc "cd ~/projects/polymarket-bot && git add src/polybot/ers/service.py tests/test_ers_service.py && git commit -m 'feat(ers): wire SafetyController controller= gate into process_pending, op-state dominates L7 breaker (S4/POL-6)'"`

---

### Task 4: ers/controller.py — ERSController runloop scaffold (starts HALTED; beats heartbeat then process_pending)

**Files:**
- Create: `src/polybot/ers/controller.py`
- Test: `tests/test_ers_controller.py` (new)

- [ ] **Step 1: Write the failing test — run_cycle beats the heartbeat then drives process_pending with the controller**

Create `tests/test_ers_controller.py`:

```python
"""ERSController runloop scaffold (S4.1 / POL-6).

The long-lived cadence driver that OWNS the SafetyController and wraps process_pending. Each
run_cycle: beat the heartbeat (if wired) THEN process_pending(controller=self._controller,
breaker=..., pipeline=...). Starts effectively HALTED -- the held SafetyController is HALTED on
construction, so a cycle before any clean transition blocks the loop. Later sub-slices extend the
cadence (L7 evaluate, signing canary, reconcile); this is the scaffold + the beat->process order.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner, process_pending  # noqa: F401
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


class _SpyHeartbeat:
    def __init__(self):
        self.beats = 0

    def beat(self):
        self.beats += 1


def test_run_cycle_starts_halted_and_blocks(tmp_path):
    # The held SafetyController starts HALTED, so a run_cycle before any clean transition rejects
    # every pending intent with unclean_restart -- the controller-driven loop does NOT trade.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        final = rc.run_cycle()
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "unclean_restart"
        assert signer.placed == []
        assert isinstance(final, Portfolio)
    finally:
        store.close()


def test_run_cycle_beats_heartbeat_then_processes(tmp_path):
    # With a RUNNING controller and a heartbeat wired, run_cycle beats THEN trades.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        hb = _SpyHeartbeat()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, heartbeat=hb, clock=lambda: 0)
        rc.run_cycle()
        assert hb.beats == 1                       # the heartbeat was beaten this cycle
        assert store.get("i1").status == "ACCEPTED"  # RUNNING -> the loop traded
        assert [o["token_id"] for o in signer.placed] == ["t1"]


    finally:
        store.close()


def test_run_cycle_without_heartbeat_still_runs(tmp_path):
    # heartbeat=None (default) must not break the cycle -- the beat is guarded.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)  # no heartbeat
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
    finally:
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_controller.py -v'`
  - Expected: **FAIL** with `ModuleNotFoundError: No module named 'polybot.ers.controller'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/polybot/ers/controller.py`:

```python
"""ERSController -- the long-lived runloop / cadence driver (S4.1 scaffold / POL-6).

NONE exists today: process_pending is per-call pure, and S3 had no loop owner. This scaffold owns
the SafetyController, wraps process_pending, and exposes the cadence hook (run_cycle) that later
sub-slices extend (L7 evaluate is already wired via the breaker= passthrough; S4.2 adds the
signing canary, S4.5 the reconcile). It starts effectively HALTED: the held SafetyController is
HALTED on construction, so the first cycle never trades until a clean transition (S4.5) flips it
to RUNNING.

Each run_cycle: beat the heartbeat (fate-isolated file; if wired) THEN drive process_pending with
the controller consulted FIRST. The beat-before-process order matters: the out-of-band supervisor
(S4.3) watches the heartbeat, so a cycle that is about to process must first prove liveness.
Clocks are injected for deterministic TDD.
"""

from polybot.ers.service import process_pending


class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, clock):
        self._store = store
        self._book_for = book_for
        self._caps = caps
        self._signer = signer
        self._controller = controller   # the SafetyController (starts HALTED)
        self._breaker = breaker
        self._pipeline = pipeline
        self._heartbeat = heartbeat
        self._clock = clock
        # The working portfolio is threaded across cycles (S4.5 rebuilds it from reconcile on
        # boot; for the scaffold it starts empty at this NAV and folds each cycle's ACCEPTs).
        self._portfolio = self._empty_portfolio()

    def _empty_portfolio(self):
        from polybot.ers.validator import Portfolio
        return Portfolio(nav=self._caps.nav)

    def run_cycle(self):
        """One cadence tick: beat (if wired) THEN process_pending(controller=...). Returns the
        updated portfolio (threaded for the next cycle)."""
        if self._heartbeat is not None:
            self._heartbeat.beat()
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio, caps=self._caps,
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller)
        return self._portfolio
```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_controller.py -v'`
  - Expected: **PASS** (3 tests).

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc "cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_controller.py && git commit -m 'feat(ers): ERSController runloop scaffold, beat-then-process, starts HALTED (S4/POL-6)'"`

---

### Task 5: Extend the facade structural sweep — no kill/pause/cancel/set_state surface

The `ProposeOnlyFacade` is the entire Hermes safety boundary. S4 adds a whole new control surface (kill/pause/flatten/cancel_all/set_state). This task makes it load-bearing that NONE of it leaked onto the facade — including single-underscore variants — exactly mirroring the existing sweep's discipline.

**Files:**
- Modify: `tests/test_ers_facade.py`

- [ ] **Step 1: Write the failing test — the S4 control-surface sweep**

Append a new test to `tests/test_ers_facade.py` (the file already imports `inspect`, `IntentStore`, `ProposeOnlyFacade`, and defines `_store` + `_PROPOSAL`):

```python
def test_structural_sweep_no_s4_kill_or_op_state_surface(tmp_path):
    """S4.1 (POL-6): the new operational control surface (kill / pause / flatten / cancel_all /
    op-state mutation) is a SEPARATE authority path (L6 supervisor + L8 Telegram) and must NOT be
    reachable through the Hermes facade. A compromised/confused Hermes can at worst enqueue a
    PROPOSED row -- it can never stop, flatten, cancel, or re-state the bot. Mirror the existing
    sweep: forbid the bare AND single-underscore form of every S4 control verb."""
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)

        # (a) The public surface is STILL exactly the 7 allowed names -- S4 added nothing.
        allowed = {
            "propose_trade", "get", "audit_log",
            "get_market", "get_book", "get_ledger", "get_flags",
        }
        public = {name for name in dir(facade) if not name.startswith("_")}
        assert public == allowed, f"S4 leaked public surface: {public ^ allowed}"

        # (b) No S4 kill-path / op-state-mutation attribute is reachable, bare OR single-underscore.
        for name in ("cancel_all", "kill", "pause", "resume", "halt", "flatten",
                     "set_state", "state", "verdict", "active_caps", "controller",
                     "record_op_event", "op_audit_log", "place_gtd_bracket", "run_canary"):
            assert not hasattr(facade, name), f"S4 forbidden attr exposed: {name}"
            assert name not in dir(facade), name
            assert not hasattr(facade, "_" + name), \
                f"S4 forbidden single-underscore attr exposed: _{name}"

        # (c) Still not callable and still composition-only over the store (no new dispatch path).
        assert not callable(facade)
        assert getattr(facade, "_ProposeOnlyFacade__store", None) is store
```

- [ ] **Step 2: Run test to verify it passes (it MUST already pass — the facade has no S4 surface)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_facade.py::test_structural_sweep_no_s4_kill_or_op_state_surface -v'`
  - Expected: **PASS**. This is a guard test, not a RED→GREEN cycle: the `ProposeOnlyFacade` is unchanged, so the sweep passes immediately. This is the intended pass-by-construction case (flagged per the plan rules) — it pins the invariant against *future* careless wiring.

- [ ] **Step 3: Prove the sweep BITES (mutation check — confirm it would catch a leak, then revert)**

Temporarily add a forbidden attribute to `src/polybot/ers/facade.py` to prove the sweep is not vacuous. Add inside `ProposeOnlyFacade` (e.g. right after `audit_log`):

```python
    def cancel_all(self):  # MUTATION PROBE -- must be reverted in Step 5
        return None
```

- [ ] **Step 4: Run the sweep — confirm it now FAILS (the guard is live)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_facade.py::test_structural_sweep_no_s4_kill_or_op_state_surface -v'`
  - Expected: **FAIL** — first on `assert public == allowed` (`cancel_all` in the public surface) and on `assert not hasattr(facade, "cancel_all")`. This proves the sweep would catch a real leak.

- [ ] **Step 5: Revert the mutation probe**

Use Edit to delete the `cancel_all` method added in Step 3 from `src/polybot/ers/facade.py`, restoring it to the original.

- [ ] **Step 6: Re-run the full facade suite to confirm GREEN after revert**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_facade.py -v'`
  - Expected: **PASS** (the original 6 tests + the new S4 sweep = 7).

- [ ] **Step 7: Commit**
  - `wsl -d Ubuntu -- bash -lc "cd ~/projects/polymarket-bot && git add tests/test_ers_facade.py && git commit -m 'test(ers): extend facade structural sweep to forbid S4 kill/op-state surface (S4/POL-6)'"`

---

### Final verification (whole sub-slice)

- [ ] **Step 1: Run the entire suite — confirm the 448-test regression guard holds plus the new S4.1 tests**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'`
  - Expected: **all green** — the pre-existing 448 tests still pass (`controller=None`/no-S4-surface == today) and the new S4.1 tests (op_audit_log, SafetyController, the controller= gate, ERSController, the facade sweep) pass on top.

- [ ] **Step 2: Pinned Opus review** — request a `superpowers:code-reviewer` pass pinned to `model:opus` across the S4.1 diff (probe especially: op-FLATTEN vs L7-FLATTEN precedence is unambiguous; the controller's de-risk fires on the ERS's own signer; op_audit append-only + crash-consistent; the facade sweep genuinely bites; `controller=None` is verbatim today). Re-review after any safety-critical fix.

---

## Sub-slice S4.2: Signer de-risk primitives + GTD brackets + RiskCaps fields + startup self-test

> **Context for the implementer.** This sub-slice extends three existing files **additively** (`ers/caps.py` RiskCaps, `ers/service.py` PaperSigner + the `_fold`/ACCEPT path) and creates three net-new files (`ers/signer.py`, `ers/gtd.py`, `ers/startup_selftest.py`). **Do NOT touch** `evaluate_intent` / the validator dataclasses / `propose_trade`'s INSERT-only chokepoint. The existing 448 tests MUST stay green: `process_pending` keeps `controller=None`-default behaviour, and the new GTD staging only fires on ACCEPT through a new optional collaborator (`gtd_for=None` == today).
>
> **TDD discipline (per the writing-plans skill):** every cycle is RED → GREEN → COMMIT. You MUST *observe the true RED* — run the failing test and confirm it fails for the *stated* reason (import error, AssertionError, wrong attribute) before writing implementation. If a test passes the moment you write it (pass-by-construction), STOP and flag it — that means the assertion isn't biting. Each task below calls out where to watch for that.
>
> **Runner.** The repo runs under WSL. Use the proven WSL recipe:
> `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest <path>::<test> -v'`
> `pyproject.toml` sets `pythonpath=["src"]`, so imports are `from polybot...`. Money is `Decimal` from STRINGS everywhere.
>
> **Critical design point (DESIGN §9, §3 S4.2):** `cancel_all` cancels WORKING/unfilled **ENTRY** orders but KEEPS the protective **GTD EXIT** brackets (the passive backstop). On the shadow `PaperSigner` this is a modeling choice we encode + test directly: `gtd_exits` MUST survive a `cancel_all()`.

---

### Task 1: `Signer` Protocol (the structural contract for signer_A / signer_B)

**Files:**
- Create: `src/polybot/ers/signer.py`
- Test: `tests/test_ers_signer.py`

- [ ] **Step 1: Write the failing test** — pin that the Protocol exists, is runtime-checkable, and that `PaperSigner` (today, before we extend it) is NOT yet a structural `Signer` because it lacks `cancel_all`/`place_gtd_bracket`/`run_canary`. This is what forces us to both define the Protocol AND extend PaperSigner in Task 2.

```python
# tests/test_ers_signer.py
"""The Signer Protocol (S4.2 / POL-6): the structural contract behind signer_A (ERS) and
signer_B (the out-of-band supervisor). A runtime-checkable Protocol documents in the type
system that the supervisor's signer is a DISTINCT instance with the SAME de-risk surface."""
from polybot.ers.signer import Signer


def test_signer_protocol_is_runtime_checkable_and_lists_the_derisk_surface():
    # Runtime-checkable so isinstance() structural checks work in tests + wiring.
    assert getattr(Signer, "_is_runtime_protocol", False) is True
    # The de-risk + canary surface the kill path depends on is named on the Protocol.
    for method in ("place", "flatten", "cancel_all", "place_gtd_bracket", "run_canary"):
        assert hasattr(Signer, method), f"Signer Protocol is missing {method}"


def test_object_missing_a_method_is_not_a_structural_signer():
    class _PartialSigner:
        def place(self, intent, decision): ...
        def flatten(self, positions): ...
        # no cancel_all / place_gtd_bracket / run_canary
    assert not isinstance(_PartialSigner(), Signer)


def test_object_with_full_surface_is_a_structural_signer():
    class _FullSigner:
        def place(self, intent, decision): ...
        def flatten(self, positions): ...
        def cancel_all(self): ...
        def place_gtd_bracket(self, position, *, exit_price, expiry): ...
        def run_canary(self): ...
    assert isinstance(_FullSigner(), Signer)
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_signer.py -v'`
  Expected: **FAIL** with `ModuleNotFoundError: No module named 'polybot.ers.signer'` (the module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/polybot/ers/signer.py
"""The Signer Protocol (S4 / POL-6).

Formalizes the signer SEAM so the ERS's signer (signer_A) and the out-of-band supervisor's
signer (signer_B) are structurally-distinct injected dependencies behind ONE contract. The
type system documents that the supervisor's signer is NOT the wedged ERS's. ``PaperSigner``
(ers/service.py) is the shadow implementation; the real Rust signer (POL-4) is a future
implementation behind the same Protocol. ``@runtime_checkable`` so structural isinstance()
checks work in tests + wiring.

The de-risk surface (DESIGN §4):
  place             -- the entry order (existing)
  flatten           -- exit the named open positions (existing)
  cancel_all        -- cancel WORKING/unfilled ENTRY orders; KEEP the GTD exit brackets
  place_gtd_bracket -- stage a protective standing exit at entry (the passive backstop)
  run_canary        -- sign+place+cancel a min-size order to prove signing health; NEVER blind-retry
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Signer(Protocol):
    def place(self, intent, decision) -> None: ...
    def flatten(self, positions) -> None: ...
    def cancel_all(self) -> None: ...
    def place_gtd_bracket(self, position, *, exit_price, expiry) -> None: ...
    def run_canary(self) -> bool: ...
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_signer.py -v'`
  Expected: **PASS** (3 tests). `test_object_missing_a_method_is_not_a_structural_signer` proves the Protocol bites; `test_object_with_full_surface_is_a_structural_signer` confirms the full surface satisfies it.

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/signer.py tests/test_ers_signer.py && git commit -m "feat(ers): add runtime-checkable Signer Protocol (S4.2/POL-6)"'`

---

### Task 2: Extend `PaperSigner` with `cancel_all` / `place_gtd_bracket` / `run_canary`

**Files:**
- Modify: `src/polybot/ers/service.py` (the `PaperSigner` class — keep existing `place`/`flatten` + `.placed`/`.flattened`)
- Test: `tests/test_ers_paper_signer_derisk.py`

> **Encodes the §9 cancel-vs-keep semantics:** `cancel_all()` records the cancel of working ENTRY orders but DOES NOT clear `gtd_exits`. The test asserts the GTD brackets survive a `cancel_all`.

- [ ] **Step 1: Write the failing test** (RED cycle 2a — the de-risk recorders)

```python
# tests/test_ers_paper_signer_derisk.py
"""PaperSigner S4.2 de-risk primitives: cancel_all / place_gtd_bracket / run_canary, with the
shadow-record lists cancelled_all / gtd_exits. The load-bearing safety property (DESIGN §9):
cancel_all cancels WORKING ENTRY orders but the GTD EXIT brackets SURVIVE -- they are the
passive backstop on a wedge."""
from decimal import Decimal

from polybot.ers.signer import Signer
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition


def _pos(token="A", entry="0.50", risk="12"):
    return OpenPosition("m", "e", "s", "c", Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry), frozen=False)


def test_paper_signer_is_a_structural_signer():
    # Extending it with the three new methods makes it satisfy the Protocol.
    assert isinstance(PaperSigner(), Signer)


def test_new_recorder_lists_start_empty():
    s = PaperSigner()
    assert s.cancelled_all == []
    assert s.gtd_exits == []


def test_place_gtd_bracket_records_the_standing_exit():
    s = PaperSigner()
    s.place_gtd_bracket(_pos(token="A", risk="12"), exit_price=Decimal("0.10"), expiry=1700)
    assert s.gtd_exits == [
        {"token_id": "A", "exit_price": Decimal("0.10"), "expiry": 1700, "size": Decimal("12")}
    ]


def test_cancel_all_records_a_marker():
    s = PaperSigner()
    s.cancel_all()
    assert len(s.cancelled_all) == 1


def test_cancel_all_keeps_the_gtd_exit_brackets():
    # DESIGN §9: cancel_all cancels WORKING ENTRY orders, NOT the protective GTD exits.
    s = PaperSigner()
    s.place_gtd_bracket(_pos(token="A", risk="12"), exit_price=Decimal("0.10"), expiry=1700)
    s.place_gtd_bracket(_pos(token="B", risk="8"), exit_price=Decimal("0.05"), expiry=1800)
    before = list(s.gtd_exits)
    s.cancel_all()
    assert s.gtd_exits == before, "cancel_all must NOT clear the protective GTD exit brackets"
    assert len(s.gtd_exits) == 2


def test_run_canary_returns_true_in_shadow_and_records_nothing_extra():
    # Shadow: returns True (real sign+place+cancel is POL-4). NEVER blind-retries.
    s = PaperSigner()
    assert s.run_canary() is True
    assert s.placed == [] and s.gtd_exits == []
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_paper_signer_derisk.py -v'`
  Expected: **FAIL** — `test_paper_signer_is_a_structural_signer` fails (PaperSigner lacks the methods → not a structural `Signer`), and the recorder tests fail with `AttributeError: 'PaperSigner' object has no attribute 'cancelled_all'` / `'place_gtd_bracket'` / `'run_canary'`.

- [ ] **Step 3: Write minimal implementation** — replace the `PaperSigner` class body in `src/polybot/ers/service.py` with the extended version (keep `place`/`flatten`/`placed`/`flattened` verbatim, ADD the new lists + methods):

```python
class PaperSigner:
    """Signer-seam stub: records the orders the ERS WOULD place (shadow), the FLATTEN exits the
    L7/op-FLATTEN path WOULD signal, the working-entry cancels (kill path), and the pre-staged GTD
    EXIT brackets (the passive backstop) -- no keys or network, so the loop runs end-to-end in
    shadow (S9). Satisfies the ers.signer.Signer Protocol. The real Rust signer + real venue
    de-risking (POL-4) replace it.

    Cancel-vs-keep (DESIGN §9): cancel_all() cancels WORKING/unfilled ENTRY orders and leaves the
    GTD EXIT brackets STANDING -- a cancelAll that also killed the protective exits would INCREASE
    risk on a wedge. The live POL-4 signer must implement that entry-vs-exit distinction.
    """

    def __init__(self):
        self.placed = []
        self.flattened = []
        self.cancelled_all = []   # cancel_all() appends a marker (count of cancels issued)
        self.gtd_exits = []       # place_gtd_bracket(...) appends the standing protective exit

    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

    def flatten(self, positions):
        # Shadow: record which positions the breaker / op-FLATTEN asked to exit.
        self.flattened.append(tuple(p.token_id for p in positions))

    def cancel_all(self):
        # Shadow: cancel WORKING/unfilled ENTRY orders. Deliberately does NOT touch gtd_exits --
        # the protective GTD exit brackets are the passive backstop and must SURVIVE the kill.
        self.cancelled_all.append({"cancelled": "working_entries"})

    def place_gtd_bracket(self, position, *, exit_price, expiry):
        # Shadow: record a pre-staged protective standing exit (good-til-date). size = the
        # position's worst-case risk (notional for a long), the dollars the exit protects.
        self.gtd_exits.append({"token_id": position.token_id, "exit_price": exit_price,
                               "expiry": expiry, "size": position.worst_case_risk})

    def run_canary(self):
        # Shadow: a sign+place+cancel min-size canary returns True (real signing is POL-4).
        # NEVER blind-retries -- a real canary failure must HALT signing (S4.4), not loop.
        return True
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_paper_signer_derisk.py -v'`
  Expected: **PASS** (6 tests). Also run the existing service suite to confirm no regression:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py -q'`
  Expected: **PASS** (unchanged — `place`/`flatten`/`placed`/`flattened` are untouched).

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/service.py tests/test_ers_paper_signer_derisk.py && git commit -m "feat(ers): extend PaperSigner with cancel_all/place_gtd_bracket/run_canary; GTD exits survive cancel_all (S4.2/POL-6)"'`

---

### Task 3: `ers/gtd.py` — `Bracket` + `derive_bracket` (pure bracket derivation)

**Files:**
- Create: `src/polybot/ers/gtd.py`
- Test: `tests/test_ers_gtd.py`

> **Sizing rule (DESIGN §3 S4.2):** the bracket's `size` = the position's worst-case risk (notional for a long), and the AGGREGATE standing-exit across all open positions must be `<= caps.gtd_bracket_aggregate` (which `_verify` pins `== total_open_risk`, $60). `derive_bracket` derives ONE bracket from the accepted `Decision` + the just-folded `position`; it asserts the running aggregate stays within the cap (fail-closed: a bracket that would push standing exits past the ceiling raises). The protective `exit_price` is a conservative floor below entry (we exit to protect, not to profit).

- [ ] **Step 1: Write the failing test** (RED cycle 3 — the pure derivation + the aggregate guard)

```python
# tests/test_ers_gtd.py
"""ers/gtd.py: pure GTD-bracket derivation at entry. A protective standing-exit per accepted
position; the AGGREGATE standing-exit is bounded by caps.gtd_bracket_aggregate (== total_open_risk,
$60). Fail-closed: a bracket that would push the aggregate past the ceiling raises."""
import pytest
from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.gtd import Bracket, derive_bracket
from polybot.ers.validator import Decision, OpenPosition


def _pos(token="A", entry="0.50", risk="12"):
    return OpenPosition("m", "e", "s", "c", Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry), frozen=False)


def test_derive_bracket_returns_a_protective_standing_exit():
    caps = RiskCaps()
    pos = _pos(token="A", entry="0.50", risk="12")
    decision = Decision("ACCEPT", Decimal("12"), Decimal("0.50"), "per_trade_cap")
    bracket = derive_bracket(decision, pos, caps=caps, expiry=1700, standing_exit_total=Decimal("0"))
    assert isinstance(bracket, Bracket)
    assert bracket.token_id == "A"
    assert bracket.size == Decimal("12")                 # protects the full notional
    assert bracket.expiry == 1700
    # Protective exit: strictly BELOW the entry price and inside (0,1).
    assert Decimal(0) < bracket.exit_price < Decimal("0.50")


def test_aggregate_standing_exit_within_total_open_is_allowed():
    caps = RiskCaps()  # gtd_bracket_aggregate == total_open_risk == 60
    pos = _pos(token="B", entry="0.50", risk="18")
    decision = Decision("ACCEPT", Decimal("18"), Decimal("0.50"), "per_market_cap")
    # 40 already standing + this 18 = 58 <= 60 -> OK
    bracket = derive_bracket(decision, pos, caps=caps, expiry=1800,
                             standing_exit_total=Decimal("40"))
    assert bracket.size == Decimal("18")


def test_aggregate_standing_exit_over_total_open_fails_closed():
    caps = RiskCaps()
    pos = _pos(token="C", entry="0.50", risk="18")
    decision = Decision("ACCEPT", Decimal("18"), Decimal("0.50"), "per_market_cap")
    # 50 already standing + this 18 = 68 > 60 -> must raise (fail-closed).
    with pytest.raises(ValueError, match="aggregate|gtd|total_open|standing"):
        derive_bracket(decision, pos, caps=caps, expiry=1900,
                       standing_exit_total=Decimal("50"))


def test_bracket_is_frozen():
    b = Bracket(token_id="A", exit_price=Decimal("0.10"), expiry=1, size=Decimal("12"))
    with pytest.raises(Exception):
        b.exit_price = Decimal("0.20")  # frozen dataclass -> FrozenInstanceError
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_gtd.py -v'`
  Expected: **FAIL** with `ModuleNotFoundError: No module named 'polybot.ers.gtd'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/polybot/ers/gtd.py
"""GTD (good-til-date) protective exit brackets (S4.2 / POL-6).

A pure derivation: from an accepted Decision + the just-folded OpenPosition, produce ONE
standing protective EXIT order. These brackets are the PASSIVE BACKSTOP -- they remain standing
even after the kill path's cancel_all cancels the working ENTRY orders (DESIGN §9), so a wedged
ERS still de-risks at the venue.

Sizing (DESIGN §3 S4.2): each bracket protects the position's full worst-case risk (notional
for a long), and the AGGREGATE standing-exit across all open positions is bounded by
``caps.gtd_bracket_aggregate`` (which _verify pins == total_open_risk, $60). Fail-closed: a
bracket that would push the running aggregate past that ceiling raises -- we never stage more
protective exits than the at-risk ceiling permits.

No persistence, no network, no keys. The live POL-4 signer places the real GTD order.
"""

from dataclasses import dataclass
from decimal import Decimal


# A conservative protective floor: exit at 20% of the entry price (well below entry, inside
# (0,1)). The exact protective price is a modeling choice on the shadow signer; the live POL-4
# signer derives it from the book + the worst-case mark. Kept simple + deterministic here.
_PROTECTIVE_FRACTION = Decimal("0.20")


@dataclass(frozen=True)
class Bracket:
    token_id: str
    exit_price: Decimal
    expiry: int
    size: Decimal


def derive_bracket(decision, position, *, caps, expiry, standing_exit_total=Decimal(0)):
    """Derive the protective GTD exit bracket for a just-accepted position.

    ``standing_exit_total`` is the aggregate size of brackets already standing; this bracket's
    size (the position's worst-case risk) is added and the total must stay <=
    caps.gtd_bracket_aggregate, else we fail closed (raise).
    """
    size = position.worst_case_risk
    projected = standing_exit_total + size
    if projected > caps.gtd_bracket_aggregate:
        raise ValueError(
            f"GTD aggregate standing-exit {projected} exceeds gtd_bracket_aggregate "
            f"({caps.gtd_bracket_aggregate}); refusing to stage bracket for {position.token_id}"
        )
    # Protective exit strictly below the executed entry price (we exit to protect, not profit).
    exit_price = decision.price_exec * _PROTECTIVE_FRACTION
    return Bracket(token_id=position.token_id, exit_price=exit_price, expiry=expiry, size=size)
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_gtd.py -v'`
  Expected: **PASS** (4 tests). Watch `test_aggregate_standing_exit_over_total_open_fails_closed` actually RAISE — that is the fail-closed guard biting.

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/gtd.py tests/test_ers_gtd.py && git commit -m "feat(ers): add gtd.py derive_bracket + Bracket; aggregate standing-exit <= total_open_risk (S4.2/POL-6)"'`

---

### Task 4: New `RiskCaps` fields + extended `_verify` invariants (content_hash auto-covers)

**Files:**
- Modify: `src/polybot/ers/caps.py` (add frozen fields; extend `_verify`; `positives` tuple)
- Test: `tests/test_ers_caps.py` (extend — add S4 cases)

> **New fields (DESIGN §3 S4.2):** `weekly_loss_halt=Decimal("36")`, `consecutive_loss=3` (int), `new_positions_per_hour=2` (int), `new_positions_per_day=6` (int), `gtd_bracket_aggregate=Decimal("60")`, `clock_skew_tolerance_seconds=2` (int), `signing_canary_interval_seconds=300` (int), `dead_man_switch_timeout_seconds=30` (int), `reconcile_tolerance=Decimal("0.50")`.
> **New `_verify` clauses (additive — keep ALL existing ones):**
> 1. `daily_pending_ceiling <= weekly_loss_halt` (a weekly halt must not sit below the daily-pending ceiling)
> 2. `new_positions_per_hour <= new_positions_per_day`
> 3. `gtd_bracket_aggregate == total_open_risk` (the standing-exit ceiling IS the at-risk ceiling)
> 4. the new int/Decimal fields are all `> 0` (extend the `positives` sweep + the int checks)
>
> **content_hash auto-covers** because it serialises `asdict(self)` — adding fields changes the hash with no extra code. We test that explicitly.

- [ ] **Step 1: Write the failing test** (RED cycle 4 — new fields exist, defaults, content_hash moves, and each new invariant bites) — APPEND to `tests/test_ers_caps.py`:

```python
# --- S4.2 (POL-6): new safety-envelope fields + extended _verify invariants ---
import pytest
from decimal import Decimal
from polybot.ers.caps import RiskCaps


def test_s4_new_fields_have_decisions_s0_defaults():
    caps = RiskCaps()
    assert caps.weekly_loss_halt == Decimal("36")
    assert caps.consecutive_loss == 3
    assert caps.new_positions_per_hour == 2
    assert caps.new_positions_per_day == 6
    assert caps.gtd_bracket_aggregate == Decimal("60")        # == total_open_risk
    assert caps.clock_skew_tolerance_seconds == 2
    assert caps.signing_canary_interval_seconds > 0
    assert caps.dead_man_switch_timeout_seconds > 0
    assert caps.reconcile_tolerance > 0


def test_s4_default_caps_still_verify():
    # The default envelope is internally consistent with the new clauses added.
    RiskCaps()  # must not raise


def test_content_hash_changes_when_a_new_field_changes():
    base = RiskCaps().content_hash()
    # consecutive_loss is a non-ordering int field -> a consistent envelope with a different value.
    tweaked = RiskCaps(consecutive_loss=2).content_hash()
    assert base != tweaked


def test_gtd_aggregate_above_total_open_fails_verify():
    # gtd_bracket_aggregate must equal total_open_risk; a looser ceiling is a wiring error.
    with pytest.raises(ValueError, match="gtd_bracket_aggregate|total_open"):
        RiskCaps(gtd_bracket_aggregate=Decimal("90"))


def test_weekly_below_daily_pending_fails_verify():
    # A weekly loss halt must not sit BELOW the daily-pending ceiling ($24).
    with pytest.raises(ValueError, match="weekly_loss_halt|daily_pending"):
        RiskCaps(weekly_loss_halt=Decimal("12"))


def test_rate_per_hour_above_per_day_fails_verify():
    with pytest.raises(ValueError, match="new_positions_per_hour|per_day"):
        RiskCaps(new_positions_per_hour=10)  # 10/hr > 6/day is impossible


def test_non_positive_new_field_fails_verify():
    with pytest.raises(ValueError, match="reconcile_tolerance|> 0"):
        RiskCaps(reconcile_tolerance=Decimal("0"))
    with pytest.raises(ValueError, match="consecutive_loss|> 0"):
        RiskCaps(consecutive_loss=0)
    with pytest.raises(ValueError, match="dead_man_switch_timeout_seconds|> 0"):
        RiskCaps(dead_man_switch_timeout_seconds=0)
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_caps.py -k s4 -v'`
  Expected: **FAIL** — `test_s4_new_fields_have_decisions_s0_defaults` fails with `TypeError: __init__() got an unexpected keyword argument`/`AttributeError` (fields don't exist), and the invariant tests fail with `TypeError: ... unexpected keyword argument 'gtd_bracket_aggregate'` (the field/override doesn't exist yet, so `pytest.raises(ValueError)` is NOT what's raised).

- [ ] **Step 3: Write minimal implementation** — add the fields to `RiskCaps` (after the existing L7 fields, before `__post_init__`) and extend `_verify`:

```python
    # S4 / POL-6 safety-envelope fields (DECISIONS-S0 §4; DOC-only until now). All frozen +
    # _verify-checked + auto-covered by content_hash (asdict serialisation).
    weekly_loss_halt: Decimal = Decimal("36")          # realized weekly loss -> halt+human-review
    consecutive_loss: int = 3                          # N losing trades in a row -> halt
    new_positions_per_hour: int = 2                    # budget-independent rate counter
    new_positions_per_day: int = 6
    gtd_bracket_aggregate: Decimal = Decimal("60")     # aggregate standing-exit ceiling (= total_open_risk)
    clock_skew_tolerance_seconds: int = 2              # wall vs NTP skew that halts SIGNING (L5)
    signing_canary_interval_seconds: int = 300         # cadence of the sign+place+cancel canary
    dead_man_switch_timeout_seconds: int = 30          # stale-heartbeat age -> supervisor FLATTEN_AND_KILL
    reconcile_tolerance: Decimal = Decimal("0.50")     # 3-way divergence tolerance (settle-window-aware)
```

Then ADD these clauses inside `_verify`, after the existing L7-velocity checks (just before the method ends):

```python
        # --- S4 / POL-6 additive invariants ---
        # A weekly realized-loss halt must not sit BELOW the daily-pending ceiling.
        if self.daily_pending_ceiling > self.weekly_loss_halt:
            raise ValueError(
                f"weekly_loss_halt({self.weekly_loss_halt}) must be >= daily_pending_ceiling "
                f"({self.daily_pending_ceiling})"
            )
        # The aggregate GTD standing-exit ceiling IS the absolute at-risk ceiling -- never looser.
        if self.gtd_bracket_aggregate != self.total_open_risk:
            raise ValueError(
                f"gtd_bracket_aggregate({self.gtd_bracket_aggregate}) must equal total_open_risk "
                f"({self.total_open_risk})"
            )
        # The hourly new-position rate cannot exceed the daily rate.
        if self.new_positions_per_hour > self.new_positions_per_day:
            raise ValueError(
                f"new_positions_per_hour({self.new_positions_per_hour}) must be <= "
                f"new_positions_per_day({self.new_positions_per_day})"
            )
        # All the new strictly-positive scalars.
        if self.weekly_loss_halt <= 0:
            raise ValueError(f"weekly_loss_halt must be > 0, got {self.weekly_loss_halt}")
        if self.reconcile_tolerance <= 0:
            raise ValueError(f"reconcile_tolerance must be > 0, got {self.reconcile_tolerance}")
        for name in ("consecutive_loss", "new_positions_per_hour", "new_positions_per_day",
                     "clock_skew_tolerance_seconds", "signing_canary_interval_seconds",
                     "dead_man_switch_timeout_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_caps.py -v'`
  Expected: **PASS** (all existing caps tests + the 8 new S4 tests). The existing `content_hash` determinism/uniqueness tests still pass (asdict auto-covers the new fields). Watch each `test_*_fails_verify` raise `ValueError` (not `TypeError`) — that confirms the override reaches `_verify` and the clause bites.

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/caps.py tests/test_ers_caps.py && git commit -m "feat(ers): add S4 RiskCaps fields (weekly/consecutive/rate/gtd/skew/canary/dead-man/recon) + _verify invariants (S4.2/POL-6)"'`

---

### Task 5: `ers/startup_selftest.py` — `verify_or_refuse` (refuse-to-start gate)

**Files:**
- Create: `src/polybot/ers/startup_selftest.py`
- Test: `tests/test_ers_startup_selftest.py`

> **Promotes `content_hash()` to a real refuse-to-start gate (DESIGN §3 S4.2, §9).** `verify_or_refuse` raises `StartupSelfTestError` on: a caps `content_hash` that doesn't match the signed `expected_caps_hash`; a wrong pUSD address; or a struct-hash mismatch. ERC-20 allowance + the real sign-canary are DOCUMENTED SEAMS (`struct_hashes=None` means "no struct hashes to check yet" — but a non-None mismatch MUST raise). Passes silently (returns `None`) when everything matches.

- [ ] **Step 1: Write the failing test** (RED cycle 5 — passes on signed caps, raises on each tamper)

```python
# tests/test_ers_startup_selftest.py
"""Startup self-test (S4.2 / POL-6): refuse to start on a tampered signed-caps content_hash, a
wrong pUSD address, or a struct-hash mismatch. Fail-closed (DESIGN §6): default under ambiguity
is DO NOT TRADE. ERC-20 allowance + the real sign-canary are documented SEAMS (POL-4)."""
import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.startup_selftest import (
    PUSD_ADDRESS, StartupSelfTestError, verify_or_refuse,
)


def test_pusd_address_constant_is_the_canonical_collateral():
    assert PUSD_ADDRESS == "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


def test_verify_passes_on_the_signed_caps():
    caps = RiskCaps()
    # The signed hash is the hash of the very caps we hand in -> matches -> no raise.
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash())  # returns None


def test_verify_refuses_on_a_tampered_caps_hash():
    caps = RiskCaps()
    with pytest.raises(StartupSelfTestError, match="caps|hash|content_hash"):
        verify_or_refuse(caps, expected_caps_hash="deadbeef" * 8)


def test_verify_refuses_on_a_caps_that_no_longer_matches_its_signed_hash():
    # A different (still-consistent) envelope vs the originally-signed hash -> mismatch -> refuse.
    signed = RiskCaps().content_hash()
    tightened = RiskCaps(consecutive_loss=2)            # consistent but DIFFERENT -> different hash
    with pytest.raises(StartupSelfTestError, match="caps|hash"):
        verify_or_refuse(tightened, expected_caps_hash=signed)


def test_verify_refuses_on_a_wrong_pusd_address():
    caps = RiskCaps()
    with pytest.raises(StartupSelfTestError, match="pUSD|address|collateral"):
        verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                         pusd_address="0x0000000000000000000000000000000000000000")


def test_verify_refuses_on_a_struct_hash_mismatch():
    caps = RiskCaps()
    expected = {"order_struct": "0xaaa", "domain": "0xbbb"}
    observed = {"order_struct": "0xaaa", "domain": "0xWRONG"}
    with pytest.raises(StartupSelfTestError, match="struct|hash"):
        verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                         struct_hashes=(expected, observed))


def test_verify_passes_with_matching_struct_hashes():
    caps = RiskCaps()
    same = {"order_struct": "0xaaa", "domain": "0xbbb"}
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                     struct_hashes=(same, dict(same)))  # equal -> no raise


def test_struct_hashes_none_is_a_documented_seam_not_a_failure():
    # struct_hashes=None means "no struct hashes to check yet" (POL-4 seam) -> must NOT raise.
    caps = RiskCaps()
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash(), struct_hashes=None)
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_startup_selftest.py -v'`
  Expected: **FAIL** with `ModuleNotFoundError: No module named 'polybot.ers.startup_selftest'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/polybot/ers/startup_selftest.py
"""Refuse-to-start self-test (S4.2 / POL-6).

Promotes RiskCaps.content_hash() to a real boot gate: the bot REFUSES TO START unless the
signed risk-caps hash matches, the pUSD collateral address is the canonical one, and (when
supplied) the EIP-712 order/domain struct hashes match. Fail-closed (DESIGN §6): the default
under ANY mismatch is DO NOT TRADE -- raise StartupSelfTestError, never silently proceed.

DOCUMENTED SEAMS (POL-4 / deploy, NOT silently skipped): the on-chain ERC-20 ALLOWANCE check
(needs a funded wallet) and the REAL sign-canary (needs the live Rust signer). They are absent
here by construction; struct_hashes=None means "no struct hashes wired yet" -- the codeable
checks (caps hash, pUSD address) still gate startup unconditionally.
"""

# Polymarket pUSD (the V2 CLOB collateral). Pinned so a wrong/poisoned config refuses to start.
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


class StartupSelfTestError(Exception):
    """Raised to REFUSE startup on any signed-caps / address / struct-hash mismatch."""


def verify_or_refuse(caps, *, expected_caps_hash, pusd_address=PUSD_ADDRESS, struct_hashes=None):
    """Raise StartupSelfTestError unless the boot environment matches the signed envelope.

    - caps.content_hash() must equal expected_caps_hash (tamper-evidence on the signed caps).
    - pusd_address must equal the canonical PUSD_ADDRESS (the collateral the bot will spend).
    - struct_hashes, when not None, is an (expected, observed) pair of dicts that must be equal
      (EIP-712 order/domain hashes). None = a documented POL-4 seam, NOT a failure.

    Returns None on success.
    """
    actual_hash = caps.content_hash()
    if actual_hash != expected_caps_hash:
        raise StartupSelfTestError(
            f"signed caps content_hash mismatch: expected {expected_caps_hash}, got {actual_hash}"
        )
    if pusd_address != PUSD_ADDRESS:
        raise StartupSelfTestError(
            f"pUSD collateral address mismatch: expected {PUSD_ADDRESS}, got {pusd_address}"
        )
    if struct_hashes is not None:
        expected_structs, observed_structs = struct_hashes
        if expected_structs != observed_structs:
            raise StartupSelfTestError(
                f"EIP-712 struct hash mismatch: expected {expected_structs}, got {observed_structs}"
            )
    return None
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_startup_selftest.py -v'`
  Expected: **PASS** (8 tests). Watch the three `refuses_*` tests RAISE `StartupSelfTestError` for the correct reason; watch `test_struct_hashes_none_is_a_documented_seam_not_a_failure` NOT raise (the seam stays open).

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/startup_selftest.py tests/test_ers_startup_selftest.py && git commit -m "feat(ers): add startup_selftest.verify_or_refuse (caps-hash/pUSD/struct gate, fail-closed) (S4.2/POL-6)"'`

---

### Task 6: Stage a GTD bracket on every ACCEPT in `process_pending` (the wiring)

**Files:**
- Modify: `src/polybot/ers/service.py` (the ACCEPT branch in `process_pending`)
- Test: `tests/test_ers_service.py` (extend — add the GTD-staging case)

> **DESIGN §2/§3 S4.2:** on ACCEPT, `signer.place(intent, decision)` is followed by `signer.place_gtd_bracket(...)` right in the `_fold` path, so a protective standing exit is pre-staged for every accepted position (the passive backstop the supervisor relies on). We add an OPTIONAL `gtd_for=None` collaborator: a callable `(decision, position, caps, standing_exit_total) -> Bracket`. **`gtd_for=None` == today's behaviour** (no GTD staging) so the 448 tests stay green. In production the `ERSController` passes `derive_bracket` bound with an `expiry`. We also accumulate `standing_exit_total` across accepts so the aggregate cap (Task 3) is enforced per-cycle.

- [ ] **Step 1: Write the failing test** (RED cycle 6 — a GTD bracket is staged for the ACCEPT; default path unchanged) — APPEND to `tests/test_ers_service.py`:

```python
def test_gtd_bracket_is_staged_for_each_accept(tmp_path):
    # On ACCEPT the ERS pre-stages a protective GTD exit bracket on the signer right after place.
    from polybot.ers.gtd import derive_bracket
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        gtd_for = lambda decision, position, *, caps, standing_exit_total: derive_bracket(
            decision, position, caps=caps, expiry=1700, standing_exit_total=standing_exit_total)
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, gtd_for=gtd_for)
        assert store.get("i1").status == "ACCEPTED"
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        # The protective standing exit was staged for the accepted position.
        assert len(signer.gtd_exits) == 1
        assert signer.gtd_exits[0]["token_id"] == "t1"
        assert signer.gtd_exits[0]["size"] == Decimal("12")     # == the per_trade-capped stake


def test_no_gtd_staging_when_gtd_for_is_none(tmp_path):
    # gtd_for=None (the default) == today's behavior: no GTD brackets staged. Guards the 448.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)
        assert store.get("i1").status == "ACCEPTED"
        assert signer.gtd_exits == []
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py -k gtd -v'`
  Expected: **FAIL** — `test_gtd_bracket_is_staged_for_each_accept` fails with `TypeError: process_pending() got an unexpected keyword argument 'gtd_for'`. (`test_no_gtd_staging_when_gtd_for_is_none` would pass-by-construction today since `gtd_exits == []` already — that's expected; it is the GUARD test that proves the new wiring is opt-in, so it must KEEP passing after Step 3. Note it cannot run before the signature accepts `gtd_for`, so it will also error in this RED run.)

- [ ] **Step 3: Write minimal implementation** — extend the `process_pending` signature and the ACCEPT branch in `src/polybot/ers/service.py`:

Change the signature line:

```python
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, gtd_for=None):
```

Then, in the loop, replace the ACCEPT branch:

```python
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision)
            if gtd_for is not None:
                # Pre-stage the protective GTD exit for the just-folded position (the passive
                # backstop). standing_exit_total = the aggregate already staged this cycle, so the
                # derivation enforces caps.gtd_bracket_aggregate. The folded position is the last one.
                position = portfolio.positions[-1]
                standing = sum((Decimal(b["size"]) for b in signer.gtd_exits), Decimal(0))
                bracket = gtd_for(decision, position, caps=caps, standing_exit_total=standing)
                signer.place_gtd_bracket(position, exit_price=bracket.exit_price,
                                         expiry=bracket.expiry)
    return portfolio
```

> **Implementer note:** keep the existing `block_reason` short-circuit, the per-intent `try/except`, and the `_fold` call EXACTLY as they are — only the post-ACCEPT GTD staging is added. The `gtd_exits` accumulation uses `signer.gtd_exits` directly (the shadow record is the source of truth for "what's standing this cycle").

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py -v'`
  Expected: **PASS** (all existing service tests + the 2 new ones). Then run the FULL suite to confirm the 448 baseline holds:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'`
  Expected: **PASS** (baseline + all S4.2 additions; `controller`/`gtd_for` default-None paths leave existing behaviour verbatim).

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/service.py tests/test_ers_service.py && git commit -m "feat(ers): stage a GTD bracket on every ACCEPT via opt-in gtd_for (None == today) (S4.2/POL-6)"'`

---

### Task 7: Wire `daily_pending_ceiling` ($24) into a halt-new helper (no double-count)

**Files:**
- Modify: `src/polybot/ers/safety.py` (ADD a pure helper — module created in S4.1; if S4.1 is not yet merged, create `safety.py` with just this function and let S4.1 extend it)
- Test: `tests/test_ers_safety_daily_ceiling.py`

> **DESIGN §3 S4.2 + §9:** `daily_pending_ceiling` ($24) exists in `RiskCaps` but is UNENFORCED. We add a PURE halt-new predicate that the `SafetyController` (S4.1) will consult: given the worst-case risk already pending TODAY and a prospective new position's worst-case risk, return whether accepting it would CROSS the $24 ceiling. It must NOT double-count vs the L7 breaker or the validator's existing `total_open` cap — it is a *pending-flow rate* gate (new dollars proposed per day), distinct from the at-risk *stock* the validator caps. We keep it a standalone pure function so S4.1's `SafetyController.verdict` calls it and emits `block_reason` cleanly.

- [ ] **Step 1: Write the failing test** (RED cycle 7 — the boundary + fail-closed behaviour)

```python
# tests/test_ers_safety_daily_ceiling.py
"""daily_pending_ceiling ($24) halt-new wiring (S4.2 / POL-6). A PURE pending-FLOW rate gate
(new worst-case dollars proposed today), distinct from the validator's at-risk STOCK cap and
the L7 breaker -- so it never double-counts. Fail-closed: it BLOCKS when accepting would cross
the ceiling (>), allows at-or-below."""
from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.safety import would_cross_daily_pending_ceiling


def test_below_ceiling_is_allowed():
    caps = RiskCaps()  # daily_pending_ceiling == 24
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("10"), new_worst_case=Decimal("12"), caps=caps) is False  # 22 <= 24


def test_exactly_at_ceiling_is_allowed():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("12"), new_worst_case=Decimal("12"), caps=caps) is False  # 24 == 24


def test_crossing_the_ceiling_halts_new():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("18"), new_worst_case=Decimal("12"), caps=caps) is True   # 30 > 24


def test_already_over_ceiling_halts_new_even_for_a_tiny_add():
    caps = RiskCaps()
    assert would_cross_daily_pending_ceiling(
        pending_today=Decimal("24"), new_worst_case=Decimal("0.01"), caps=caps) is True
```

- [ ] **Step 2: Run test to verify it fails** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_safety_daily_ceiling.py -v'`
  Expected: **FAIL** with `ImportError: cannot import name 'would_cross_daily_pending_ceiling' from 'polybot.ers.safety'` (or `ModuleNotFoundError` if `safety.py` doesn't exist yet — see the note below).

- [ ] **Step 3: Write minimal implementation** — add to `src/polybot/ers/safety.py`:

> If `ers/safety.py` already exists from S4.1, APPEND this function (do not disturb the op-state vocab / `OpVerdict` / `SafetyController`). If S4.2 is implemented before S4.1, create `src/polybot/ers/safety.py` with ONLY this function + the module docstring below; S4.1 then adds the op-state machine to the same module.

```python
def would_cross_daily_pending_ceiling(*, pending_today, new_worst_case, caps):
    """True if accepting ``new_worst_case`` would push today's pending worst-case-risk FLOW past
    caps.daily_pending_ceiling ($24). A pending-FLOW rate gate (new dollars proposed per day),
    DISTINCT from the validator's at-risk STOCK cap (total_open_risk) and the L7 unrealized
    breaker -- so it never double-counts. Fail-closed: blocks on a STRICT crossing (> ceiling);
    allows at-or-below. The SafetyController (S4.1) consults this and emits the halt-new
    block_reason; the per-day pending total is read from the durable fill/op tables.
    """
    return (pending_today + new_worst_case) > caps.daily_pending_ceiling
```

If creating the module fresh, prepend:

```python
# src/polybot/ers/safety.py
"""ERS operational-safety layer (S4 / POL-6): the op-state machine, the SafetyController loop
gate, and pure halt-new predicates. Fail-closed throughout. Clocks injected for deterministic
TDD. (S4.1 adds the op-state vocab + OpVerdict + SafetyController to this module.)"""
from decimal import Decimal
```

- [ ] **Step 4: Run test to verify it passes** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_safety_daily_ceiling.py -v'`
  Expected: **PASS** (4 tests). Watch `test_exactly_at_ceiling_is_allowed` (boundary `==` is allowed) and `test_crossing_the_ceiling_halts_new` (`>` blocks) — the two boundary cases prove the strict-inequality semantics.

- [ ] **Step 5: Commit**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_safety_daily_ceiling.py && git commit -m "feat(ers): wire daily_pending_ceiling halt-new predicate (pending-flow gate, no double-count) (S4.2/POL-6)"'`

---

### Task 8: Full-suite green gate (the 448 baseline + S4.2 additions)

**Files:** none (verification only).

- [ ] **Step 1: Run the FULL suite** — Run:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'`
  Expected: **PASS** — the prior **448** plus the S4.2 additions (Tasks 1–7). Confirm the count went UP and NOTHING regressed. The acceptance criterion (DESIGN §8.1) is that the existing 448 stay green because every new seam (`gtd_for=None`, the new RiskCaps fields with consistent defaults, the new module functions) is additive.

- [ ] **Step 2: If anything in the original 448 went RED** — do NOT patch the test. Diagnose: the only plausible regression is the new `RiskCaps` `_verify` clauses rejecting a previously-valid custom envelope in an existing test, or `content_hash` determinism tests that hard-code a hex digest. If an existing test hard-codes a caps `content_hash`, that digest changed (the new fields are in `asdict`); that test belongs to S0/S3 and the hash literal must be regenerated as part of THIS additive change — flag it in the commit message and regenerate the literal from `RiskCaps().content_hash()`. (Grep first: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && grep -rn "content_hash" tests/'` — if no literal digest is asserted, no action needed.)

- [ ] **Step 3: Commit any regenerated hash literal (only if Step 2 found one)**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/ && git commit -m "test(ers): regenerate caps content_hash literal after additive S4.2 fields (S4.2/POL-6)"'`

> **Hand-off to the Opus review (DESIGN §8.4):** after S4.1–S4.3 land, a pinned `model:opus` `superpowers:code-reviewer` pass probes (for THIS sub-slice): (a) the cancel_all-keeps-GTD semantics on the shadow signer + the live POL-4 requirement flagged in the code comment; (b) that `daily_pending_ceiling` wiring does NOT double-count vs the validator/L7; (c) that the new `_verify` invariants can't be loosened past the signed $60 ceiling; (d) that `verify_or_refuse` genuinely gates (codeable checks) and the deferred allowance/canary checks are explicit seams, not silent skips.

---

## Sub-slice S4.3: L6 out-of-band supervisor + Heartbeat + the wedged-process acceptance gate

> **THE headline acceptance gate.** Builds `ers/heartbeat.py` (fate-isolated file heartbeat), `ers/supervisor.py` (`OutOfBandSupervisor` pure decision unit + hard-kill/de-risk + `WedgedSigner` double), and `tests/test_ers_supervisor_kill.py` (fast pure-units + the ONE subprocess-backed acceptance test).
>
> **DEPENDENCY ORDER — read before starting.** This sub-slice depends on **S4.1** (`ers/controller.py::ERSController`, `ers/safety.py::SafetyController`) and **S4.2** (`PaperSigner.cancel_all`/`cancelled_all`/`gtd_exits`/`place_gtd_bracket`, `RiskCaps.dead_man_switch_timeout_seconds`, `ers/gtd.py`). **Do NOT start S4.3 until S4.1 and S4.2 are merged/landed on `pol-6-safety-envelope`.** Two cheap pre-flight checks (Tasks 0a/0b below) assert the upstream symbols exist before you write a line of S4.3 — run them first; if either fails, the upstream slice is not done.
>
> **Environment.** Repo is WSL `~/projects/polymarket-bot`. Run pytest natively in WSL: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest <path>::<test> -v'`. `pyproject.toml` sets `pythonpath=["src"]`, so imports are `from polybot...`. Money = `Decimal`. The subprocess start method on WSL/Linux is **`fork`** (cheap, inherits `tmp_path`/imports) — do **not** set `spawn`. The current branch is `pol-6-safety-envelope`.
>
> **Commits.** Conventional style, reference S4/POL-6, **omit any Co-Authored-By trailer**. One commit per RED→GREEN cycle. The implementer must **observe each true RED** (watch it fail for the stated reason) — any cycle flagged "pass-by-construction" below must be confirmed not-skippable.

---

### Task 0a: Pre-flight — assert S4.1 upstream symbols exist

**Files:** Test only (throwaway; do **not** commit).

- [ ] **Step 1: Run an import probe for the S4.1 symbols this slice consumes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/python -c "from polybot.ers.controller import ERSController; from polybot.ers.safety import SafetyController, RUNNING, HALTED; print(\"S4.1 OK\")"'`
  - Expected: prints `S4.1 OK`. If `ModuleNotFoundError`/`ImportError` → **S4.1 is not landed; STOP and complete S4.1 first.**

### Task 0b: Pre-flight — assert S4.2 upstream symbols exist

**Files:** Test only (throwaway; do **not** commit).

- [ ] **Step 1: Run an import + attribute probe for the S4.2 seam this slice consumes**
  - Run:
    ```bash
    wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/python -c "
from decimal import Decimal
from polybot.ers.service import PaperSigner
from polybot.ers.caps import RiskCaps
s = PaperSigner()
assert hasattr(s, \"cancel_all\") and hasattr(s, \"cancelled_all\"), \"PaperSigner.cancel_all/cancelled_all missing (S4.2)\"
assert hasattr(s, \"gtd_exits\") and hasattr(s, \"place_gtd_bracket\"), \"PaperSigner.gtd_exits/place_gtd_bracket missing (S4.2)\"
assert hasattr(RiskCaps(), \"dead_man_switch_timeout_seconds\"), \"RiskCaps.dead_man_switch_timeout_seconds missing (S4.2)\"
print(\"S4.2 OK\")
"'
    ```
  - Expected: prints `S4.2 OK`. If any assert/import fails → **S4.2 is not landed; STOP and complete S4.2 first.**

> **Contract note (loud).** The pinned contract names `RiskCaps.dead_man_switch_timeout_seconds`. If S4.2 named the field differently, **stop and reconcile the name with S4.2 — do not silently diverge.** Everything below references `caps.dead_man_switch_timeout_seconds`.

---

### Task 1: `Heartbeat` — fate-isolated file heartbeat

**Files:**
- Create: `src/polybot/ers/heartbeat.py`
- Test: `tests/test_ers_heartbeat.py`

The `Heartbeat` writes a monotonically-increasing counter + a wall-style timestamp to a **file** (so it survives a wedged interpreter and is readable out-of-process). `last_beat_age(now)` returns `now - last_beat_time`; `+inf` if never beaten / file missing. `is_alive(now, *, timeout)` is `last_beat_age <= timeout`. Time is passed IN (injected) for the unit tests — `beat()` records the count, but the age is computed against a `now` the caller supplies, so tests need no real sleep.

> **Design choice (call it out in the docstring).** `beat()` must record a *time* so a reader in a different process can compute age. We store the count AND a timestamp taken from a `clock` injected at construction (defaults to `time.time`); `last_beat_age(now)` = `now - stored_time`. Using an injected clock for the stored time keeps the unit deterministic; the integration test passes the real `time.monotonic` consistently on both sides.

- [ ] **Step 1: Write the failing test** — `tests/test_ers_heartbeat.py`
  ```python
  """Tests for the fate-isolated file Heartbeat (S4.3 / POL-6).

  beat() writes a monotonically-increasing counter + a timestamp to a FILE so the
  out-of-band supervisor can read liveness from a DIFFERENT process. Staleness is
  computed against an injected `now`, so these units need no real sleep.
  """

  import math

  from polybot.ers.heartbeat import Heartbeat


  def test_missing_file_is_infinitely_stale(tmp_path):
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      # Never beaten -> the file does not exist -> age is +inf, not alive at any timeout.
      assert hb.last_beat_age(now=100.0) == math.inf
      assert not hb.is_alive(now=100.0, timeout=5.0)


  def test_fresh_beat_is_alive_and_zero_age(tmp_path):
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      hb.beat()
      assert hb.last_beat_age(now=100.0) == 0.0
      assert hb.is_alive(now=100.0, timeout=5.0)


  def test_age_grows_with_now_and_goes_stale_past_timeout(tmp_path):
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      hb.beat()                                   # stamped at t=100
      assert hb.last_beat_age(now=103.0) == 3.0
      assert hb.is_alive(now=104.9, timeout=5.0)  # 4.9s old, under the 5s timeout
      assert not hb.is_alive(now=106.0, timeout=5.0)  # 6s old -> stale


  def test_counter_increases_monotonically_across_beats(tmp_path):
      # The counter proves a NEW beat landed even if two beats share a coarse clock tick.
      ticks = iter([100.0, 100.0, 100.0])
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: next(ticks))
      hb.beat(); c1 = hb.read_counter()
      hb.beat(); c2 = hb.read_counter()
      hb.beat(); c3 = hb.read_counter()
      assert c1 < c2 < c3


  def test_read_is_out_of_process_safe_via_a_fresh_handle(tmp_path):
      # A reader constructed AFTER the writer (no shared in-memory state) sees the beat:
      # this is exactly what the supervisor in another process does.
      path = str(tmp_path / "hb")
      writer = Heartbeat(path, clock=lambda: 200.0)
      writer.beat()
      reader = Heartbeat(path, clock=lambda: 999.0)   # different "clock", own handle
      assert reader.last_beat_age(now=205.0) == 5.0   # age uses the STORED 200, not the reader clock
      assert reader.is_alive(now=205.0, timeout=10.0)
  ```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_heartbeat.py -v'`
  - Expected: FAIL with `ModuleNotFoundError: No module named 'polybot.ers.heartbeat'`.

- [ ] **Step 3: Write minimal implementation** — `src/polybot/ers/heartbeat.py`
  ```python
  """Fate-isolated file heartbeat (S4.3 / POL-6).

  The trading loop's ERSController calls ``beat()`` each cycle, writing a
  monotonically-increasing counter + a timestamp to a FILE. The out-of-band
  supervisor (a SEPARATE OS process) reads that file to decide liveness. A file is
  used deliberately: it survives a wedged interpreter and is readable out-of-process,
  unlike the in-process EventStore / MonotonicStamper which die with the process.

  Staleness is computed against an injected ``now`` (``last_beat_age(now)`` =
  ``now - stored_time``), so the unit tests are deterministic with no real sleep; the
  integration gate passes the real ``time.monotonic`` consistently to both sides.

  Fail-closed: a missing/never-written file reads as +inf age (NOT alive). The write is
  best-effort-atomic (write to a temp sibling + ``os.replace``) so a reader never sees a
  half-written line.
  """

  import math
  import os
  import time


  class Heartbeat:
      def __init__(self, path, *, clock=None):
          self._path = path
          self._clock = clock or time.time
          self._counter = 0

      def beat(self):
          self._counter += 1
          line = f"{self._counter} {self._clock()!r}\n"
          tmp = f"{self._path}.tmp"
          with open(tmp, "w") as fh:
              fh.write(line)
              fh.flush()
              os.fsync(fh.fileno())
          os.replace(tmp, self._path)   # atomic on POSIX -> reader never sees a partial line

      def _read(self):
          """(counter, timestamp) or None if the file is missing / unreadable / partial."""
          try:
              with open(self._path) as fh:
                  raw = fh.read().strip()
          except (FileNotFoundError, OSError):
              return None
          if not raw:
              return None
          parts = raw.split()
          if len(parts) != 2:
              return None
          try:
              return int(parts[0]), float(parts[1])
          except ValueError:
              return None

      def read_counter(self):
          rec = self._read()
          return None if rec is None else rec[0]

      def last_beat_age(self, now):
          rec = self._read()
          if rec is None:
              return math.inf       # never beaten / unreadable -> infinitely stale (fail closed)
          return now - rec[1]

      def is_alive(self, now, *, timeout):
          return self.last_beat_age(now) <= timeout
  ```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_heartbeat.py -v'`
  - Expected: PASS (5 tests).

- [ ] **Step 5: Commit**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/heartbeat.py tests/test_ers_heartbeat.py && git commit -m "feat(S4/POL-6): fate-isolated file Heartbeat (beat/last_beat_age/is_alive)"'`

---

### Task 2: `OutOfBandSupervisor.decide` — the pure dead-man decision unit

**Files:**
- Create: `src/polybot/ers/supervisor.py`
- Test: `tests/test_ers_supervisor_kill.py` (this task creates the file; later tasks append to it)

`decide(now)` reads the heartbeat the supervisor holds and returns `"OK"` or `"FLATTEN_AND_KILL"`: a stale heartbeat past `caps.dead_man_switch_timeout_seconds` → `"FLATTEN_AND_KILL"`, else `"OK"`. The supervisor holds **its own** signer (`signer_B`) — a distinct instance from the ERS's `signer_A`. Clock injected; no real sleep.

> The pinned contract names the timeout `dead_man_switch_timeout_seconds`. This task's unit tests must NOT touch a process — they drive `decide` purely against a `Heartbeat` whose age is controlled by the injected `now`.

- [ ] **Step 1: Write the failing test** — create `tests/test_ers_supervisor_kill.py` with the pure-unit block
  ```python
  """Tests for the out-of-band supervisor + the wedged-process acceptance gate (S4.3 / POL-6).

  Two layers:
    * FAST pure units (this block + Task 3): decide() dead-man timing with an injected
      clock + a file Heartbeat in tmp_path -- no process, no real sleep.
    * The ONE subprocess-backed ACCEPTANCE GATE (Task 4): a real ERS child accepts >=1
      position (so signer_A.gtd_exits is non-empty), beats a file Heartbeat, then WEDGES;
      the parent supervisor detects the stale FILE heartbeat, hard-kills the child PID, and
      fires signer_B.cancel_all/flatten on its OWN distinct signer.

  Fate isolation: the supervisor is a separate process, holds a DISTINCT signer, and
  watches a FILE heartbeat -- it shares nothing with the loop it guards.
  """

  from decimal import Decimal

  from polybot.ers.caps import RiskCaps
  from polybot.ers.heartbeat import Heartbeat
  from polybot.ers.service import PaperSigner
  from polybot.ers.supervisor import OutOfBandSupervisor


  def _caps_dms(seconds):
      # A consistent RiskCaps with the dead-man timeout overridden. dead_man_switch_timeout_seconds
      # is an S4.2 field; overriding it alone keeps every other _verify invariant satisfied.
      return RiskCaps(dead_man_switch_timeout_seconds=seconds)


  def test_decide_ok_when_heartbeat_is_fresh(tmp_path):
      caps = _caps_dms(5)
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      hb.beat()                                  # stamped at t=100
      signer_b = PaperSigner()
      sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=lambda: 102.0)
      assert sup.decide(now=102.0) == "OK"       # 2s old, under the 5s dead-man timeout


  def test_decide_flatten_and_kill_when_stale_past_timeout(tmp_path):
      caps = _caps_dms(5)
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      hb.beat()
      signer_b = PaperSigner()
      sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=lambda: 106.0)
      assert sup.decide(now=106.0) == "FLATTEN_AND_KILL"   # 6s old, past the 5s timeout


  def test_decide_flatten_and_kill_when_never_beaten(tmp_path):
      # No beat ever -> +inf age -> fail closed to FLATTEN_AND_KILL (never assume alive).
      caps = _caps_dms(5)
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)   # file never written
      sup = OutOfBandSupervisor(signer=PaperSigner(), heartbeat=hb, caps=caps, clock=lambda: 100.0)
      assert sup.decide(now=100.0) == "FLATTEN_AND_KILL"


  def test_decide_boundary_exactly_at_timeout_is_ok(tmp_path):
      # age == timeout is still alive (is_alive uses <=); one tick past is the kill.
      caps = _caps_dms(5)
      hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
      hb.beat()
      sup = OutOfBandSupervisor(signer=PaperSigner(), heartbeat=hb, caps=caps, clock=lambda: 105.0)
      assert sup.decide(now=105.0) == "OK"
  ```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v'`
  - Expected: FAIL with `ModuleNotFoundError: No module named 'polybot.ers.supervisor'`.

- [ ] **Step 3: Write minimal implementation** — `src/polybot/ers/supervisor.py` (decide + the seams `on_wedge`/`WedgedSigner` are stubbed now, filled in Task 4 so this task's RED is real)
  ```python
  """Out-of-band L6 supervisor + the wedged-loop test doubles (S4.3 / POL-6).

  Fork 1/2 (DESIGN-S4 §0): the supervisor is a SEPARATE OS process holding its OWN signer
  (signer_B, distinct from the ERS's signer_A) and watching a FILE Heartbeat. It must
  survive a wedged trading-loop interpreter, so it shares nothing with the loop it guards.

  ``decide(now)`` is the PURE dead-man decision (clock-injected, deterministic): a heartbeat
  stale past ``caps.dead_man_switch_timeout_seconds`` -> "FLATTEN_AND_KILL", else "OK". It
  fails CLOSED -- a never-written / unreadable heartbeat reads as +inf age -> kill.

  ``on_wedge`` is the action half: hard-kill the wedged ERS PID (SIGKILL -- a wedged
  interpreter can swallow SIGTERM), THEN de-risk on signer_B (cancel WORKING ENTRY orders +
  flatten the open set). The pre-staged GTD EXIT brackets on signer_A are the PASSIVE
  backstop and are intentionally NOT cancelled here. Live cancelAll/credential separation is
  POL-4-deferred; this is the shadow PaperSigner proof.
  """

  import os
  import signal
  import time

  OK = "OK"
  FLATTEN_AND_KILL = "FLATTEN_AND_KILL"


  class OutOfBandSupervisor:
      def __init__(self, *, signer, heartbeat, caps, clock=None):
          # `signer` is signer_B -- a DISTINCT instance from the ERS's signer_A (fate isolation).
          self._signer = signer
          self._heartbeat = heartbeat
          self._caps = caps
          self._clock = clock or time.monotonic

      def decide(self, now):
          timeout = self._caps.dead_man_switch_timeout_seconds
          if self._heartbeat.is_alive(now, timeout=timeout):
              return OK
          return FLATTEN_AND_KILL   # stale / never-beaten -> fail closed

      def on_wedge(self, ers_pid, open_positions):
          """Hard-kill the wedged ERS, then de-risk on the supervisor's OWN signer.

          Order is load-bearing: kill FIRST (stop the wedged loop from doing anything more),
          THEN cancel working entries + flatten on signer_B. The GTD exit brackets staged on
          signer_A are left standing (the passive backstop)."""
          self._hard_kill(ers_pid)
          self._signer.cancel_all()
          self._signer.flatten(open_positions)

      @staticmethod
      def _hard_kill(pid):
          # SIGKILL: a genuinely-wedged interpreter can ignore SIGTERM; the whole point is fate
          # isolation, so do not negotiate. ProcessLookupError == already dead == success.
          try:
              os.kill(pid, signal.SIGKILL)
          except ProcessLookupError:
              pass
  ```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v'`
  - Expected: PASS (4 tests).

- [ ] **Step 5: Commit**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/supervisor.py tests/test_ers_supervisor_kill.py && git commit -m "feat(S4/POL-6): OutOfBandSupervisor.decide dead-man unit (clock-injected, fail-closed)"'`

---

### Task 3: `on_wedge` de-risks on signer_B (NOT signer_A) and hard-kills — unit-level

**Files:**
- Modify: `tests/test_ers_supervisor_kill.py` (append)

Before the real subprocess gate, pin the `on_wedge` *action contract* with a fast in-process unit: it must call `signer_B.cancel_all()` + `signer_B.flatten(open)` on the supervisor's OWN signer (asserting `signer_B is not signer_A` and `signer_A` is untouched), and must `os.kill` the pid we pass. We kill a short-lived real child process so the kill is genuinely exercised without the full ERS wedge harness.

- [ ] **Step 1: Write the failing test** — append to `tests/test_ers_supervisor_kill.py`
  ```python
  import os
  import signal
  import time
  import multiprocessing as mp

  from polybot.ers.validator import OpenPosition


  def _sleep_forever():
      while True:
          time.sleep(3600)


  def test_on_wedge_kills_pid_and_derisks_only_on_signer_b(tmp_path):
      # signer_A is the (untouched) ERS signer; signer_B is the supervisor's OWN signer.
      signer_a, signer_b = PaperSigner(), PaperSigner()
      assert signer_b is not signer_a

      caps = _caps_dms(5)
      hb = Heartbeat(str(tmp_path / "hb"))
      sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=time.monotonic)

      child = mp.Process(target=_sleep_forever)
      child.start()
      try:
          open_positions = (
              OpenPosition("m", "e", "s", "c", Decimal("12"), False,
                           token_id="t1", entry_price=Decimal("0.50"), frozen=False),
          )
          sup.on_wedge(child.pid, open_positions)

          # (a) the child PID was hard-killed
          child.join(timeout=5)
          assert child.exitcode is not None
          assert child.exitcode == -signal.SIGKILL   # killed by SIGKILL, not a clean exit

          # (b) de-risk landed on signer_B's OWN seam ...
          assert signer_b.cancelled_all          # cancel_all() recorded
          assert signer_b.flattened == [("t1",)] # flatten(open) recorded the token
          # ... and signer_A (the wedged ERS signer) was NOT touched
          assert signer_a.cancelled_all == []
          assert signer_a.flattened == []
      finally:
          if child.is_alive():
              os.kill(child.pid, signal.SIGKILL)
              child.join(5)
  ```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py::test_on_wedge_kills_pid_and_derisks_only_on_signer_b -v'`
  - Expected: this should already PASS if Task 2's `on_wedge` is correct. **This is a pass-by-construction risk** — `on_wedge` was written in Task 2. To observe a TRUE RED for the action contract, FIRST run it; if it passes, deliberately break `on_wedge` (temporarily comment out the `self._signer.flatten(open_positions)` line in `supervisor.py`), re-run, and confirm it FAILs with `assert signer_b.flattened == [("t1",)]`. Then restore the line. (Do not commit the broken state.)
  - Expected after restore: PASS.

- [ ] **Step 3: (no new impl needed — `on_wedge` already exists from Task 2)**
  - This cycle locks the action contract against future regressions; the RED was demonstrated by the temporary break in Step 2.

- [ ] **Step 4: Run the full supervisor unit file**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v'`
  - Expected: PASS (5 tests so far).

- [ ] **Step 5: Commit**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_supervisor_kill.py && git commit -m "test(S4/POL-6): pin on_wedge de-risks on signer_B not signer_A + SIGKILLs the PID"'`

---

### Task 4: `WedgedSigner` + the subprocess-backed ACCEPTANCE GATE

**Files:**
- Modify: `src/polybot/ers/supervisor.py` (add `WedgedSigner`)
- Modify: `tests/test_ers_supervisor_kill.py` (append the gate + its child helpers)

This is the headline. A **real ERS child process** runs an `ERSController` cycle that ACCEPTs ≥1 position (so `signer_A.gtd_exits` is non-empty) and beats a file `Heartbeat`; then it **wedges** (its `WedgedSigner.place` blocks forever, so the loop never returns to beat again). The parent `OutOfBandSupervisor` polls the **file** heartbeat, detects staleness past `dead_man_switch_timeout_seconds`, hard-kills the child PID, fires `signer_B.cancel_all/flatten` on its OWN distinct signer, and the pre-staged GTD brackets that the child wrote to disk remain recorded.

> **Cross-slice fixture (the load-bearing part).** The child must drive a genuine ACCEPT through the S4.1 `ERSController`. `ERSController.__init__(*, store, book_for, caps, signer, controller, breaker=None, pipeline=None, heartbeat=None, clock)` and `SafetyController.__init__(*, caps, store, clock)`. The controller **starts HALTED**, so the child must transition it to `RUNNING` via `controller.set_state(RUNNING, reason=...)` before `run_cycle()` (otherwise the op-state gate blocks the ACCEPT and `gtd_exits` stays empty — the gate would test nothing). The book/intent fixtures are the canonical `_book`/`_P` from `test_ers_service.py`. **If S4.1's `ERSController.run_cycle` does not stage a GTD bracket on ACCEPT (the S4.2 `_fold`-path call to `signer.place_gtd_bracket`), this child cannot satisfy the `gtd_exits` precondition — that wiring belongs to S4.1/S4.2; verify it before assuming the gate can pass.**

> **Determinism + WSL fidelity (DESIGN §9):** poll a `ready` FILE for child startup (kills the heartbeat write/read race — never a blind sleep for "is the child up yet"); use a SHORT real timeout (`dead_man_switch_timeout_seconds=1`) and a single small real sleep to let the file heartbeat genuinely age past it; assert `child.exitcode is not None` to prove the kill landed; assert de-risk on `signer_B` (`is not signer_A`); always `SIGKILL`-and-join in `finally` so a hung child can't wedge the suite. Start method stays `fork` (default on Linux/WSL).

- [ ] **Step 1: Write the failing test** — first add the `WedgedSigner` double + the gate to `tests/test_ers_supervisor_kill.py`
  ```python
  from polybot.core.clock import MonotonicStamper
  from polybot.ers.caps import RiskCaps
  from polybot.ers.controller import ERSController
  from polybot.ers.intent_store import IntentStore
  from polybot.ers.safety import SafetyController, RUNNING
  from polybot.ers.supervisor import WedgedSigner
  from polybot.ers.validator import Portfolio
  from polybot.ingestion.orderbook import LocalBook

  # --- the same canonical fixtures the ERS service tests use ---
  def _book(ask, *, size="1000", bid="0.01"):
      book = LocalBook()
      book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
      return book

  _P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
            max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
            resolution_summary="", thesis="", citations=())

  SHORT_TIMEOUT = 1   # seconds; the dead-man window for the gate (real but bounded)


  def _wedged_ers_child(db_path, hb_path, gtd_path, ready_path):
      """A REAL ERS child: accept >=1 position (staging a GTD bracket on signer_A), beat the
      file heartbeat ONCE, signal ready, then WEDGE forever inside the signer so the loop never
      beats again. Runs in a forked subprocess -- no shared in-memory state with the parent."""
      import json as _json
      stamper = MonotonicStamper()
      store = IntentStore(db_path, stamper)
      store.propose_trade("i1", **_P)

      signer_a = WedgedSigner(wedge_after=1)        # places the 1st order, BLOCKS on the 2nd
      caps = RiskCaps(dead_man_switch_timeout_seconds=SHORT_TIMEOUT)
      controller = SafetyController(caps=caps, store=store, clock=lambda: 0.0)
      controller.set_state(RUNNING, reason="gate_test")   # leave HALTED -> no ACCEPT, no GTD bracket
      hb = Heartbeat(hb_path)
      ers = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=caps,
                          signer=signer_a, controller=controller, heartbeat=hb, clock=lambda: 0.0)

      ers.run_cycle()        # heartbeat.beat() + process_pending -> ACCEPT i1 -> signer_a.gtd_exits non-empty

      # Persist the staged GTD brackets so the PARENT (separate process) can assert they survive.
      with open(gtd_path, "w") as fh:
          _json.dump([{"token_id": g["token_id"]} for g in signer_a.gtd_exits], fh)
      open(ready_path, "w").close()   # tell the parent the heartbeat + GTD bracket are on disk

      # Now WEDGE: a second cycle blocks forever inside WedgedSigner.place; the loop never beats again.
      store.propose_trade("i2", **dict(_P, token_id="t1"))
      ers.run_cycle()        # blocks inside signer_a.place (wedge_after=1 already consumed)


  def test_supervisor_hard_kills_wedged_child_and_flattens_on_signer_b(tmp_path):
      db_path = str(tmp_path / "i.db")
      hb_path = str(tmp_path / "hb")
      gtd_path = str(tmp_path / "gtd.json")
      ready = str(tmp_path / "ready")

      child = mp.Process(target=_wedged_ers_child, args=(db_path, hb_path, gtd_path, ready))
      child.start()
      try:
          # 1. Wait (bounded poll, NO blind sleep) for the child to have staged the GTD + heartbeat.
          deadline = time.monotonic() + 5
          while not os.path.exists(ready) and time.monotonic() < deadline:
              time.sleep(0.01)
          assert os.path.exists(ready), "child never reached ready -> it failed to ACCEPT/stage"

          # 2. The pre-staged GTD bracket exists on signer_A (recorded to disk by the child).
          import json as _json
          with open(gtd_path) as fh:
              staged = _json.load(fh)
          assert staged and staged[0]["token_id"] == "t1", "child did not stage a GTD bracket"

          # 3. Let the FILE heartbeat genuinely go stale past the dead-man timeout (one small real wait).
          hb = Heartbeat(hb_path)
          time.sleep(SHORT_TIMEOUT + 0.2)
          assert not hb.is_alive(now=time.monotonic(), timeout=SHORT_TIMEOUT)

          # 4. The supervisor (its OWN signer_B) decides + acts.
          signer_a_unused, signer_b = PaperSigner(), PaperSigner()
          assert signer_b is not signer_a_unused
          caps = RiskCaps(dead_man_switch_timeout_seconds=SHORT_TIMEOUT)
          sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=time.monotonic)
          assert sup.decide(now=time.monotonic()) == "FLATTEN_AND_KILL"

          open_positions = (
              OpenPosition("m1", "e1", "m1", "e1", Decimal("12"), False,
                           token_id="t1", entry_price=Decimal("0.50"), frozen=False),
          )
          sup.on_wedge(child.pid, open_positions)

          # 5. The child REALLY died (hard kill landed past the wedge).
          child.join(timeout=5)
          assert child.exitcode is not None

          # 6. De-risk fired on the supervisor's OWN signer_B (cancel working entries + flatten).
          assert signer_b.cancelled_all
          assert signer_b.flattened == [("t1",)]

          # 7. The pre-staged GTD EXIT brackets survive the wedge (passive backstop -- still on disk).
          with open(gtd_path) as fh:
              survived = _json.load(fh)
          assert survived and survived[0]["token_id"] == "t1"
      finally:
          if child.is_alive():
              os.kill(child.pid, signal.SIGKILL)
              child.join(5)
  ```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py::test_supervisor_hard_kills_wedged_child_and_flattens_on_signer_b -v'`
  - Expected: FAIL with `ImportError: cannot import name 'WedgedSigner' from 'polybot.ers.supervisor'` (collection error before the gate even runs).

- [ ] **Step 3: Write minimal implementation** — add `WedgedSigner` to `src/polybot/ers/supervisor.py`
  ```python
  import time as _time


  class WedgedSigner:
      """Test double that BLOCKS to wedge a real ERS child (S4.3 acceptance gate).

      Implements the Signer seam. The first ``wedge_after`` place() calls behave like a
      PaperSigner (record + stage a GTD bracket so signer_A.gtd_exits is non-empty); the NEXT
      place() BLOCKS forever (``time.sleep`` loop), so the ERS loop never returns to beat the
      heartbeat again -> the file heartbeat goes stale -> the out-of-band supervisor must
      hard-kill the wedged process. flatten()/cancel_all() exist for protocol completeness.
      """

      def __init__(self, wedge_after=1):
          self._wedge_after = wedge_after
          self._placed = 0
          self.placed = []
          self.flattened = []
          self.cancelled_all = []
          self.gtd_exits = []

      def place(self, intent, decision):
          if self._placed >= self._wedge_after:
              while True:               # genuinely wedged: never returns
                  _time.sleep(3600)
          self._placed += 1
          self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                              "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

      def place_gtd_bracket(self, position, *, exit_price, expiry):
          self.gtd_exits.append({"token_id": position.token_id, "exit_price": exit_price,
                                 "expiry": expiry, "size": position.worst_case_risk})

      def flatten(self, positions):
          self.flattened.append(tuple(p.token_id for p in positions))

      def cancel_all(self):
          self.cancelled_all.append(len(self.placed))

      def run_canary(self):
          return True
  ```

  > **If S4.1/S4.2 did not wire `place_gtd_bracket` into the `ERSController.run_cycle` ACCEPT path** (i.e. `signer_a.gtd_exits` is empty after `run_cycle`), the gate's `assert staged` at parent-Step 2 will fail. That is a **real upstream gap, not a test bug** — `place_gtd_bracket(...)` on ACCEPT is S4.2's `_fold`-path obligation (DESIGN §3 S4.2 / §2 diagram: "ACCEPT → signer.place → signer.place_gtd_bracket → _fold"). Fix it in the slice that owns it; do not paper over it in this test.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py::test_supervisor_hard_kills_wedged_child_and_flattens_on_signer_b -v'`
  - Expected: PASS (the gate). If it fails at parent-Step 2 (`child did not stage a GTD bracket`), inspect the S4.1/S4.2 ACCEPT→`place_gtd_bracket` wiring per the note above. If it hangs, the `finally` SIGKILL guarantees the suite recovers; check that the child actually reached `ready` (the bounded poll) and that `run_cycle` returned after the first ACCEPT.
  - Then run the WHOLE file to confirm the units still pass alongside the gate:
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v'`
  - Expected: PASS (6 tests: 4 decide units + the on_wedge unit + the gate).

- [ ] **Step 5: Commit**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/supervisor.py tests/test_ers_supervisor_kill.py && git commit -m "feat(S4/POL-6): WedgedSigner + subprocess wedged-process acceptance gate (fate isolation)"'`

---

### Task 5: Full-suite regression + flake check

**Files:** none (verification only).

- [ ] **Step 1: Run the entire suite — the 448 existing tests must stay green + the new S4.3 tests pass**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q'`
  - Expected: PASS, count = 448 + 5 (heartbeat) + 6 (supervisor) = **459** (adjust if S4.1/S4.2 added their own; the floor is "no regression in the prior count").

- [ ] **Step 2: Re-run the gate twice to catch subprocess flakiness (DESIGN §9 hazard)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v --count=1; ./.venv/bin/pytest tests/test_ers_supervisor_kill.py -v'`
  - Expected: PASS both times. (If `--count` is unavailable because `pytest-repeat` isn't installed, just run the second invocation; the point is two clean back-to-back runs with no leaked/hung child between them.)
  - If any run leaves a zombie (`ps` shows a stray python from the test), the `finally` SIGKILL guard regressed — fix before proceeding.

- [ ] **Step 3: Commit (only if Step 1/2 surfaced a fix; otherwise skip)**
  - Run: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add -A && git commit -m "test(S4/POL-6): full-suite regression green; supervisor gate stable across reruns"'`

---

### Post-slice (per DESIGN §3 / acceptance §8.4): pinned-Opus code review

After Tasks 1–5 are green, request a **pinned `model:opus` `superpowers:code-reviewer`** pass over `ers/heartbeat.py`, `ers/supervisor.py`, and `tests/test_ers_supervisor_kill.py`. Specifically point the reviewer at the DESIGN §9 hazards this slice owns:
- **Subprocess fidelity / flakiness:** the heartbeat write/read ordering race (mitigated by the `ready`-file poll + atomic `os.replace`), WSL `kill` semantics (SIGKILL + `exitcode is not None`), and that the gate asserts on `signer_B` (`is not signer_A`), not the wedged signer.
- **cancel_all vs the GTD exits:** confirm `on_wedge` cancels working entries but leaves the GTD exit brackets standing (the gate's "GTD brackets survive" assertion), and flag the live POL-4 entry-vs-exit requirement.
- **Determinism:** `decide` is clock-injected (no real sleep in units); the gate uses a single bounded real sleep past a SHORT `dead_man_switch_timeout_seconds`.

Re-review after any safety-critical fix. (Memory pref: a separate Opus review before declaring done.)

---

---

## Done criteria (S4.1–S4.3, the kill path)

- `./.venv/bin/pytest` green; **the existing 448 tests still pass** (`controller=None`/`gtd_for=None` == today; additive seams).
- Per-sub-slice tests (TDD, RED→GREEN) all pass, including: a `KILL`/`PAUSE` op-state short-circuits the loop ahead of the L7 breaker with the **specific** `block_reason`; op-FLATTENING calls `signer.flatten`/`cancel_all`; the precedence ordering holds (op-block can't be overwritten by a weaker L7 verdict); `set_state` appends an op-audit row; the facade structural sweep proves no `kill/pause/cancel/cancel_all/set_state` surface; the new `RiskCaps` fields fail `_verify` on a loosening/inconsistent value; the startup self-test refuses to start on a `content_hash`/pUSD-address mismatch; a GTD bracket is staged per ACCEPT sized so the aggregate standing-exit ≤ `total_open_risk`; **`cancel_all` keeps the GTD exit brackets**.
- **The wedged-process acceptance gate passes** (`tests/test_ers_supervisor_kill.py`): the out-of-band supervisor detects a stale FILE heartbeat from a genuinely-wedged ERS child, **hard-kills the child PID** (`exitcode` confirms), fires `cancel_all`/`flatten` on its OWN distinct signer (`signer_B is not signer_A`), and the pre-staged GTD brackets survive.
- **Pinned-`opus` `superpowers:code-reviewer`** pass per sub-slice (min. after S4.1 + S4.3); re-review after any safety-critical fix.
- `docs/HANDOFF.md` + memory updated; a progress comment on [POL-6](https://mysigner.youtrack.cloud/issue/POL-6).
- Branch `pol-6-safety-envelope`; commit per task; merge to `main` `--no-ff` with the verification status in the merge message. **Confirm before pushing to origin.**

## Repo conventions & fixture cookbook

(The shared ground truth the tasks above are written against — from the conventions scout.)


I have everything needed. Here is the complete brief.

---

# S4 Test + Fixture Conventions Brief — polymarket-bot ERS

Repo: WSL `/home/jurgenubuntu/projects/polymarket-bot` (UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot`). All paths below are absolute under that root.

## (a) Pytest command + conventions

**Config** (`pyproject.toml`, `[tool.pytest.ini_options]`):
```toml
pythonpath = ["src"]
testpaths  = ["tests"]
addopts    = "-q"
```
- **Invocation:** `./.venv/bin/pytest` (run under WSL bash). venv built with `uv venv --python 3.13`; current suite count is **448** passing. Per the WSL-exec memory, run native: write a script to `~` over UNC, `tr -d '\015'`, `bash ~/foo.clean.sh`.
- **Layout:** FLAT `tests/` dir (no nested packages), `tests/__init__.py` is empty. One `test_<module>.py` per source module; ERS files are `test_ers_<name>.py`. Imports are absolute from `polybot.*` (the `pythonpath=["src"]` makes `src/polybot/...` importable).
- **Money is `Decimal` from STRINGS** everywhere: `Decimal("300")`, `Decimal("0.50")`. Never float literals for money. (The component-log `w_news_effective` is the one deliberate float, `0.20`.) Counts (`max_concurrent`) are plain `int`.
- **Plain `assert`** statements (no unittest). Compound asserts like `assert store.get("i1").status == "ACCEPTED"` and `assert a < b < c`.
- **Fail-loud is pinned with `pytest.raises(match=...)`** using a regex on the message substring, e.g. `pytest.raises(ValueError, match="ordering")`, `match="slack|concurrent"`, `match="20%|at-risk"`, `match="L7|flatten|total"`. `import pytest` is done at top of file (caps/clock) or locally inside a test (facade `test_read_tools_fail_loud_without_reader`).
- **Determinism via injected clocks:** `MonotonicStamper(clock=lambda: 1)`; `DrawdownBreaker(caps, clock=lambda: 0)`; a multi-call clock fixture `_clock(*times)` returns `lambda: next(iter(times))`.
- **`tmp_path`** for every DB-backed test; DBs are `str(tmp_path / "i.db")`. Stores opened as context managers (`with _store(...) as store:`).
- **Fakes are hand-written local classes** (prefixed `_`), not Mock. `monkeypatch.setattr(module, "name", fn, raising=True)` is used to swap module-level collaborators (the function-local-import sites `fusion.engine.fuse`, `truthgate.gate.verify`).

## (b) Fixture cookbook (copy-pasteable)

These are the exact helpers/constructors used by the existing ERS tests.

**Imports block (the ERS-service test set):**
```python
from decimal import Decimal
from polybot.core.clock import MonotonicStamper
from polybot.ers.breaker import DrawdownBreaker, FLATTEN, FREEZE_ADDS, NONE
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import ClusterView, OpenPosition, Portfolio, Decision, TradeIntent
from polybot.ingestion.orderbook import LocalBook
```

**LocalBook (the live book the ERS re-prices off) — the canonical fixture:**
```python
def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}],
                     "asks": [{"price": ask, "size": size}]})
    return book
# book.mark_stale() -> forces is_stale()==True; midpoint()/best_ask() then refuse.
# midpoint of bid 0.01 / ask 0.50 == Decimal("0.255").
# book_for is ALWAYS a dict.get callable: book_for={"t1": _book("0.50")}.get
# a missing token -> None -> fail-closed "no_book".
```

**RiskCaps — default-constructed; override single fields by kwarg:**
```python
caps = RiskCaps()                       # the S0 envelope; ALL tests use defaults unless probing _verify
RiskCaps(daily_pending_ceiling=Decimal("10"))   # raises ValueError (ordering) -- override to trip an invariant
# When you raise total_open you MUST also fix reserve_floor in the SAME call (it's a verified invariant):
RiskCaps(total_open_risk=Decimal("90"), reserve_floor=Decimal("210"))  # raises (20% NAV) -- but reserve stays consistent
RiskCaps(nav=Decimal("600"), total_open_risk=Decimal("120"),
         reserve_floor=Decimal("480"), daily_pending_ceiling=Decimal("48"))  # a LARGER consistent envelope
```

**IntentStore (chokepoint store) + a PROPOSED intent:**
```python
def _store(path):
    return IntentStore(path, MonotonicStamper())

_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())

with _store(str(tmp_path / "i.db")) as store:
    store.propose_trade("i1", **_P)             # keyword-only after intent_id; numeric fields are STRINGS
    store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="mb", event_id="eb"))  # override pattern
# record_decision(intent_id, Decision(...)) is ERS-only; pending() returns FIFO PROPOSED rows;
# get(intent_id) returns the PendingIntent; audit_log() returns list[dict].
```

**process_pending — full ACCEPT-path call (the cookbook line):**
```python
signer = PaperSigner()
final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)
assert store.get("i1").status == "ACCEPTED"
assert store.get("i1").decision_stake_usd == Decimal("12")   # per_trade cap binds
assert [o["token_id"] for o in signer.placed] == ["t1"]
assert final.positions[0].worst_case_risk == Decimal("12")
```

**PaperSigner — the shadow signer seam:**
```python
signer = PaperSigner()
# signer.placed     -> list[dict]: {"intent_id","token_id","stake_usd","price_exec"}, appended on .place(intent, decision)
# signer.flattened  -> list[tuple]: tuple(p.token_id for p in positions), appended on .flatten(positions)
# Both start as []. The S4 supervisor's signer_B is "a distinct PaperSigner behind the Signer Protocol"
# -> tests will assert `signer_B is not signer_A` and that cancel_all/flatten land on signer_B.
```

**DrawdownBreaker (L7) — clock-injected, wired into process_pending:**
```python
process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps, signer=signer,
                breaker=DrawdownBreaker(caps, clock=lambda: 0))
# Multi-cycle velocity tests use a stepping clock:  _clock(0, 100) -> lambda: next(iter(...))
# constants for precedence: NONE / FREEZE_ADDS / FLATTEN  (FLATTEN > FREEZE_ADDS > NONE)
# block_reason short-circuit in process_pending: FLATTEN -> signer.flatten + block_reason="l7_flatten";
#   FREEZE_ADDS -> block_reason="l7_freeze"; every pending intent then -> Decision("REJECT",None,None,block_reason).
```

**Portfolio / OpenPosition / Decision / ClusterView constructors:**
```python
Portfolio(nav=Decimal("300"))                              # empty positions=()
Portfolio(nav=Decimal("300"), positions=(pos1, pos2))      # positions is a TUPLE

# OpenPosition positional order: condition_id, event_id, resolution_source, cluster_id, worst_case_risk,
#   matrix_cold(default True); THEN keyword L7-mark fields token_id="", entry_price=Decimal(0), frozen=False.
OpenPosition("mz", "ez", "sz", "cz", Decimal("50"), False)                      # pre-L7 short form
OpenPosition("m", "e", "s", "c", Decimal("18"), False, token_id="A",
             entry_price=Decimal("0.50"), frozen=False)                          # L7-markable form
# breaker-test helper:
def _pos(token, entry, risk, *, frozen=False):
    return OpenPosition("m", "e", "s", "c", Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry), frozen=frozen)

Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly")   # verdict, stake_usd, price_exec, reason
Decision("REJECT", None, None, "book_stale")                 # stake/price None on non-accept

ClusterView(warm=False, rho=None)                            # cold (default path)
ClusterView(warm=True, rho=Decimal("1"))                     # warm -> earns the dollar cluster cap
```

**MonotonicStamper:**
```python
MonotonicStamper()                       # production: time.monotonic_ns
MonotonicStamper(clock=lambda: 1000)     # frozen clock -> every stamp() goes down the +1 strict-mono path
MonotonicStamper(clock=lambda: next(iter([1000,2000,3000])))   # advancing clock tracks underlying
# stamp() is thread-safe (internal Lock); the ONE shared instance is passed to IntentStore/ForecastLedger/ComponentLog.
```

## (c) Verbatim signatures + field lists

### `src/polybot/ers/caps.py` — `RiskCaps`
`@dataclass(frozen=True)`. **Full field list with defaults** (all `Decimal` unless noted):
```
nav=Decimal("300"); total_open_risk=Decimal("60"); reserve_floor=Decimal("240")
per_trade=Decimal("12"); per_market=Decimal("18"); per_event_union=Decimal("24")
per_negrisk_event=Decimal("18"); per_source_open=Decimal("30"); per_source_locked_effective=Decimal("18")
max_locked_to_resolution=Decimal("36")
max_concurrent: int = 4; matrix_cold_concurrent: int = 3
daily_pending_ceiling=Decimal("24"); kelly_fraction=Decimal("0.25"); min_position_floor=Decimal("5")
liquidity_depth_frac=Decimal("0.10"); liquidity_impact_cents=Decimal("1")
l7_freeze_floor=Decimal("18"); l7_flatten_floor=Decimal("30"); l7_velocity_delta=Decimal("18")
l7_velocity_window_seconds: int = 900
```
`__post_init__(self)` calls `self._verify()`. **Ordering invariants `_verify` already enforces** (S4 extends this additively — your new fields like `daily_pending_ceiling`-wiring, `weekly_loss_halt`, `dead_man_switch_timeout`, `reconcile_tolerance` add MORE clauses here, and you must keep ALL existing ones consistent in any override):
1. `per_trade < daily_pending_ceiling < total_open_risk` (breaker ordering)
2. `max_concurrent * per_trade <= total_open_risk` (no zero-slack concurrency)
3. `0 < matrix_cold_concurrent <= max_concurrent`
4. `reserve_floor == nav - total_open_risk` (one capital band)
5. `total_open_risk <= 0.20 * nav` (20%-NAV at-risk ceiling)
6. `0 < kelly_fraction <= 0.5`
7. `min_position_floor >= 5`
8. a list of strictly-positive fields (`positives` tuple) all `> 0`; `max_concurrent > 0`
9. `0 < l7_freeze_floor < l7_flatten_floor <= total_open_risk` (L7 ordering)
10. `l7_velocity_delta > 0`; `l7_velocity_window_seconds > 0`

`content_hash(self)`: SHA-256 over `json.dumps({k: str(v) for k,v in asdict(self).items()}, sort_keys=True)` → hexdigest. Deterministic; different consistent envelopes hash differently. `cluster_cap(self, rho)` → `per_trade + (1-rho)*(total_open_risk - per_trade)` clamped to `[per_trade, total_open_risk]`.

### `src/polybot/ers/service.py`
**`process_pending` full signature:**
```python
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None):
```
Returns the updated `Portfolio`. `book_for` is a callable `token_id -> book|None`. The **block_reason short-circuit** (the lines S4 mirrors for op-FLATTEN; in `service.py`):
```python
    block_reason = None
    if breaker is not None:
        state = breaker.evaluate(portfolio.positions, book_for)
        if state.action == FLATTEN:
            signer.flatten(portfolio.positions)
            block_reason = "l7_flatten"
        elif state.action == FREEZE_ADDS:
            block_reason = "l7_freeze"

    for intent in store.pending():
        trade_intent = None
        try:
            if block_reason is not None:
                decision = Decision("REJECT", None, None, block_reason)
            elif pipeline is None:
                decision, trade_intent = _process_intent_slice3(...)
            else:
                decision, trade_intent = _process_intent_pipeline(...)
        except Exception:
            decision = Decision("REJECT", None, None, "internal_error")
            trade_intent = None
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision)
    return portfolio
```
Note for S4 precedence probing: the L7 `FLATTEN` here is drawdown-driven and short-circuits as `"l7_flatten"`; the S4 design explicitly flags op/L5/L6-FLATTEN vs L7-FLATTEN as two distinct concepts whose precedence must stay unambiguous.

**`_fold(portfolio, trade_intent, decision)`** builds an `OpenPosition(condition_id, event_id, resolution_source, cluster_id, worst_case_risk=decision.stake_usd, matrix_cold=trade_intent.matrix_cold, token_id, entry_price=decision.price_exec, frozen=False)` and returns `Portfolio(nav=portfolio.nav, positions=portfolio.positions + (pos,))`.

**`_cluster_view(cluster_model, intent, portfolio, *, cluster_id_of=None)`** — `None` model → `_COLD = ClusterView(warm=False, rho=None)`; else spans `intent.token_id` + every open position whose `cluster_id == cluster_id_of(intent)` (default `lambda i: i.event_id`) and calls `cluster_model.view(tokens)`.

**`PaperSigner`** verbatim:
```python
class PaperSigner:
    def __init__(self):
        self.placed = []
        self.flattened = []
    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})
    def flatten(self, positions):
        self.flattened.append(tuple(p.token_id for p in positions))
```
(No `cancel_all`/`gtd_exits` yet — S4 adds those to the signer seam. The design names `signer_A.gtd_exits` and `signer_B.cancel_all()`/`signer_B.flatten(open_positions)`.)

`HermesPipeline` is a `@dataclass(frozen=True)` with fields: `calib_gate, fusion_config, truth_gate_config, detectors, forecast_ledger, component_log, market_meta, allowlist, event_store, stamper` (all `object`-typed seams). `pipeline=None` ⇒ behaviour is exactly slice-3.

### `src/polybot/ers/breaker.py`
Constants: `NONE = "NONE"`, `FREEZE_ADDS = "FREEZE_ADDS"`, `FLATTEN = "FLATTEN"`.
```python
@dataclass(frozen=True)
class BreakerState:
    action: str        # NONE | FREEZE_ADDS | FLATTEN
    drawdown: Decimal
    triggers: tuple    # subset of: freeze_floor / flatten_floor / velocity / position_loss / stale_mark

class DrawdownBreaker:
    def __init__(self, caps, *, clock): ...      # clock() -> monotonic SECONDS
    def evaluate(self, positions, book_for): ... # -> BreakerState; called once per cycle
```
Precedence inside `evaluate`: FLATTEN (drawdown > `l7_flatten_floor`) supersedes FREEZE_ADDS (drawdown > `l7_freeze_floor`, OR any trigger present) supersedes NONE. Stale/velocity/position_loss alone only ever FREEZE (never FLATTEN blind). Marks to MID; frozen positions excluded; `entry_price <= 0` or `book_for(token) is None` or `midpoint() is None` ⇒ un-markable ⇒ `stale` ⇒ freeze.

### `src/polybot/ers/intent_store.py`
```python
class IntentStore:
    def __init__(self, path, stamper): ...   # opens sqlite3 at path; PRAGMA journal_mode=WAL, synchronous=NORMAL
    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()): ...  # INSERT OR IGNORE; True if new
    def record_decision(self, intent_id, decision): ...  # UPDATE status + INSERT audit row; ERS-only
    def pending(self): ...    # SELECT ... WHERE status='PROPOSED' ORDER BY rowid  (FIFO) -> list[PendingIntent]
    def get(self, intent_id): ...
    def audit_log(self): ...  # SELECT ... FROM intent_audit ORDER BY audit_id -> list[dict]
    def close(self); __enter__; __exit__   # context-manager
```
`_STATUS_FOR_VERDICT = {"ACCEPT":"ACCEPTED","REJECT":"REJECTED","SKIP":"SKIPPED"}`. Numeric columns are stored as exact STRINGS.

**EXACT existing table-creation SQL pattern to MIRROR for the new S4 op/kill/heartbeat + fill/exposure tables** (the design says: "`AUTOINCREMENT` + shared `stamper.stamp()`, mirroring `intent_audit`"):
```sql
CREATE TABLE IF NOT EXISTS pending_intents (
    intent_id           TEXT PRIMARY KEY,
    status              TEXT    NOT NULL,
    token_id            TEXT    NOT NULL,
    condition_id        TEXT    NOT NULL,
    event_id            TEXT    NOT NULL,
    side                TEXT    NOT NULL,
    target_price        TEXT    NOT NULL,
    max_price           TEXT    NOT NULL,
    size_usd_suggestion TEXT    NOT NULL,
    p                   TEXT    NOT NULL,
    p_confidence        TEXT    NOT NULL,
    resolution_summary  TEXT    NOT NULL,
    thesis              TEXT    NOT NULL,
    citations           TEXT    NOT NULL,
    created_at          INTEGER NOT NULL,
    decided_at          INTEGER,
    decision_verdict    TEXT,
    decision_stake_usd  TEXT,
    decision_price_exec TEXT,
    decision_reason     TEXT
)
-- THE append-only audit table the new S4 tables must mirror exactly:
CREATE TABLE IF NOT EXISTS intent_audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id  TEXT    NOT NULL,
    at         INTEGER NOT NULL,
    verdict    TEXT    NOT NULL,
    stake_usd  TEXT,
    price_exec TEXT,
    reason     TEXT    NOT NULL
)
```
Insert pattern (commits per write): `INSERT INTO intent_audit (intent_id, at, verdict, stake_usd, price_exec, reason) VALUES (?, ?, ?, ?, ?, ?)` with `at = self._stamper.stamp()`. Tests build a store via `IntentStore(str(tmp_path / "i.db"), MonotonicStamper())` and verify restart persistence by closing and reopening the same path.

`PendingIntent` (`@dataclass(frozen=True)`) fields: `intent_id, status, token_id, condition_id, event_id, side, target_price:Decimal, max_price:Decimal, size_usd_suggestion:Decimal, p:Decimal, p_confidence:Decimal, resolution_summary, thesis, citations:tuple, created_at:int, decided_at:int|None=None, decision_verdict:str|None=None, decision_stake_usd:Decimal|None=None, decision_price_exec:Decimal|None=None, decision_reason:str|None=None`.

### `src/polybot/ers/validator.py` — dataclass field lists (all `@dataclass(frozen=True)`)
```python
TradeIntent:  token_id, condition_id, event_id, resolution_source, cluster_id,
              p:Decimal, max_price:Decimal, size_usd_suggestion:Decimal, matrix_cold:bool=True
OpenPosition: condition_id, event_id, resolution_source, cluster_id, worst_case_risk:Decimal,
              matrix_cold:bool=True, token_id:str="", entry_price:Decimal=Decimal(0), frozen:bool=False
Portfolio:    nav:Decimal, positions:tuple=()
              # methods: total_open_risk(), market_risk(cid), event_risk(eid), source_risk(src),
              #          cluster_risk(cid), matrix_cold_count()
Decision:     verdict:str, stake_usd:Decimal|None, price_exec:Decimal|None, reason:str
ClusterView:  warm:bool, rho:Decimal|None=None
```
`evaluate_intent(intent, book, portfolio, caps, *, calib_score=Decimal(1), cluster=_COLD_CLUSTER) -> Decision`. **Per the S4 design, `evaluate_intent`/the validator dataclasses are UNCHANGED** by S4 (additive seams only).

### `src/polybot/core/clock.py` — `MonotonicStamper`
```python
class MonotonicStamper:
    def __init__(self, clock=None):   # clock defaults to time.monotonic_ns
        self._clock = clock or time.monotonic_ns
        self._last = 0
        self._lock = threading.Lock()
    def stamp(self):                  # strictly-increasing ns; thread-safe; +1 if clock didn't advance
```

### `src/polybot/ers/facade.py` + `tests/test_ers_facade.py` — the structural-sweep style for S4.1 to match
`ProposeOnlyFacade(store, *, market_reader=None, book_reader=None, ledger_reader=None, flags_reader=None)`. Public surface is EXACTLY `{propose_trade, get, audit_log, get_market, get_book, get_ledger, get_flags}`; store held name-mangled as `_ProposeOnlyFacade__store`. The load-bearing sweep test (`test_structural_sweep_no_signer_or_status_path`) is the template S4.1 extends — copy its exact shape:
```python
allowed = {"propose_trade","get","audit_log","get_market","get_book","get_ledger","get_flags"}
public = {name for name in dir(facade) if not name.startswith("_")}
assert public == allowed, f"unexpected public surface: {public ^ allowed}"
for name in ("place","flatten","record_decision","pending","signer","store","sign","submit","cancel"):
    assert not hasattr(facade, name)
    assert name not in dir(facade)
    assert not hasattr(facade, "_" + name)          # also catches single-underscore leaks
assert not callable(facade)                          # no __call__ dispatch path
assert not isinstance(facade, IntentStore) and IntentStore not in type(facade).__mro__
assert not hasattr(facade, "_store")
assert getattr(facade, "_ProposeOnlyFacade__store", None) is store
assert "status" not in inspect.signature(facade.propose_trade).parameters
```
Conventions to match in S4.1: `inspect.signature(...).parameters` for the no-`status`/no-dispose assertions; `with pytest.raises(TypeError):` (import pytest locally) for "fail-loud without a wired reader"; module-level `_PROPOSAL` dict + `_store(tmp_path)` helper.

## (d) Subprocess-test verdict + recommended harness

**Verdict: the S4.3 wedged-process gate is NET-NEW.** No source file (`ers/heartbeat.py`, `ers/supervisor.py`, `ers/startup_selftest.py`) and no test (`tests/test_ers_supervisor_kill.py`) exist yet — they are only NAMED in `docs/DESIGN-S4-SAFETY.md` (lines 176-185, 280-298, 355-365). A repo-wide grep for `multiprocessing | subprocess | os.fork | Process( | Popen | spawn | fork( | concurrent.futures | ProcessPool` finds **zero** matches in `src/` or `tests/` — the only hits are prose in `docs/` (DECISIONS-S0, DESIGN-S4, VERIFICATION). **No existing test spawns a child process.** All existing concurrency in tests is:
- `threading` — only `test_clock.py` (8-thread Barrier stress on the stamper), `test_market_memory.py`, `test_event_writer.py` (`threading.Event` for blocked-append coordination).
- `asyncio.run(...)` — the ingestion/WS/news/polygon tests drive single-loop coroutines.

So there is **no subprocess test harness to copy**; S4.3 establishes the pattern. The design itself (open-risks §9) pre-flags the exact hazards: heartbeat write/read ordering races, OS-specific `kill` semantics under WSL, asserting on the supervisor's OWN signer (`signer_B is not signer_A`), and determinism via an injected clock for the timeout.

**WSL/pytest constraints:**
- Runs under `wsl bash` → real Linux; `multiprocessing` default start method is **`fork`** (cheap, inherits the parent's `tmp_path`/imports — no pickling-the-target headache). Use it; do not set `spawn`.
- The pure-unit `OutOfBandSupervisor.decide(heartbeat_age, now)` and `Heartbeat.last_beat_age/is_alive` tests must be clock-injected and have **no real sleep** (mirror `DrawdownBreaker(caps, clock=lambda: 0)` and `MonotonicStamper(clock=lambda: 1000)`). Only the one end-to-end acceptance test uses a real-but-bounded sleep.
- Hard-kill with `os.kill(child.pid, signal.SIGKILL)` (SIGKILL is reliable on Linux/WSL; SIGTERM could be swallowed by a genuinely wedged interpreter — the whole point is fate-isolation). Always `child.join(timeout=...)` and assert `child.exitcode is not None` / kill in test teardown so a hung child can't wedge the suite.

**Recommended harness shape** (simplest robust approach, matching the design's "file Heartbeat + out-of-band supervisor"):

*Pure unit (fast, deterministic — the bulk of coverage):*
```python
# Heartbeat staleness with an injected clock + a real file in tmp_path (out-of-process read).
hb = Heartbeat(str(tmp_path / "hb"))
hb.beat()                                  # writes mtime/counter
assert hb.is_alive(now=hb_now, timeout=...)        # fresh
assert not hb.is_alive(now=hb_now + stale, timeout=...)
# Supervisor decision unit -- NO process, NO sleep:
sup = OutOfBandSupervisor(caps, signer_B, clock=lambda: T)
assert sup.decide(heartbeat_age=0, now=T) == "OK"
assert sup.decide(heartbeat_age=caps.dead_man_switch_timeout + 1, now=T) == "FLATTEN_AND_KILL"
```

*Integration acceptance (`tests/test_ers_supervisor_kill.py`) — one real child, file heartbeat, bounded join:*
```python
import multiprocessing as mp, os, signal, time

def _wedged_child(hb_path, ready_path):
    hb = Heartbeat(hb_path)
    hb.beat()                  # one good beat so a GTD bracket is "staged", then go silent
    open(ready_path, "w").close()
    while True:                # genuinely wedged: never beat again
        time.sleep(3600)

def test_supervisor_hard_kills_wedged_child_and_flattens_on_signer_b(tmp_path):
    hb_path = str(tmp_path / "hb"); ready = str(tmp_path / "ready")
    child = mp.Process(target=_wedged_child, args=(hb_path, ready))
    child.start()
    try:
        # wait for the child to have written its single heartbeat (bounded poll, no blind sleep)
        deadline = time.monotonic() + 5
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.01)
        hb = Heartbeat(hb_path)
        # real-but-bounded wait so the file heartbeat is genuinely stale past the timeout
        time.sleep(SHORT_TIMEOUT + 0.05)
        signer_A, signer_B = PaperSigner(), PaperSigner()      # signer_B is the supervisor's OWN
        sup = OutOfBandSupervisor(caps, signer_B, clock=time.monotonic)
        assert not hb.is_alive(now=time.monotonic(), timeout=SHORT_TIMEOUT)
        sup.kill_and_flatten(child.pid, open_positions)        # os.kill(pid, SIGKILL) + signer_B.cancel_all/flatten
        child.join(timeout=5)
        assert child.exitcode is not None                      # the child really died
        assert signer_B.flattened and signer_B is not signer_A # fate-isolated on the supervisor's signer
        # pre-staged GTD exit brackets on signer_A survive the wedge (passive backstop)
    finally:
        if child.is_alive():
            os.kill(child.pid, signal.SIGKILL); child.join(5)
```
Key fidelity points (straight from design §9): poll a `ready` file rather than a blind sleep for child startup (kills the write/read race); use a deterministic injected clock for the `decide` UNIT and only a small real sleep for the integration timeout; assert `child.exitcode is not None` to prove the kill landed; assert the flatten happened on `signer_B` (the supervisor's own, `is not signer_A`); always SIGKILL-and-join in `finally`. This runs entirely on `PaperSigner` now — only the live `cancelAll`/GTD proof is POL-4-deferred.

**Key relevant files:** `C:` is the Windows mount; the live tree is `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\` — `src\polybot\ers\{caps,service,breaker,intent_store,validator,facade}.py`, `src\polybot\core\clock.py`, `src\polybot\ingestion\orderbook.py`, `tests\test_ers_{service,caps,breaker,intent_store,validator,facade}.py`, `tests\test_clock.py`, and the build spec `docs\DESIGN-S4-SAFETY.md` (§S4.3 lines 176-185, acceptance gate §5 lines 280-298, open-risks §9 lines 355-365).