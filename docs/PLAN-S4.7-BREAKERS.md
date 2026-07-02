# S4.7 Realized-Loss Breakers + Flow Gate + Ramp Ratchet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build S4.7 / POL-6: the durable dual-stamped `flow_journal`, the per-cycle flow gate (rate caps + daily pending ceiling) in `SafetyController.verdict`'s RUNNING branch, the realized-loss breakers (weekly sticky HALT + one-shot cancel_all; consecutive/pending sticky PAUSE), and the tighten-only ramp-DOWN ratchet (`swap_caps` + the two operator-signed steps + the `active_caps()` re-plumb) — all shadow-only on the `PaperSigner`.

**Architecture:** NEW `src/polybot/ers/flow.py`, `ers/ramp.py`, `ers/lossbreaker.py` + additive extensions: one new append-only IntentStore table, `SafetyController.wire_flow_gate`/`swap_caps`, `ERSController(lossbreakers=)` + the caps re-plumb. The authoritative spec is `docs/DESIGN-S4.7-BREAKERS.md` (§4 = the pinned contract); this plan implements exactly that. **No new RiskCaps fields.**

**Tech Stack:** Python 3.13, pytest, stdlib only (Decimal/dataclasses) — no new dependencies.

---

## Execution notes (READ FIRST — every implementer)

- **Environment:** repo is WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot`, branch `pol-6-s4.7-breakers` (already checked out). Run tests/git from Windows via `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`; edit files via UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...` (EISDIR = 9p glitch, retry). Tests: `./.venv/bin/pytest -o addopts="" -q` — baseline **660 passing** before Task A1.
- **Strict TDD:** run each Step 2 and OBSERVE the RED (fail for the stated reason) before writing Step 3. One commit per RED→GREEN cycle. **Commit messages: single `-m`, NO Co-Authored-By trailer.** If you mutation-check anything, revert with `git checkout` AND sweep pycache (`find src -name __pycache__ -exec rm -rf {} +`) — a stale .pyc once masqueraded as a source regression.
- **SACRED — never touch:** `validator.py`/`evaluate_intent`, `propose_trade`/`record_decision`/`record_op_event` code in `intent_store.py` (ADDING the new table+methods is in-scope; the `record_op_event` DOCSTRING kind-set may grow), `process_pending`'s signature + decision flow in `service.py`, `core/clock.py`, `heartbeat.py`, `supervisor.py`, `breaker.py`, `anomaly.py`, `gtd.py`, `reconcile.py`.
- **Sub-slices run SERIALLY A → B → C → D** on the shared branch. Fragments were drafted against the pinned contract in `docs/DESIGN-S4.7-BREAKERS.md` §4: when a Step-3 code block shows surrounding code that has since evolved (`safety.py`/`controller.py` grow across sub-slices), reconcile against the CURRENT file and NEVER delete an earlier sub-slice's additions. If reconciliation is ambiguous, STOP and report NEEDS_CONTEXT rather than improvise.
- **Suite counts are per-sub-slice estimates.** Authoritative verification: the named new tests pass, the FULL suite is all green (exit 0), no test deleted/skipped. If an absolute count differs but everything is green, proceed and note it.
- **Fail-closed doctrine:** under ambiguity the correct behavior is DO NOT TRADE + surface the condition. Re-read `docs/DESIGN-S4.7-BREAKERS.md` §3/§6 (including the rows-70-vs-72 interplay note) before asking.

---

## Sub-slice S4.7a: The journal (flow_journal + recorder + compose_sinks + window helpers)

Implements DESIGN-S4.7-BREAKERS.md Â§4's `flow_journal` table (`record_flow_event`/`flow_log` in `intent_store.py` â€” additive; `propose_trade`/`record_decision`/`record_op_event` byte-untouched) and the NEW module `src/polybot/ers/flow.py` with `make_flow_recorder`, `compose_sinks`, `accepts_in_window`, `pending_in_window` (`make_flow_gate` is S4.7c â€” NOT built here). All tests in NEW `tests/test_ers_flow_journal.py`. No other file is touched. Test-count ladder: 660 â†’ A1 663 â†’ A2 664 â†’ A3 666 â†’ A4 668 â†’ A5 671 â†’ A6 673 â†’ A7 674.

---

### Task A1: flow_journal table + record_flow_event/flow_log (round-trip, dual-stamp, durability)

**Files:**
- Modify: `src/polybot/ers/intent_store.py` (DDL: insert after the `fills` CREATE TABLE block ending at line 125, before the `self._conn.commit()` at line 126; methods: insert after `fills_log` ending at line 231, before `def close` at line 233)
- Test: `tests/test_ers_flow_journal.py` (NEW)

- [ ] **Step 1: Write the failing test** â€” create `tests/test_ers_flow_journal.py`:

```python
"""Tests for the S4.7a flow journal (DESIGN-S4.7-BREAKERS.md SS4/SS9 sub-slice a).

The durable dual-stamped flow_journal (monotonic ``at`` for cross-table ordering + injected
wall-clock ``wall_at`` for restart-surviving windows) + the fill_sink-shaped accept recorder +
compose_sinks fan-out + the pure rolling-window helpers (accepts_in_window / pending_in_window).
Window math uses wall_at ONLY -- stored monotonic stamps are not comparable across restarts
(the S4.5 lesson, re-pinned by DESIGN SS6.8).
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_flow_journal_round_trips_decimal_amount_float_wall_at_in_flow_id_order(tmp_path):
    # Kills: storing amount as float / returning str (Decimal reconstruction dropped);
    # Kills: ORDER BY wall_at (or at) instead of flow_id -- the wall stamps here are DESCENDING.
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=200.0)
        store.record_flow_event(kind="realized", token_id="t2", amount=Decimal("-3.50"), wall_at=100.0)
        rows = store.flow_log()
        assert [(r["kind"], r["token_id"], r["amount"]) for r in rows] == [
            ("accept", "t1", Decimal("12")), ("realized", "t2", Decimal("-3.50"))]
        assert all(isinstance(r["amount"], Decimal) for r in rows)
        assert [r["wall_at"] for r in rows] == [200.0, 100.0]  # insertion order, NOT wall order
        assert all(isinstance(r["wall_at"], float) for r in rows)


def test_flow_journal_at_comes_from_the_one_shared_monotonic_stamper(tmp_path):
    # Kills: a per-table clock or wall_at reuse for ``at`` -- a flow row's ``at`` must interleave
    # with op_audit's on the ONE shared stamper (total ordering across every table).
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=1.0)
        store.record_op_event(kind="state_change", reason="r", detail="d")
        store.record_flow_event(kind="realized", token_id="t1", amount=Decimal("-2"), wall_at=2.0)
        first_at, second_at = [r["at"] for r in store.flow_log()]
        op_at = store.op_audit_log()[0]["at"]
        assert isinstance(first_at, int) and isinstance(second_at, int)
        assert first_at < op_at < second_at


def test_flow_journal_survives_close_and_reopen(tmp_path):
    # Kills: an in-memory journal / a missing per-write commit -- restart-surviving windows
    # (DESIGN SS2 durability) require the row to be durable across close-and-reopen.
    path = str(tmp_path / "i.db")
    with _store(path) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=500.0)
    with _store(path) as reopened:
        rows = reopened.flow_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "accept" and rows[0]["token_id"] == "t1"
        assert rows[0]["amount"] == Decimal("12") and rows[0]["wall_at"] == 500.0
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: **3 failed**, each with `AttributeError: 'IntentStore' object has no attribute 'record_flow_event'`.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/intent_store.py`, insert the DDL between the `fills` CREATE TABLE `execute(...)` (ends line 125) and `self._conn.commit()` (line 126):

```python
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flow_journal (
                flow_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                at       INTEGER NOT NULL,
                wall_at  REAL    NOT NULL,
                kind     TEXT    NOT NULL,
                token_id TEXT    NOT NULL,
                amount   TEXT    NOT NULL
            )
            """
        )
```

and insert the two methods after `fills_log` (line 231), before `def close` (line 233):

```python
    def record_flow_event(self, *, kind, token_id, amount, wall_at):
        """Append an IMMUTABLE flow-journal row (S4.7): ``kind`` in {accept, realized}.
        Dual-stamped: ``at`` = the shared monotonic stamp (cross-table ordering; NOT
        cross-restart comparable), ``wall_at`` = the caller-supplied wall clock in epoch
        seconds (windowing; the ONLY cross-restart-comparable time in the store). ``amount``
        is stored as an exact string: accept => worst_case_risk (+); realized => signed
        PnL (+win / -loss). Commit per write, mirroring record_op_event."""
        self._conn.execute(
            "INSERT INTO flow_journal (at, wall_at, kind, token_id, amount) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._stamper.stamp(), wall_at, kind, token_id, str(amount)),
        )
        self._conn.commit()

    def flow_log(self):
        rows = self._conn.execute(
            "SELECT at, wall_at, kind, token_id, amount FROM flow_journal ORDER BY flow_id"
        ).fetchall()
        return [{"at": r[0], "wall_at": r[1], "kind": r[2], "token_id": r[3],
                 "amount": Decimal(r[4])} for r in rows]
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 3 passed; full suite **663 passed** (660 baseline + 3).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/intent_store.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: flow_journal table + record_flow_event/flow_log (dual-stamped, durable)"'
```

---

### Task A2: make_flow_recorder (the fill_sink-shaped accept recorder; creates ers/flow.py)

**Files:**
- Create: `src/polybot/ers/flow.py`
- Test: `tests/test_ers_flow_journal.py` (append)

- [ ] **Step 1: Write the failing test** â€” append to `tests/test_ers_flow_journal.py`:

```python
# --- S4.7a: make_flow_recorder (ers/flow.py -- the fill_sink-shaped accept recorder) ----------
from polybot.ers.flow import make_flow_recorder
from polybot.ers.validator import OpenPosition


def test_make_flow_recorder_records_accept_with_worst_case_risk_and_injected_wall_clock(tmp_path):
    # Kills: recording the wrong kind / sourcing amount from anything but position.worst_case_risk;
    # Kills: calling time.time() instead of the injected 0-arg wall_clock (wall_at must be 777.5).
    with _store(str(tmp_path / "i.db")) as store:
        recorder = make_flow_recorder(store, wall_clock=lambda: 777.5)
        position = OpenPosition(condition_id="m1", event_id="e1", resolution_source="s1",
                                cluster_id="c1", worst_case_risk=Decimal("8"), matrix_cold=False,
                                token_id="t9", entry_price=Decimal("0.50"), frozen=False)
        recorder(None, None, position)  # intent/decision unused: the recorder reads ONLY the position
        rows = store.flow_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "accept" and rows[0]["token_id"] == "t9"
        assert rows[0]["amount"] == Decimal("8") and rows[0]["wall_at"] == 777.5
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: collection **ERROR** on the module â€” `ModuleNotFoundError: No module named 'polybot.ers.flow'`.

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/flow.py` (NOTE: the module source must never contain the strings `set_state` or the resume-state name â€” Task A7 pins this):

```python
"""Flow-journal recorder + rolling-window helpers (S4.7a / POL-6, DESIGN-S4.7-BREAKERS.md SS4).

The flow_journal counts NEW-POSITION flow (kind="accept", amount = the folded position's
worst_case_risk) and realized outcomes (kind="realized", signed PnL: +win / -loss) so the
S4.7 rate caps, daily pending ceiling, and loss breakers survive restarts. Window math uses
the caller-supplied wall clock (``wall_at``, epoch seconds) -- NEVER the monotonic ``at``
stamp, which is not comparable across restarts. Wins never offset pending (conservative).
A malformed row in our own journal is corruption, never skipped: the window helpers RAISE
and each consumer converts the raise into its fail-closed action.
"""

from decimal import Decimal


def make_flow_recorder(store, *, wall_clock):
    """Return a fill_sink-shaped callable ``(intent, decision, position)`` appending one
    kind="accept" flow row per ACCEPT: amount = the folded position's worst_case_risk,
    wall_at = wall_clock() (epoch seconds; time.time in the live assembly, injected in tests)."""
    def _rec(intent, decision, position):
        store.record_flow_event(kind="accept", token_id=position.token_id,
                                amount=position.worst_case_risk, wall_at=wall_clock())
    return _rec
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 4 passed; full suite **664 passed**.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: make_flow_recorder - accept flow rows via the fill_sink shape"'
```

---

### Task A3: compose_sinks (fan-out order + the fills+flow e2e through process_pending)

**Files:**
- Modify: `src/polybot/ers/flow.py` (append after `make_flow_recorder`)
- Test: `tests/test_ers_flow_journal.py` (append)

- [ ] **Step 1: Write the failing tests** â€” append to `tests/test_ers_flow_journal.py`:

```python
# --- S4.7a: compose_sinks (one fill_sink fanning out to many; NO service.py change) -----------
from polybot.ers.caps import RiskCaps
from polybot.ers.flow import compose_sinks
from polybot.ers.service import PaperSigner, make_fill_sink, process_pending
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def test_compose_sinks_calls_each_sink_exactly_once_in_order_with_the_same_args():
    # Kills: reversed fan-out order; a sink invoked twice or skipped; args not threaded through.
    calls = []

    def _first_sink(intent, decision, position):
        calls.append(("first", intent, decision, position))

    def _second_sink(intent, decision, position):
        calls.append(("second", intent, decision, position))

    composed = compose_sinks(_first_sink, _second_sink)
    composed("I", "D", "P")
    assert calls == [("first", "I", "D", "P"), ("second", "I", "D", "P")]


def test_composed_sink_writes_both_a_fills_row_and_a_flow_row_on_an_accept(tmp_path):
    # Kills: the composite not being fill_sink-shaped end-to-end -- one ACCEPT through the
    # UNCHANGED process_pending fill_sink seam must land BOTH durable legs: the S4.5 fill
    # AND the S4.7 accept-flow row (amount == the folded worst_case_risk == stake $12).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        sink = compose_sinks(make_fill_sink(store),
                             make_flow_recorder(store, wall_clock=lambda: 1000.0))
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, fill_sink=sink)
        assert store.get("i1").status == "ACCEPTED"
        fills = store.fills_log()
        assert len(fills) == 1 and fills[0]["worst_case_risk"] == Decimal("12")
        flow = store.flow_log()
        assert len(flow) == 1
        assert flow[0]["kind"] == "accept" and flow[0]["token_id"] == "t1"
        assert flow[0]["amount"] == Decimal("12") and flow[0]["wall_at"] == 1000.0
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: collection **ERROR** â€” `ImportError: cannot import name 'compose_sinks' from 'polybot.ers.flow'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/flow.py`:

```python
def compose_sinks(*sinks):
    """Return ONE fill_sink fanning out to many: each sink is called exactly once per ACCEPT,
    in the given order, with the same ``(intent, decision, position)``. No service.py change --
    the composite plugs into process_pending's existing ``fill_sink=`` seam (fills + flow)."""
    def _sink(intent, decision, position):
        for sink in sinks:
            sink(intent, decision, position)
    return _sink
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 6 passed; full suite **666 passed**.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: compose_sinks fan-out + fills-plus-flow e2e through the fill_sink seam"'
```

---

### Task A4: accepts_in_window (inclusive-old-edge boundary pair + kind filter)

**Files:**
- Modify: `src/polybot/ers/flow.py` (append after `compose_sinks`)
- Test: `tests/test_ers_flow_journal.py` (append)

- [ ] **Step 1: Write the failing tests** â€” append to `tests/test_ers_flow_journal.py`:

```python
# --- S4.7a: accepts_in_window (rolling count over wall_at; INCLUSIVE old edge) ----------------
from polybot.ers.flow import accepts_in_window


def test_accepts_in_window_boundary_pair_exact_edge_in_just_older_out():
    # Boundary PAIR (DESIGN SS4: in-window iff wall_now - wall_at <= window, INCLUSIVE old edge):
    # exactly-3600s-old is IN; 3601s-old is OUT.
    # Kills: `<` instead of `<=` on the old edge (the at-edge row); an unbounded / wrong-sign
    # window comparison (the just-older row).
    rows = [
        {"at": 1, "wall_at": 1000.0, "kind": "accept", "token_id": "t1", "amount": Decimal("12")},
        {"at": 2, "wall_at": 999.0, "kind": "accept", "token_id": "t2", "amount": Decimal("12")},
    ]
    assert accepts_in_window(rows, wall_now=4600.0, window_seconds=3600) == 1


def test_accepts_in_window_counts_only_accept_rows():
    # Kills: counting realized rows (win OR loss) toward the rate caps.
    rows = [
        {"at": 1, "wall_at": 100.0, "kind": "accept", "token_id": "t1", "amount": Decimal("12")},
        {"at": 2, "wall_at": 100.0, "kind": "realized", "token_id": "t1", "amount": Decimal("-5")},
        {"at": 3, "wall_at": 100.0, "kind": "realized", "token_id": "t1", "amount": Decimal("5")},
    ]
    assert accepts_in_window(rows, wall_now=100.0, window_seconds=3600) == 1
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: collection **ERROR** â€” `ImportError: cannot import name 'accepts_in_window' from 'polybot.ers.flow'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/flow.py`:

```python
def accepts_in_window(rows, *, wall_now, window_seconds):
    """Count kind=="accept" rows inside the rolling window: in-window iff
    ``wall_now - wall_at <= window_seconds`` (INCLUSIVE old edge -- the breaker/ApiStorm
    convention; keeping the boundary row is the tighter direction)."""
    return sum(1 for r in rows
               if r["kind"] == "accept" and wall_now - r["wall_at"] <= window_seconds)
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 8 passed; full suite **668 passed**.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: accepts_in_window rolling counter (inclusive old edge)"'
```

---

### Task A5: pending_in_window (accepts + abs(losses); wins NEVER offset; default 86400 boundary pair)

**Files:**
- Modify: `src/polybot/ers/flow.py` (append after `accepts_in_window`)
- Test: `tests/test_ers_flow_journal.py` (append)

- [ ] **Step 1: Write the failing tests** â€” append to `tests/test_ers_flow_journal.py`:

```python
# --- S4.7a: pending_in_window (accept flow + abs realized losses; wins NEVER offset) ----------
from polybot.ers.flow import pending_in_window


def test_pending_in_window_sums_accepts_plus_abs_of_realized_losses_across_mixed_kinds():
    # Kills: adding the SIGNED loss (12 + 8 - 3.50) instead of abs; dropping either kind
    # from the sum. Expected: 12 + 8 + |âˆ’3.50| = 23.50.
    rows = [
        {"at": 1, "wall_at": 100.0, "kind": "accept", "token_id": "t1", "amount": Decimal("12")},
        {"at": 2, "wall_at": 200.0, "kind": "accept", "token_id": "t2", "amount": Decimal("8")},
        {"at": 3, "wall_at": 300.0, "kind": "realized", "token_id": "t1", "amount": Decimal("-3.50")},
    ]
    assert pending_in_window(rows, wall_now=300.0) == Decimal("23.50")


def test_pending_in_window_a_realized_win_contributes_exactly_zero():
    # Kills: abs() over ALL realized rows (a win would ADD 50) and signed summation (a win
    # would SUBTRACT 50) -- wins NEVER offset pending (conservative, DESIGN SS4).
    rows = [
        {"at": 1, "wall_at": 100.0, "kind": "accept", "token_id": "t1", "amount": Decimal("12")},
        {"at": 2, "wall_at": 200.0, "kind": "realized", "token_id": "t2", "amount": Decimal("50")},
    ]
    assert pending_in_window(rows, wall_now=200.0) == Decimal("12")


def test_pending_in_window_default_window_boundary_pair_exact_edge_in_just_older_out():
    # Boundary PAIR on the DEFAULT 86400s window: exactly-86400s-old is IN (inclusive old
    # edge); 86401s-old is OUT.
    # Kills: `<` on the old edge; a wrong default window_seconds.
    rows = [
        {"at": 1, "wall_at": 13600.0, "kind": "accept", "token_id": "t1", "amount": Decimal("7")},
        {"at": 2, "wall_at": 13599.0, "kind": "accept", "token_id": "t2", "amount": Decimal("9")},
    ]
    assert pending_in_window(rows, wall_now=100000.0) == Decimal("7")
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: collection **ERROR** â€” `ImportError: cannot import name 'pending_in_window' from 'polybot.ers.flow'`.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/flow.py` (kind validation is deliberately NOT here yet â€” Task A6 adds it test-first; `r["amount"]` is read unconditionally so a missing key already propagates KeyError on every row):

```python
def pending_in_window(rows, *, wall_now, window_seconds=86400):
    """Today's pending worst-case-risk FLOW: the sum of accept amounts in the rolling window
    plus abs(amount) for realized LOSSES (amount < 0) in the window. Wins (amount >= 0)
    NEVER offset -- conservative. In-window iff ``wall_now - wall_at <= window_seconds``
    (INCLUSIVE old edge). Every row is read in full regardless of window, so a missing key
    propagates KeyError -- callers convert the raise to their fail-closed action."""
    total = Decimal("0")
    for r in rows:
        kind = r["kind"]
        amount = r["amount"]
        if wall_now - r["wall_at"] > window_seconds:
            continue
        if kind == "accept":
            total += amount
        elif kind == "realized" and amount < Decimal("0"):
            total += -amount
    return total
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 11 passed; full suite **671 passed**.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: pending_in_window - accepts plus abs realized losses, wins never offset"'
```

---

### Task A6: pending_in_window fails LOUD on malformed journal rows

**Files:**
- Modify: `src/polybot/ers/flow.py` (the `pending_in_window` body from Task A5 + a module-level `_KINDS`)
- Test: `tests/test_ers_flow_journal.py` (append)

- [ ] **Step 1: Write the failing tests** â€” append to `tests/test_ers_flow_journal.py`:

```python
def test_pending_in_window_raises_value_error_on_an_unknown_kind():
    # Corruption in OUR OWN journal is never skipped (DESIGN SS6.4): the helper RAISES and
    # each consumer converts the raise into its fail-closed action (gate block / breaker HALT).
    # Kills: silently ignoring unknown-kind rows (the `else: continue` mutation).
    rows = [{"at": 1, "wall_at": 100.0, "kind": "flattened", "token_id": "t1",
             "amount": Decimal("1")}]
    with pytest.raises(ValueError):
        pending_in_window(rows, wall_now=100.0)


def test_pending_in_window_propagates_key_error_on_a_missing_amount_key():
    # Kills: r.get("amount", <default>) tolerance -- a missing key in our own journal must
    # propagate KeyError (dict indexing), never default to zero.
    rows = [{"at": 1, "wall_at": 100.0, "kind": "accept", "token_id": "t1"}]
    with pytest.raises(KeyError):
        pending_in_window(rows, wall_now=100.0)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
```

Expected: **1 failed, 12 passed** â€” the ValueError pin fails with `Failed: DID NOT RAISE <class 'ValueError'>` (A5's minimal body ignores unknown kinds); the KeyError pin already passes because A5 reads `r["amount"]` by dict indexing on every row (observed green, documented â€” the RED in this task is the ValueError arm).

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/flow.py`, add at module level (below the imports):

```python
_KINDS = ("accept", "realized")
```

and replace `pending_in_window` with:

```python
def pending_in_window(rows, *, wall_now, window_seconds=86400):
    """Today's pending worst-case-risk FLOW: the sum of accept amounts in the rolling window
    plus abs(amount) for realized LOSSES (amount < 0) in the window. Wins (amount >= 0)
    NEVER offset -- conservative. In-window iff ``wall_now - wall_at <= window_seconds``
    (INCLUSIVE old edge). A malformed row (unknown kind / missing key) RAISES
    (ValueError / KeyError propagate) -- corruption in our own journal is never skipped;
    every row is validated in full regardless of window."""
    total = Decimal("0")
    for r in rows:
        kind = r["kind"]
        if kind not in _KINDS:
            raise ValueError(f"unknown flow kind: {kind!r}")
        amount = r["amount"]
        if wall_now - r["wall_at"] > window_seconds:
            continue
        if kind == "accept":
            total += amount
        elif amount < Decimal("0"):
            total += -amount
    return total
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 13 passed; full suite **673 passed**.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_journal.py && git commit -m "S4.7a: pending_in_window fails loud on malformed journal rows"'
```

---

### Task A7: structural pin â€” flow.py never references set_state / the resume state

**Files:**
- Test: `tests/test_ers_flow_journal.py` (append; no source change)

- [ ] **Step 1: Write the test** â€” append to `tests/test_ers_flow_journal.py`:

```python
def test_flow_module_source_never_references_the_resume_state_or_set_state():
    # STICKY pin (DESIGN SS6.2, extended to the new modules; mirrors the ers/anomaly.py pin):
    # nothing in ers/flow.py may transition op-state or even NAME the resume state -- the ONLY
    # automatic HALTED->resume in the system stays RestartReconciler's clean boot-reconcile.
    # Kills: any op-state mutation (or auto-resume) creeping into the flow module.
    from pathlib import Path

    from polybot.ers import flow as _f
    src = Path(_f.__file__).read_text(encoding="utf-8")
    assert "set_state" not in src
    assert "RUNNING" not in src
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” this pin asserts an ABSENCE, so it is green-on-arrival by design (the `ers/anomaly.py` precedent); the observed RED comes from a throwaway mutation proving the pin bites:

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && echo "# set_state" >> src/polybot/ers/flow.py && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q; git checkout -- src/polybot/ers/flow.py && find . -name __pycache__ -prune -exec rm -rf {} +'
```

Expected: **1 failed** (`assert "set_state" not in src`), 13 passed â€” then the mutation is reverted and pycache swept by the same command.

- [ ] **Step 3: Minimal implementation** â€” none (the pin already holds on the reverted source; verify `git status` shows only `tests/test_ers_flow_journal.py` modified):

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git status --short'
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_journal.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: 14 passed; full suite **674 passed** (660 baseline + 14 new).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_flow_journal.py && git commit -m "S4.7a: structural pin - flow.py never references set_state or the resume state"'
```

---

## Sub-slice S4.7b: The ratchet (tighten-only direction map, `assert_tighten_only`, step factories, `swap_caps`, the `active_caps()` re-plumb)

All tests live in NEW `tests/test_ers_ramp.py`. New module `src/polybot/ers/ramp.py`. Extensions to `src/polybot/ers/safety.py` (additive method + one import), `src/polybot/ers/intent_store.py` (docstring text only), `src/polybot/ers/controller.py` (the one-arg re-plumb). SACRED surfaces untouched: `evaluate_intent`, `propose_trade`/`record_decision`/`record_op_event` bodies, `process_pending` signature.

Full-suite note: S4.7a lands before this sub-slice on `pol-6-s4.7-breakers`, so "full suite green" below means **0 failed, passed == 660 baseline + S4.7a's tests + this file's cumulative count** (given per task). Line refs below are against pre-S4.7 files â€” if S4.7a shifted `intent_store.py` line numbers, match on the exact strings.

---

### Task B1: `ramp.py` skeleton â€” `TIGHTEN_DIRECTION` + all-38 structural pin + op-state source scan

**Files:**
- Create: `src/polybot/ers/ramp.py`
- Create: `tests/test_ers_ramp.py`

- [ ] **Step 1: Write the failing test** â€” create `tests/test_ers_ramp.py`:

```python
"""S4.7b (POL-6) -- the tighten-only caps ratchet.

TIGHTEN_DIRECTION over all 38 RiskCaps fields, the assert_tighten_only guard, the two
operator-signed step factories (daily 9/45, weekly 6/30), SafetyController.swap_caps
(audit-before-mutate, no-op-safe), and the run_cycle active_caps() re-plumb so a swap
bites the NEXT cycle's validator. DESIGN-S4.7-BREAKERS.md SS4/SS6.1/SS6.7.
"""

import dataclasses
from pathlib import Path

from polybot.ers import ramp
from polybot.ers.caps import RiskCaps


def test_tighten_direction_covers_exactly_the_riskcaps_fields():
    # Kills: a TIGHTEN_DIRECTION key dropped/renamed, or a future RiskCaps field added unclassified
    assert set(ramp.TIGHTEN_DIRECTION) == {f.name for f in dataclasses.fields(RiskCaps)}
    assert len(ramp.TIGHTEN_DIRECTION) == 38


def test_tighten_direction_classification_is_the_pinned_one():
    # Kills: misclassifying any field (e.g. reserve_floor as "down" would let the ratchet
    # shrink the reserve; a window field as "down" would falsely permit ambiguous changes)
    assert set(ramp.TIGHTEN_DIRECTION.values()) <= {"down", "up", "fixed"}
    assert {k for k, v in ramp.TIGHTEN_DIRECTION.items() if v == "up"} == {"reserve_floor"}
    assert {k for k, v in ramp.TIGHTEN_DIRECTION.items() if v == "fixed"} == {
        "nav", "min_position_floor", "l7_velocity_window_seconds", "api_storm_window_seconds"}
    assert sum(1 for v in ramp.TIGHTEN_DIRECTION.values() if v == "down") == 33


def test_ramp_source_never_touches_op_state():
    # Kills: a future ramp.py edit that drives op-state (the no-new-auto-resume structural
    # pin, DESIGN SS6.2 -- mirrors the anomaly-module scan)
    source = Path(ramp.__file__).read_text()
    assert "set_state" not in source
    assert "RUNNING" not in source
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: collection error â€” `ModuleNotFoundError: No module named 'polybot.ers.ramp'`.

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/ramp.py` (full content; the docstring/comments deliberately never contain the two scanned strings):

```python
"""The tighten-only caps ratchet (S4.7 / POL-6 -- DESIGN-S4.7-BREAKERS.md SS4/SS6.1).

TIGHTEN_DIRECTION classifies EVERY RiskCaps field: "down" (a tighter value is lower),
"up" (reserve_floor: a tighter value is higher), or "fixed" (any change refused in v1 --
nav, the dust floor, and the two counting windows whose direction is genuinely ambiguous).
A structural test pins the map keys against dataclasses.fields(RiskCaps), so adding a caps
field without classifying it here fails loudly.

This module is PURE over caps values: it never touches op-state, the store, or a clock.
"""

TIGHTEN_DIRECTION = {
    # Capital band.
    "nav": "fixed",
    "total_open_risk": "down",
    "reserve_floor": "up",
    # Per-intent caps.
    "per_trade": "down",
    "per_market": "down",
    "per_event_union": "down",
    "per_negrisk_event": "down",
    "per_source_open": "down",
    "per_source_locked_effective": "down",
    "max_locked_to_resolution": "down",
    # Concurrency.
    "max_concurrent": "down",
    "matrix_cold_concurrent": "down",
    # Breakers / sizing.
    "daily_pending_ceiling": "down",
    "kelly_fraction": "down",
    "min_position_floor": "fixed",
    "liquidity_depth_frac": "down",
    "liquidity_impact_cents": "down",
    # L7 drawdown breaker.
    "l7_freeze_floor": "down",
    "l7_flatten_floor": "down",
    "l7_velocity_delta": "down",
    "l7_velocity_window_seconds": "fixed",
    # S4 safety envelope.
    "weekly_loss_halt": "down",
    "consecutive_loss": "down",
    "new_positions_per_hour": "down",
    "new_positions_per_day": "down",
    "gtd_bracket_aggregate": "down",
    "clock_skew_tolerance_seconds": "down",
    "signing_canary_interval_seconds": "down",
    "dead_man_switch_timeout_seconds": "down",
    "reconcile_tolerance": "down",
    "reconcile_settle_window_seconds": "down",
    # S4.4 L5 anomaly thresholds.
    "midpoint_jump_halt": "down",
    "depth_collapse_fraction": "down",
    "depth_collapse_min_prev_shares": "down",
    "ws_staleness_halt_seconds": "down",
    "api_5xx_storm_count": "down",
    "api_auth_storm_count": "down",
    "api_storm_window_seconds": "fixed",
}
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 3 passed; full suite = 0 failed (660 + S4.7a + 3).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/ramp.py tests/test_ers_ramp.py && git commit -m "S4.7b B1: ramp.py TIGHTEN_DIRECTION + all-38 structural pin + op-state source scan"'
```

---

### Task B2: `assert_tighten_only` â€” accept/reject pairs per direction class

**Files:**
- Modify: `src/polybot/ers/ramp.py` (append one function; no imports yet needed beyond `dataclasses`)
- Test: `tests/test_ers_ramp.py` (append)

- [ ] **Step 1: Write the failing test** â€” in `tests/test_ers_ramp.py`, extend the import block (Edit):

old_string:
```python
import dataclasses
from pathlib import Path
```
new_string:
```python
import dataclasses
import types
from decimal import Decimal
from pathlib import Path

import pytest
```

then append at end of file:

```python
# --- B2: assert_tighten_only ------------------------------------------------------------------


def _fake_caps(**overrides):
    # A RiskCaps-SHAPED attribute bag that BYPASSES _verify: lets a test loosen exactly ONE
    # field in isolation (a real RiskCaps couples nav/total_open_risk/reserve_floor via
    # _verify, so e.g. "only reserve_floor lowered" is unconstructible). assert_tighten_only
    # iterates dataclasses.fields(old) and only getattr()s new, so a namespace suffices.
    values = dataclasses.asdict(RiskCaps())
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_assert_tighten_only_accepts_byte_equal_caps():
    # Kills: an inverted comparison rejecting equality (equal is ALWAYS a legal swap input)
    ramp.assert_tighten_only(RiskCaps(), RiskCaps())  # must not raise


def test_assert_tighten_only_accepts_a_lower_down_field():
    # Kills: the "down" arm written as new >= old (a strictly lower value must pass)
    ramp.assert_tighten_only(RiskCaps(), RiskCaps(per_trade=Decimal("9")))  # must not raise


def test_assert_tighten_only_rejects_a_just_over_down_field_naming_it():
    # Boundary pair with the equal/lower accepts: per_trade 12 -> 12.01 is a loosening.
    # Kills: the "down" comparison dropped or mutated to >=
    with pytest.raises(ValueError, match="per_trade"):
        ramp.assert_tighten_only(RiskCaps(), RiskCaps(per_trade=Decimal("12.01")))


def test_assert_tighten_only_accepts_a_higher_up_field():
    # reserve_floor 240 -> 255 (the daily-step shape, built as a REAL verified RiskCaps).
    # Kills: treating "up" like "down" (a GROWN reserve would be refused)
    tightened = RiskCaps(per_trade=Decimal("9"), total_open_risk=Decimal("45"),
                         reserve_floor=Decimal("255"), gtd_bracket_aggregate=Decimal("45"))
    ramp.assert_tighten_only(RiskCaps(), tightened)  # must not raise


def test_assert_tighten_only_rejects_a_just_under_up_field_naming_it():
    # Boundary pair: reserve_floor 240 -> 239.99 shrinks the reserve. Kills: the "up" arm dropped
    with pytest.raises(ValueError, match="reserve_floor"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(reserve_floor=Decimal("239.99")))


def test_assert_tighten_only_rejects_a_raised_fixed_field_nav():
    # Kills: "fixed" degraded to "up" (a raised nav must still be refused)
    with pytest.raises(ValueError, match="nav"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(nav=Decimal("301")))


def test_assert_tighten_only_rejects_a_lowered_fixed_field_min_position_floor():
    # Kills: "fixed" degraded to "down" (a lowered dust floor must still be refused)
    with pytest.raises(ValueError, match="min_position_floor"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(min_position_floor=Decimal("4.99")))
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: 7 failed with `AttributeError: module 'polybot.ers.ramp' has no attribute 'assert_tighten_only'`; the 3 B1 tests stay passed.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/ramp.py`, add below the module docstring (Edit):

old_string:
```python
TIGHTEN_DIRECTION = {
```
new_string:
```python
import dataclasses

TIGHTEN_DIRECTION = {
```

and append at end of file:

```python
def assert_tighten_only(old, new):
    """Raise ValueError naming the first field (declaration order) whose old->new change
    violates its TIGHTEN_DIRECTION class: "down" requires new <= old, "up" requires
    new >= old, "fixed" requires new == old. Equal is always acceptable. Pure comparison
    over getattr -- the caller (swap_caps) owns construction/_verify of the new caps."""
    for field in dataclasses.fields(old):
        direction = TIGHTEN_DIRECTION[field.name]
        old_value = getattr(old, field.name)
        new_value = getattr(new, field.name)
        if new_value == old_value:
            continue
        if direction == "fixed" or (direction == "down" and new_value > old_value) \
                or (direction == "up" and new_value < old_value):
            raise ValueError(
                f"tighten-only violation on {field.name} ({direction}): "
                f"{old_value} -> {new_value}")
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 10 passed; full suite = 0 failed (660 + S4.7a + 10).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/ramp.py tests/test_ers_ramp.py && git commit -m "S4.7b B2: assert_tighten_only per-field guard (down/up/fixed accept-reject pairs)"'
```

---

### Task B3: `step_daily` â€” the operator-signed 9/45/255/45 step

**Files:**
- Modify: `src/polybot/ers/ramp.py` (append `step_daily` + `Decimal` import)
- Test: `tests/test_ers_ramp.py` (append)

- [ ] **Step 1: Write the failing test** â€” append to `tests/test_ers_ramp.py`:

```python
# --- B3: step_daily ---------------------------------------------------------------------------


def test_step_daily_pins_the_exact_operator_signed_values():
    # Kills: any wrong step constant (fork 1 signed: per_trade 9, total 45, reserve 255, gtd 45)
    stepped = ramp.step_daily(RiskCaps())
    assert stepped.per_trade == Decimal("9")
    assert stepped.total_open_risk == Decimal("45")
    assert stepped.reserve_floor == Decimal("255")
    assert stepped.gtd_bracket_aggregate == Decimal("45")


def test_step_daily_touches_only_the_four_ratchet_fields():
    # Kills: a step that silently changes a construction-captured field (the stale-copy
    # boundary of DESIGN SS2 -- v1 steps must never touch L7/anomaly sentinel inputs)
    base = dataclasses.asdict(RiskCaps())
    stepped = dataclasses.asdict(ramp.step_daily(RiskCaps()))
    changed = {name for name in base if base[name] != stepped[name]}
    assert changed == {"per_trade", "total_open_risk", "reserve_floor", "gtd_bracket_aggregate"}


def test_step_daily_reconstructs_a_verified_riskcaps_with_a_fresh_hash():
    # dataclasses.replace re-runs __post_init__/_verify, so returning at all proves
    # constructibility. Kills: returning a non-RiskCaps bag / a hash that does not change
    # (the caps_swap audit detail would show old==new)
    stepped = ramp.step_daily(RiskCaps())
    assert isinstance(stepped, RiskCaps)
    assert stepped.content_hash() != RiskCaps().content_hash()


def test_step_daily_passes_the_tighten_only_guard():
    # Kills: a step constant drifting loose -- swap_caps would refuse its own ramp step
    ramp.assert_tighten_only(RiskCaps(), ramp.step_daily(RiskCaps()))  # must not raise


def test_step_daily_is_idempotent_by_hash():
    # Kills: a subtractive step (per_trade - 3 style) that keeps tightening on re-application
    # (run_cycle re-applies steps every cycle while the trigger holds -- must be a no-op)
    once = ramp.step_daily(RiskCaps())
    assert ramp.step_daily(once).content_hash() == once.content_hash()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: 5 failed with `AttributeError: module 'polybot.ers.ramp' has no attribute 'step_daily'`; the 10 earlier tests stay passed.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/ramp.py` (Edit the import, then append):

old_string:
```python
import dataclasses
```
new_string:
```python
import dataclasses
from decimal import Decimal
```

append at end of file:

```python
def step_daily(caps):
    """The daily-halt ramp step (fork 1, operator-signed 2026-07-02): per_trade -> min(., $9),
    total_open_risk -> min(., $45), with reserve_floor/gtd_bracket_aggregate re-derived to keep
    _verify's exact equalities (reserve == nav - total; gtd == total). min() makes the step
    idempotent AND composable with the deeper weekly step (weekly(daily(c)) == weekly(c));
    dataclasses.replace re-runs __post_init__/_verify on the frozen dataclass, so the result
    is a re-verified RiskCaps or a raise -- never a silently inconsistent envelope."""
    tightened_total = min(caps.total_open_risk, Decimal("45"))
    return dataclasses.replace(
        caps,
        per_trade=min(caps.per_trade, Decimal("9")),
        total_open_risk=tightened_total,
        reserve_floor=caps.nav - tightened_total,
        gtd_bracket_aggregate=tightened_total,
    )
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 15 passed; full suite = 0 failed (660 + S4.7a + 15).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/ramp.py tests/test_ers_ramp.py && git commit -m "S4.7b B3: step_daily ratchet step (9/45/255/45, idempotent, guard-clean)"'
```

---

### Task B4: `step_weekly` + the compose / never-loosen-back laws

**Files:**
- Modify: `src/polybot/ers/ramp.py` (append `step_weekly`)
- Test: `tests/test_ers_ramp.py` (append)

- [ ] **Step 1: Write the failing test** â€” append to `tests/test_ers_ramp.py`:

```python
# --- B4: step_weekly + composition ------------------------------------------------------------


def test_step_weekly_pins_the_exact_operator_signed_values():
    # Kills: any wrong weekly constant (fork 1 signed: per_trade 6, total 30, reserve 270, gtd 30)
    stepped = ramp.step_weekly(RiskCaps())
    assert stepped.per_trade == Decimal("6")
    assert stepped.total_open_risk == Decimal("30")
    assert stepped.reserve_floor == Decimal("270")
    assert stepped.gtd_bracket_aggregate == Decimal("30")


def test_step_weekly_passes_the_tighten_only_guard():
    # Kills: the weekly constants drifting loose -- swap_caps would refuse the step
    ramp.assert_tighten_only(RiskCaps(), ramp.step_weekly(RiskCaps()))  # must not raise


def test_step_weekly_after_daily_composes_to_weekly():
    # The pinned compose law: weekly(daily(c)) == weekly(c) by content hash.
    # Kills: a step pair that cannot stack (a daily breach then a weekly halt must land
    # exactly on the deeper weekly envelope)
    assert (ramp.step_weekly(ramp.step_daily(RiskCaps())).content_hash()
            == ramp.step_weekly(RiskCaps()).content_hash())


def test_step_daily_after_weekly_never_loosens_back():
    # Kills: dropping the min() -- a later daily trigger must NOT relax the deeper weekly
    # step from 6/30 back to 9/45 (that swap would also be refused, wedging the ramp)
    assert (ramp.step_daily(ramp.step_weekly(RiskCaps())).content_hash()
            == ramp.step_weekly(RiskCaps()).content_hash())


def test_step_weekly_is_idempotent_by_hash():
    # Kills: a subtractive weekly step that keeps tightening on re-application
    once = ramp.step_weekly(RiskCaps())
    assert ramp.step_weekly(once).content_hash() == once.content_hash()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: 5 failed with `AttributeError: module 'polybot.ers.ramp' has no attribute 'step_weekly'`; the 15 earlier tests stay passed.

- [ ] **Step 3: Minimal implementation** â€” append to `src/polybot/ers/ramp.py`:

```python
def step_weekly(caps):
    """The weekly-halt ramp step (fork 1; DEEPER than step_daily): per_trade -> min(., $6),
    total_open_risk -> min(., $30), reserve/gtd re-derived exactly as step_daily. The min()
    gives idempotence and the compose laws weekly(daily(c)) == weekly(c) and
    daily(weekly(c)) == weekly(c) -- a later daily trigger can never loosen the weekly
    envelope back."""
    tightened_total = min(caps.total_open_risk, Decimal("30"))
    return dataclasses.replace(
        caps,
        per_trade=min(caps.per_trade, Decimal("6")),
        total_open_risk=tightened_total,
        reserve_floor=caps.nav - tightened_total,
        gtd_bracket_aggregate=tightened_total,
    )
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 20 passed; full suite = 0 failed (660 + S4.7a + 20).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/ramp.py tests/test_ers_ramp.py && git commit -m "S4.7b B4: step_weekly + compose/never-loosen-back laws"'
```

---

### Task B5: `SafetyController.swap_caps` â€” tighten-only, no-op-safe, audit-before-mutate

**Files:**
- Modify: `src/polybot/ers/safety.py` (import at line 20; new method after `active_caps`, line 78)
- Modify: `src/polybot/ers/intent_store.py` (`record_op_event` docstring kind-set, lines 195â€“197 â€” text only, the method body is SACRED)
- Test: `tests/test_ers_ramp.py` (append)

- [ ] **Step 1: Write the failing test** â€” in `tests/test_ers_ramp.py`, extend the import block (Edit):

old_string:
```python
from polybot.ers import ramp
from polybot.ers.caps import RiskCaps
```
new_string:
```python
from polybot.core.clock import MonotonicStamper
from polybot.ers import ramp
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
```

then append at end of file:

```python
# --- B5: SafetyController.swap_caps -----------------------------------------------------------


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def test_swap_caps_real_swap_returns_true_and_installs_the_new_caps(tmp_path):
    # The controller starts HALTED and no set_state is issued: a tighten swap applies in ANY
    # op-state (DESIGN SS6.7). Kills: swap_caps never assigning self._caps / gating on op-state
    store = _store(tmp_path)
    try:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        tightened = ramp.step_daily(RiskCaps())
        assert ctl.swap_caps(tightened, reason="ramp_down") is True
        assert ctl.active_caps() is tightened
    finally:
        store.close()


def test_swap_caps_real_swap_audits_caps_swap_with_both_hash_prefixes(tmp_path):
    # Kills: a missing/mis-formatted caps_swap audit row (the 16-char hash pair IS the
    # tamper-evidence trail of which envelope replaced which)
    store = _store(tmp_path)
    try:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        old_hash = RiskCaps().content_hash()
        tightened = ramp.step_daily(RiskCaps())
        ctl.swap_caps(tightened, reason="ramp_down")
        rows = [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()]
        assert rows == [("caps_swap", "ramp_down",
                         f"{old_hash[:16]}->{tightened.content_hash()[:16]}")]
    finally:
        store.close()


def test_swap_caps_noop_swap_returns_false_with_no_audit_row(tmp_path):
    # Hash-identical caps => idempotent re-application writes NOTHING (run_cycle re-applies
    # steps while a trigger holds -- no audit spam). Kills: auditing/mutating on a no-op
    store = _store(tmp_path)
    try:
        original = RiskCaps()
        ctl = SafetyController(caps=original, store=store, clock=lambda: 0)
        assert ctl.swap_caps(RiskCaps(), reason="ramp_down") is False
        assert ctl.active_caps() is original
        assert store.op_audit_log() == []
    finally:
        store.close()


def test_swap_caps_rejects_a_loosening_swap_untouched_and_unaudited(tmp_path):
    # Default caps LOOSEN the daily-stepped ones (total_open_risk 45 -> 60 fires first in
    # declaration order). Kills: the tighten-only guard dropped, or caps mutated / a row
    # written on the reject path
    store = _store(tmp_path)
    try:
        tightened = ramp.step_daily(RiskCaps())
        ctl = SafetyController(caps=tightened, store=store, clock=lambda: 0)
        with pytest.raises(ValueError, match="total_open_risk"):
            ctl.swap_caps(RiskCaps(), reason="ramp_down")
        assert ctl.active_caps() is tightened
        assert store.op_audit_log() == []
    finally:
        store.close()


def _raising_op_event(**kwargs):
    raise RuntimeError("op_audit write refused")


def test_swap_caps_audits_before_mutating(tmp_path, monkeypatch):
    # Audit-before-mutate: a refused audit write must leave the OLD caps active, so a crash
    # mid-swap always leaves the explanation AHEAD of the effect (the set_state doctrine).
    # Kills: mutate-then-audit reordering
    store = _store(tmp_path)
    try:
        original = RiskCaps()
        ctl = SafetyController(caps=original, store=store, clock=lambda: 0)
        monkeypatch.setattr(store, "record_op_event", _raising_op_event)
        with pytest.raises(RuntimeError):
            ctl.swap_caps(ramp.step_daily(RiskCaps()), reason="ramp_down")
        assert ctl.active_caps() is original
    finally:
        store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: 5 failed with `AttributeError: 'SafetyController' object has no attribute 'swap_caps'`; the 20 earlier tests stay passed.

- [ ] **Step 3: Minimal implementation** â€” three edits.

`src/polybot/ers/safety.py` â€” Edit 1 (module top, line 20; ramp imports only dataclasses/decimal, so no cycle):

old_string:
```python
from dataclasses import dataclass
```
new_string:
```python
from dataclasses import dataclass

from polybot.ers.ramp import assert_tighten_only
```

Edit 2 â€” insert the method directly after `active_caps` (lines 76â€“78):

old_string:
```python
    def active_caps(self):
        # The swappable RiskCaps reference (the S4.7 ramp-DOWN ratchet replaces it atomically).
        return self._caps
```
new_string:
```python
    def active_caps(self):
        # The swappable RiskCaps reference (the S4.7 ramp-DOWN ratchet replaces it atomically).
        return self._caps

    def swap_caps(self, new_caps, *, reason):
        """The S4.7 ramp-DOWN ratchet: atomically install a NEW re-verified RiskCaps.

        Tighten-only (assert_tighten_only over every field per ramp.TIGHTEN_DIRECTION -- a
        loosening swap raises ValueError and changes NOTHING); idempotent (a hash-identical
        new_caps returns False and writes NO audit row); audited (kind=caps_swap,
        detail=old->new 16-char content-hash prefixes) BEFORE the in-memory mutate, so a
        crash mid-swap leaves the explanation ahead of the effect (the set_state doctrine).
        Applies in ANY op-state -- tightening while halted is harmless and desirable.
        Returns True iff the caps actually changed."""
        assert_tighten_only(self._caps, new_caps)
        old_hash = self._caps.content_hash()
        new_hash = new_caps.content_hash()
        if new_hash == old_hash:
            return False
        self._store.record_op_event(
            kind="caps_swap", reason=reason, detail=f"{old_hash[:16]}->{new_hash[:16]}")
        self._caps = new_caps
        return True
```

`src/polybot/ers/intent_store.py` â€” Edit 3 (docstring TEXT only, lines 195â€“197; the pinned-contract-sanctioned kind-set growth):

old_string:
```python
        {state_change, kill, pause, flatten, heartbeat, cancel_all}; ``reason`` is a REASON_* code
```
new_string:
```python
        {state_change, kill, pause, flatten, heartbeat, cancel_all, caps_swap}; ``reason`` is a REASON_* code
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 25 passed; full suite = 0 failed (660 + S4.7a + 25).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py src/polybot/ers/intent_store.py tests/test_ers_ramp.py && git commit -m "S4.7b B5: SafetyController.swap_caps (tighten-only, audit-before-mutate, no-op-safe)"'
```

---

### Task B6: THE re-plumb â€” `run_cycle` reads `controller.active_caps()`, and a swap bites the next cycle

**Files:**
- Modify: `src/polybot/ers/controller.py` (lines 78â€“81 â€” the `process_pending` call's `caps=` ARG VALUE only; signature/flow SACRED)
- Test: `tests/test_ers_ramp.py` (append)

Sizing math verified against `evaluate_intent` (validator.py:100â€“169): `_P` at ask `0.50`, `p=0.9`, NAV 300 gives a Kelly stake of `0.25 * 0.8 * 300 = 60`; the min-headroom clamp is `per_trade` (12 default / 9 after `step_daily`) because per_market=18, per_event=24, per_source=30, total_open headroom â‰¥ 33, size_suggestion=100, liquidity=`0.10*1000*0.50=50` are all larger. The second intent uses its OWN token/market/event (`t2/m2/e2`) so no shared-cap headroom confounds the clamp.

- [ ] **Step 1: Write the failing test** â€” in `tests/test_ers_ramp.py`, extend the import block (Edit):

old_string:
```python
from polybot.core.clock import MonotonicStamper
from polybot.ers import ramp
```
new_string:
```python
from polybot.core.clock import MonotonicStamper
from polybot.ers import ramp
from polybot.ers import safety as _safety
from polybot.ers.controller import ERSController
from polybot.ers.service import PaperSigner
from polybot.ingestion.orderbook import LocalBook
```

then append at end of file:

```python
# --- B6: the run_cycle active_caps() re-plumb --------------------------------------------------


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def test_run_cycle_sizes_off_the_controllers_active_caps_not_the_constructor_caps(tmp_path):
    # The re-plumb itself: the ERSController is built with DEFAULT caps (per_trade 12) while
    # the SafetyController holds the daily-stepped envelope (per_trade 9) -- the cycle's
    # accept must clamp at 9, proving process_pending received controller.active_caps().
    # Kills: reverting the caps= arg to self._caps
    store = _store(tmp_path)
    try:
        ctl = SafetyController(caps=ramp.step_daily(RiskCaps()), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        rc.run_cycle()
        decided = store.get("i1")
        assert decided.status == "ACCEPTED"
        assert decided.decision_reason == "per_trade_cap"
        assert decided.decision_stake_usd == Decimal("9")
    finally:
        store.close()


def test_swap_caps_between_cycles_bites_the_next_cycles_validator(tmp_path):
    # The at/after pair: cycle 1 clamps at the signed per_trade 12; a step_daily swap BETWEEN
    # cycles clamps cycle 2's fresh intent (own market/event -- no shared-cap confound) at 9.
    # Kills: run_cycle caching active_caps() at construction instead of reading it per cycle
    store = _store(tmp_path)
    try:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        books = {"t1": _book("0.50"), "t2": _book("0.50")}
        rc = ERSController(store=store, book_for=books.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        store.propose_trade("i1", **_P)
        rc.run_cycle()
        assert store.get("i1").decision_stake_usd == Decimal("12")   # pre-swap clamp

        assert ctl.swap_caps(ramp.step_daily(ctl.active_caps()), reason="ramp_down") is True
        store.propose_trade("i2", **{**_P, "token_id": "t2", "condition_id": "m2",
                                     "event_id": "e2"})
        rc.run_cycle()
        decided = store.get("i2")
        assert decided.status == "ACCEPTED"
        assert decided.decision_reason == "per_trade_cap"
        assert decided.decision_stake_usd == Decimal("9")            # the swap bit next cycle
    finally:
        store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
```

Expected: 2 failed on the stake assertions â€” `AssertionError: assert Decimal('12') == Decimal('9')` (both cycles still size off the construction-captured `self._caps`, per_trade 12); the 25 earlier tests stay passed.

- [ ] **Step 3: Minimal implementation** â€” `src/polybot/ers/controller.py`, Edit lines 78â€“81 (the `caps=` ARG VALUE only; every other argument byte-identical):

old_string:
```python
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio, caps=self._caps,
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink)
```
new_string:
```python
        # THE S4.7 re-plumb: read the SWAPPABLE caps from the SafetyController EVERY cycle so
        # a ramp-DOWN swap_caps lands on the very next cycle's validator/GTD derivation.
        # self._caps remains only the construction-time NAV source for the scaffold portfolio.
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio,
            caps=self._controller.active_caps(),
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink)
```

- [ ] **Step 4: Task file green + FULL suite green** (the re-plumb must keep every pre-existing test green â€” absent a swap, `active_caps()` carries the same values the constructor caps did)

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_ramp.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```

Expected: file = 27 passed; full suite = 0 failed (660 baseline + S4.7a's tests + 27 from this file).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_ramp.py && git commit -m "S4.7b B6: run_cycle active_caps() re-plumb -- swaps bite the next cycle"'
```

---

## Sub-slice S4.7c: The flow gate

**Dependency note:** Tasks C1â€“C4 touch only `safety.py`/`intent_store.py` and are buildable immediately after S4.7b. Tasks C5â€“C8 REQUIRE S4.7a merged (`src/polybot/ers/flow.py` with `accepts_in_window`/`pending_in_window`, and `IntentStore.record_flow_event`/`flow_log`). All tests live in the NEW `tests/test_ers_flow_gate.py`. Line refs below are against safety.py/intent_store.py as of the S4.7 branch point â€” anchor edits by the quoted content, not the line number, since S4.7a/b may have shifted them.

---

### Task C1: The nine S4.7 REASON_* constants + the caps_swap op-audit kind

**Files:**
- Create: `tests/test_ers_flow_gate.py`
- Modify: `src/polybot/ers/safety.py` (insert after the S4.4 reason block, line 44 `REASON_L5_CANARY_FAIL`)
- Modify: `src/polybot/ers/intent_store.py` (the `record_op_event` docstring, line 196)

- [ ] **Step 1: Write the failing test** â€” create `tests/test_ers_flow_gate.py` with the full module header (helpers copied per file per convention; later tasks only append tests):

```python
"""S4.7c -- the flow gate (POL-6; DESIGN-S4.7-BREAKERS SS3 rows 1-2, SS4).

The nine S4.7 REASON_* constants, SafetyController.wire_flow_gate (one-shot late binder),
the verdict RUNNING-branch consult (the gate BLOCKS without touching op-state -- the block
auto-slides with the window; a raising gate fail-closes to flow_gate_error), make_flow_gate's
three ordered arms (hourly rate, daily rate, conservative per_trade-headroom daily ceiling),
and the gate-through-verdict e2e. Helpers are copied per file per convention (no conftest)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import OpVerdict, SafetyController
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _store(path):
    return IntentStore(path, MonotonicStamper())


def _running_controller(tmp_path):
    """A controller already transitioned to RUNNING (so only the gate can block)."""
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl, store


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def test_s4_7_flow_loss_ramp_reason_constants_exist_with_exact_strings():
    # The nine NET-NEW S4.7 reason codes -- free-form Decision.reason / op-audit strings, NO
    # validator/schema change (mirrors test_s4_4_l5_reason_constants_exist_with_exact_strings).
    # Kills: renaming any constant or typo-ing its string (the gate/breakers/ratchet report
    # these verbatim as block/halt/audit reasons).
    from polybot.ers import safety as _s
    assert _s.REASON_RATE_HOURLY == "rate_cap_hourly"
    assert _s.REASON_RATE_DAILY == "rate_cap_daily"
    assert _s.REASON_DAILY_CEILING == "daily_ceiling"
    assert _s.REASON_DAILY_PENDING_PAUSE == "daily_pending_pause"
    assert _s.REASON_WEEKLY_LOSS == "weekly_loss_halt"
    assert _s.REASON_CONSECUTIVE_LOSS == "consecutive_loss"
    assert _s.REASON_RAMP_DOWN == "ramp_down"
    assert _s.REASON_FLOW_GATE_ERROR == "flow_gate_error"
    assert _s.REASON_FLOW_DATA_ERROR == "flow_data_error"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed` â€” `AttributeError: module 'polybot.ers.safety' has no attribute 'REASON_RATE_HOURLY'`. (If S4.7b already landed some constants, the failure points at the first still-missing one â€” add only the missing lines in Step 3.)

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/safety.py`, insert directly below `REASON_L5_CANARY_FAIL = "l5_canary_fail" ...` (line 44):

```python
# --- S4.7 reason codes (NET-NEW; the flow gate / loss breakers / ramp-ratchet vocabulary) ------
REASON_RATE_HOURLY = "rate_cap_hourly"              # accepts in rolling 3600s >= new_positions_per_hour
REASON_RATE_DAILY = "rate_cap_daily"                # accepts in rolling 86400s >= new_positions_per_day
REASON_DAILY_CEILING = "daily_ceiling"              # conservative per_trade-headroom pre-crossing block
REASON_DAILY_PENDING_PAUSE = "daily_pending_pause"  # realized losses pushed pending over -> sticky PAUSE
REASON_WEEKLY_LOSS = "weekly_loss_halt"             # rolling-7d realized losses > cap -> sticky HALT
REASON_CONSECUTIVE_LOSS = "consecutive_loss"        # trailing realized-loss streak >= cap -> sticky PAUSE
REASON_RAMP_DOWN = "ramp_down"                      # swap_caps audit reason for a ratchet step
REASON_FLOW_GATE_ERROR = "flow_gate_error"          # the flow gate raised -> fail-closed verdict block
REASON_FLOW_DATA_ERROR = "flow_data_error"          # flow_journal corruption -> loss breakers HALT
```

And in `src/polybot/ers/intent_store.py` grow the `record_op_event` docstring kind-set (docstring text ONLY â€” the method body is sacred). Edit line 196:

old: `` {state_change, kill, pause, flatten, heartbeat, cancel_all}; ``reason`` is a REASON_* code ``
new: `` {state_change, kill, pause, flatten, heartbeat, cancel_all, caps_swap}; ``reason`` is a REASON_* code ``

(Skip this edit if S4.7b already grew the set.)

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `1 passed` in the task file; full suite 0 failed (pre-task total + 1).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py src/polybot/ers/intent_store.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: nine flow/loss/ramp REASON_* constants + caps_swap in the op-audit kind-set"'
```

---

### Task C2: `SafetyController.wire_flow_gate` â€” the one-shot late binder

**Files:**
- Modify: `src/polybot/ers/safety.py` (`__init__` line ~71 after `self._reason = REASON_UNCLEAN_RESTART`; new method after `active_caps`, line ~78)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_wire_flow_gate_second_call_raises_runtime_error(tmp_path):
    # One-shot late binder (design SS4: the gate needs caps_provider=controller.active_caps,
    # so it cannot be a ctor kwarg). Kills: dropping the already-wired guard (a silent re-wire
    # could swap the safety gate out from under a running loop).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: None)
        with pytest.raises(RuntimeError):
            ctl.wire_flow_gate(lambda: None)
    finally:
        ctl_store.close()


def test_unwired_running_verdict_is_byte_identical_to_today(tmp_path):
    # Unwired == today byte-for-byte: the RUNNING branch returns the no-block verdict
    # (the existing 660-baseline suite pins the other branches). Kills: __init__ pre-wiring
    # _flow_gate to anything non-None (a phantom gate would block a clean RUNNING loop).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, None, None, ())
    finally:
        ctl_store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed, 2 passed` â€” the one-shot test fails `AttributeError: 'SafetyController' object has no attribute 'wire_flow_gate'`. (The byte-compat pin passes by construction â€” it is the boundary partner guarding this task's `__init__` change.)

- [ ] **Step 3: Minimal implementation** â€” in `SafetyController.__init__`, after `self._reason = REASON_UNCLEAN_RESTART`:

```python
        # S4.7: the flow gate is a ONE-SHOT late binder (wire_flow_gate) because it needs
        # caps_provider=self.active_caps -- it cannot exist before the controller does.
        # Unwired (None) == today's verdict byte-for-byte.
        self._flow_gate = None
```

New method directly after `active_caps`:

```python
    def wire_flow_gate(self, gate):
        """One-shot late binder for the S4.7 flow gate (rate caps + daily pending ceiling).

        ``gate`` is a 0-arg callable -> None | a REASON_* string, consulted ONLY in verdict()'s
        RUNNING branch. One-shot: a re-wire is a mis-assembly, not a supported operation -- the
        second call fails LOUD rather than silently swapping the safety gate."""
        if self._flow_gate is not None:
            raise RuntimeError("flow gate already wired")
        self._flow_gate = gate
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `3 passed`; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: SafetyController.wire_flow_gate one-shot late binder"'
```

---

### Task C3: verdict RUNNING-branch consult â€” blocks without touching op-state

**Files:**
- Modify: `src/polybot/ers/safety.py` (verdict RUNNING branch, lines ~116-118)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_running_verdict_with_gate_returning_none_does_not_block(tmp_path):
    # No-block side of the consult pair. Kills: inverting the `reason is not None` check
    # (blocking on None would wedge every clean RUNNING cycle).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: None)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, None, None, ())
    finally:
        ctl_store.close()


def test_running_verdict_with_gate_reason_blocks_but_op_state_and_audit_are_untouched(tmp_path):
    # A gate reason blocks THIS cycle's intents while action stays RUNNING, state() stays
    # RUNNING, and NO op-audit row is written -- the block must auto-slide with the window
    # (design SS2 "the gate blocks, states stick"; no new auto-resume path exists to undo a
    # sticky transition). Kills: the consult calling set_state or record_op_event (a sticky
    # gate block would then need an operator RESUME every hour).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        ctl.wire_flow_gate(lambda: _safety.REASON_RATE_HOURLY)
        audit_before = ctl_store.op_audit_log()
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, "rate_cap_hourly", None, ("rate_cap_hourly",))
        assert ctl.state() == _safety.RUNNING
        assert ctl_store.op_audit_log() == audit_before
    finally:
        ctl_store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed, 4 passed` â€” the blocking test fails on the verdict equality (`OpVerdict(RUNNING, None, ...)` returned: the gate is wired but verdict never consults it). The gate-None partner passes trivially pre-impl (its kill target is the inverted check post-impl).

- [ ] **Step 3: Minimal implementation** â€” in `safety.py` `verdict()`, replace the RUNNING branch:

old:
```python
        if self._state == RUNNING:
            # RUNNING -> no op-block; the loop proceeds to the L7 breaker unchanged.
            return OpVerdict(RUNNING, None, None, ())
```
new:
```python
        if self._state == RUNNING:
            # S4.7: consult the flow gate (rate caps + daily pending ceiling). The gate BLOCKS
            # without touching op-state -- when the window slides the block evaporates (no new
            # auto-resume path; sticky transitions stay the S4.4 edge-triggered doctrine).
            if self._flow_gate is not None:
                reason = self._flow_gate()
                if reason is not None:
                    return OpVerdict(RUNNING, reason, None, (reason,))
            # RUNNING -> no op-block; the loop proceeds to the L7 breaker unchanged.
            return OpVerdict(RUNNING, None, None, ())
```

All other branches byte-untouched.

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `5 passed`; full suite 0 failed (the unwired path is byte-compatible â€” every pre-existing controller test stays green).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: verdict RUNNING branch consults the flow gate (blocks without touching op-state)"'
```

---

### Task C4: Fail-closed raising gate + PAUSED/HALTED never consult

**Files:**
- Modify: `src/polybot/ers/safety.py` (the consult added in C3)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_raising_gate_fail_closes_with_flow_gate_error_and_state_stays_running(tmp_path):
    # Fail closed on our own data (design SS6.4): a raising gate means the flow_journal read
    # is corrupt -- the verdict blocks with flow_gate_error instead of propagating, and the
    # op-state is untouched (the block clears if the read recovers; no operator unwind needed).
    # Kills: letting the exception escape verdict (wedges process_pending), or except-ing to a
    # silent no-block pass (trades through corruption).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        def _corrupt_gate():
            raise RuntimeError("flow_journal corrupted")
        ctl.wire_flow_gate(_corrupt_gate)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v == OpVerdict(_safety.RUNNING, "flow_gate_error", None, ("flow_gate_error",))
        assert ctl.state() == _safety.RUNNING
    finally:
        ctl_store.close()


def test_paused_verdict_never_consults_the_gate(tmp_path):
    # The consult lives ONLY in the RUNNING branch: PAUSED blocks under its stored reason and
    # the gate is never called. Kills: hoisting the consult above the state dispatch (a gate
    # reason could then overwrite the sticky paused reason the operator must see).
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        calls = []
        def _counting_gate():
            calls.append(1)
            return None
        ctl.wire_flow_gate(_counting_gate)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v.block_reason == "l8_paused"
        assert calls == []
    finally:
        ctl_store.close()


def test_halted_verdict_never_consults_the_gate(tmp_path):
    # HALTED boundary partner (the boot default): blocks unclean_restart, gate never called.
    # Kills: hoisting the consult above the state dispatch.
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    try:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # starts HALTED
        calls = []
        def _counting_gate():
            calls.append(1)
            return None
        ctl.wire_flow_gate(_counting_gate)
        v = ctl.verdict(Portfolio(nav=Decimal("300")), PaperSigner())
        assert v.block_reason == "unclean_restart"
        assert calls == []
    finally:
        store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed, 7 passed` â€” the raising-gate test fails with the propagated `RuntimeError: flow_journal corrupted` (C3's minimal consult has no try/except yet). The PAUSED/HALTED partners pass by construction (the consult is already inside the RUNNING branch); they pin against a future hoist.

- [ ] **Step 3: Minimal implementation** â€” in the C3 consult, wrap the gate call:

old:
```python
            if self._flow_gate is not None:
                reason = self._flow_gate()
                if reason is not None:
                    return OpVerdict(RUNNING, reason, None, (reason,))
```
new:
```python
            if self._flow_gate is not None:
                try:
                    reason = self._flow_gate()
                except Exception:
                    # A raising gate is corruption in our OWN safety ledger: fail CLOSED with
                    # its own reason -- never propagate, never silently pass (design SS6.4).
                    reason = REASON_FLOW_GATE_ERROR
                if reason is not None:
                    return OpVerdict(RUNNING, reason, None, (reason,))
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `8 passed`; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/safety.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: raising flow gate fail-closes to flow_gate_error; PAUSED/HALTED never consult"'
```

---

### Task C5: `make_flow_gate` â€” the hourly arm + per-call journal/caps/clock reads

*(Requires S4.7a merged: `flow.py` window helpers + `record_flow_event`/`flow_log`.)*

**Files:**
- Modify: `src/polybot/ers/flow.py` (append `make_flow_gate` + one import)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_flow_gate_one_accept_in_the_hour_returns_none_under_cap_two(tmp_path):
    # At-boundary partner: 1 accept < new_positions_per_hour(2) -> the 2nd is still allowed.
    # Kills: off-by-one down (count >= cap - 1), which would block with headroom left.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() is None


def test_flow_gate_two_accepts_in_the_hour_blocks_the_would_be_third(tmp_path):
    # Just-over partner: 2 accepts == new_positions_per_hour(2) -> rate_cap_hourly (blocking
    # the WOULD-BE 3rd). Amounts are tiny so no other arm can fire.
    # Kills: >= mutated to > (2 > 2 would let a 3rd position through the signed rate cap).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() == "rate_cap_hourly"


def test_flow_gate_re_reads_the_journal_on_every_call(tmp_path):
    # The gate is consulted PER CYCLE: rows recorded after make_flow_gate must count.
    # Kills: capturing store.flow_log() once at make time (new accepts would never be counted).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() is None
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        assert gate() == "rate_cap_hourly"


def test_flow_gate_auto_slides_open_when_the_window_passes(tmp_path):
    # Blocked at the inclusive old edge (age == 3600 still counts -- the breaker/ApiStorm
    # convention, keeping the boundary row is tighter), open one second past it. Recovery is
    # AUTOMATIC: no set_state, no operator. Kills: freezing wall_clock() at make time (a
    # captured `now` would block forever), and flipping the inclusive <= window edge.
    from polybot.ers.flow import make_flow_gate
    wall = [200.0]
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=150.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: wall[0])
        assert gate() == "rate_cap_hourly"
        wall[0] = 3750.0   # newest accept age == 3600 exactly -> STILL in-window (inclusive)
        assert gate() == "rate_cap_hourly"
        wall[0] = 3751.0   # age 3601 -> out; daily 2 < 6; pending 2 + 12 <= 24 -> open again
        assert gate() is None


def test_flow_gate_consults_the_caps_provider_on_every_call(tmp_path):
    # The gate follows the ratchet: a tightened envelope flips the verdict on the SAME journal.
    # Kills: capturing caps_provider() once at make time (a swap_caps ramp step would never
    # bite the gate -- design SS4 binds caps_provider=controller.active_caps for exactly this).
    from polybot.ers.flow import make_flow_gate
    caps_cell = [RiskCaps()]
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: caps_cell[0], wall_clock=lambda: 200.0)
        assert gate() is None                                # 1 accept < hour cap 2
        caps_cell[0] = RiskCaps(new_positions_per_hour=1)    # tighten (1 <= day 6: constructible)
        assert gate() == "rate_cap_hourly"                   # same rows, tighter caps -> blocked
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `5 failed, 8 passed` â€” all five fail at `from polybot.ers.flow import make_flow_gate` with `ImportError: cannot import name 'make_flow_gate'`.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/flow.py`, add to the import block (safety.py never imports flow.py, so no cycle; the constant names contain neither `set_state` nor the forbidden state word, keeping the structural source-scan pin clean):

```python
from polybot.ers.safety import REASON_RATE_HOURLY
```

and append:

```python
def make_flow_gate(store, caps_provider, *, wall_clock):
    """The per-cycle flow gate (DESIGN-S4.7 SS3 rows 1-2 + SS4): returns a 0-arg callable ->
    None | a REASON_* string, wired into SafetyController.verdict's RUNNING branch via
    wire_flow_gate.

    store / caps_provider / wall_clock are consulted PER CALL: the gate follows the sliding
    window AND the ramp ratchet (assembly binds caps_provider=controller.active_caps). The
    gate does NOT catch its own exceptions -- verdict wraps a raise into flow_gate_error
    (fail closed, SS6.4). It does NOT filter frozen tokens: it is 0-arg with no portfolio
    view, and unfiltered accepts only count HIGHER = MORE blocking = the conservative
    direction (documented deviation from the breakers' frozen exclusion)."""
    def _gate():
        caps = caps_provider()
        rows = store.flow_log()
        now = wall_clock()
        if accepts_in_window(rows, wall_now=now, window_seconds=3600) >= caps.new_positions_per_hour:
            return REASON_RATE_HOURLY
        return None
    return _gate
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `13 passed`; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: make_flow_gate hourly rate arm with per-call journal/caps/clock reads"'
```

---

### Task C6: The daily rate arm + hourly-before-daily ordering

**Files:**
- Modify: `src/polybot/ers/flow.py` (the `_gate` body + import line from C5)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_flow_gate_six_accepts_spread_over_the_day_blocks_daily_rate(tmp_path):
    # 6 accepts all OLDER than the hour (hourly arm sees 0) but inside 24h == the daily cap(6)
    # -> rate_cap_daily. Ages run 45000..50000s: every row is > 3600 old and <= 86400 old.
    # Kills: dropping the daily arm, or windowing it over 3600s instead of 86400s.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        for i, at in enumerate((0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0)):
            store.record_flow_event(kind="accept", token_id=f"a{i}", amount=Decimal("1"), wall_at=at)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 50000.0)
        assert gate() == "rate_cap_daily"


def test_flow_gate_hourly_wins_when_both_rate_arms_are_breached(tmp_path):
    # 6 accepts inside ONE hour breach both arms (6 >= 2 hourly AND 6 >= 6 daily); the reason
    # must be the hourly one -- checked FIRST (design SS3 row 1, the SS4 pinned order).
    # Kills: re-ordering the arms (daily-first would misreport the block reason the operator
    # and the intent audit see).
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        for i, at in enumerate((100.0, 200.0, 300.0, 400.0, 500.0, 600.0)):
            store.record_flow_event(kind="accept", token_id=f"a{i}", amount=Decimal("1"), wall_at=at)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 700.0)
        assert gate() == "rate_cap_hourly"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed, 14 passed` â€” the daily test fails `assert None == 'rate_cap_daily'` (only the hourly arm exists, and it sees 0 accepts in the hour). The ordering pin passes pre-impl via the hourly arm â€” its kill target is a daily-first re-order after this task lands.

- [ ] **Step 3: Minimal implementation** â€” extend the C5 import line:

```python
from polybot.ers.safety import REASON_RATE_DAILY, REASON_RATE_HOURLY
```

and insert into `_gate` directly after the hourly check's `return REASON_RATE_HOURLY`:

```python
        if accepts_in_window(rows, wall_now=now, window_seconds=86400) >= caps.new_positions_per_day:
            return REASON_RATE_DAILY
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `15 passed`; full suite 0 failed.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: flow gate daily rate arm, hourly checked before daily"'
```

---

### Task C7: The conservative daily-ceiling arm (per_trade headroom)

**Files:**
- Modify: `src/polybot/ers/flow.py` (the `_gate` body + import line)
- Test: `tests/test_ers_flow_gate.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_flow_gate_pending_exactly_at_per_trade_headroom_returns_none(tmp_path):
    # At-boundary partner: pending 12 + new_worst_case per_trade(12) == ceiling(24) -- NOT
    # crossed (would_cross_daily_pending_ceiling is strict >, pinned in
    # test_ers_safety_daily_ceiling) -> no block. Kills: > mutated to >= at the consumption
    # site, or double-adding the headroom.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("12"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() is None


def test_flow_gate_pending_just_over_headroom_blocks_daily_ceiling(tmp_path):
    # Just-over partner: pending 12.01 + per_trade(12) = 24.01 > 24 -> daily_ceiling. This is
    # the CONSERVATIVE pre-crossing block (new_worst_case = caps.per_trade, design SS6.6): no
    # intent can ever cross the ceiling; smaller intents may block early -- the fail-closed
    # direction (rows-70-vs-72 interplay: pure trade flow can then NEVER trip the sticky
    # daily_pending_pause; only realized losses can). Kills: dropping the ceiling arm, or
    # passing new_worst_case=0/the intent's stake instead of caps.per_trade.
    from polybot.ers.flow import make_flow_gate
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("12.01"), wall_at=100.0)
        gate = make_flow_gate(store, lambda: RiskCaps(), wall_clock=lambda: 200.0)
        assert gate() == "daily_ceiling"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `1 failed, 16 passed` â€” the just-over test fails `assert None == 'daily_ceiling'` (no ceiling arm yet; rate arms see 1 accept). The at-boundary partner passes pre-impl â€” it is the strict-`>` pin once the arm lands.

- [ ] **Step 3: Minimal implementation** â€” extend the import:

```python
from polybot.ers.safety import (
    REASON_DAILY_CEILING,
    REASON_RATE_DAILY,
    REASON_RATE_HOURLY,
    would_cross_daily_pending_ceiling,
)
```

and insert into `_gate` after the daily-rate check, before `return None`:

```python
        if would_cross_daily_pending_ceiling(
                pending_today=pending_in_window(rows, wall_now=now),
                new_worst_case=caps.per_trade, caps=caps):
            return REASON_DAILY_CEILING
```

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `17 passed`; full suite 0 failed (the S4.2 dormant predicate now has its consumer; `tests/test_ers_safety_daily_ceiling.py` stays green untouched).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/flow.py tests/test_ers_flow_gate.py && git commit -m "S4.7c: flow gate conservative daily-ceiling arm (per_trade headroom, pre-crossing)"'
```

---

### Task C8: The gate-through-verdict e2e

**Files:**
- Test: `tests/test_ers_flow_gate.py` (append; no production code â€” this composes C1â€“C7)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_gate_through_verdict_e2e_rate_cap_rejects_then_the_window_slides_open(tmp_path):
    # The whole S4.7c chain over REAL parts: a RUNNING SafetyController wired with a real
    # make_flow_gate over a real IntentStore journal (caps_provider = ctl.active_caps, exactly
    # the assembly binding). Two accepts already flowed this hour -> process_pending REJECTs
    # the next intent with rate_cap_hourly while op-state STAYS RUNNING and nothing is placed;
    # the wall clock advances past the window -> the next intent ACCEPTs with no operator
    # action. Kills: any wiring break in record_flow_event -> flow_log -> make_flow_gate ->
    # wire_flow_gate -> verdict -> process_pending's block_reason domination.
    from polybot.ers.flow import make_flow_gate
    wall = [1000.0]
    ctl_store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    try:
        ctl = SafetyController(caps=RiskCaps(), store=ctl_store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        with _store(str(tmp_path / "i.db")) as store:
            store.record_flow_event(kind="accept", token_id="a1", amount=Decimal("1"), wall_at=500.0)
            store.record_flow_event(kind="accept", token_id="a2", amount=Decimal("1"), wall_at=600.0)
            ctl.wire_flow_gate(make_flow_gate(store, ctl.active_caps, wall_clock=lambda: wall[0]))
            signer = PaperSigner()

            store.propose_trade("i1", **_P)
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i1").status == "REJECTED"
            assert store.get("i1").decision_reason == "rate_cap_hourly"
            assert ctl.state() == _safety.RUNNING      # blocked WITHOUT touching op-state
            assert signer.placed == []

            wall[0] = 4201.0   # newest accept age 3601 > 3600 -> the hourly window slid open
            store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="m2", event_id="e2"))
            process_pending(store, book_for={"t2": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i2").status == "ACCEPTED"
            assert [o["token_id"] for o in signer.placed] == ["t2"]
    finally:
        ctl_store.close()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” everything the e2e composes already exists, so RED is observed against a deliberate mutation (the repo's mutation-battery convention; pycache sweep after the revert):

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && sed -i "s/>= caps.new_positions_per_hour/> caps.new_positions_per_hour/" src/polybot/ers/flow.py && ./.venv/bin/pytest tests/test_ers_flow_gate.py::test_gate_through_verdict_e2e_rate_cap_rejects_then_the_window_slides_open -o addopts="" -q'
```
Expected: `1 failed` â€” `assert 'ACCEPTED' == 'REJECTED'` on i1 (the mutated `2 > 2` lets the 3rd position through; the e2e catches it end-to-end). Then revert + sweep + confirm green:

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git checkout -- src/polybot/ers/flow.py && find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
```
Expected: `18 passed`.

- [ ] **Step 3: Minimal implementation** â€” none. The e2e is the sub-slice acceptance pin over C1â€“C7; if it fails un-mutated, fix the offending prior task rather than adding code here.

- [ ] **Step 4: Task file green + FULL suite green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_flow_gate.py -o addopts="" -q'
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'
```
Expected: `18 passed` in the task file; full suite 0 failed (pre-S4.7c total + 18).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_flow_gate.py && git commit -m "S4.7c: gate-through-verdict e2e (rate_cap_hourly REJECT, state stays RUNNING, window slides)"'
```

---

I have everything verified. Drafting the plan fragment now.

## Sub-slice S4.7d: The loss breakers + the whole-slice e2e

**Preconditions (build order is serial aâ†’bâ†’câ†’d on `pol-6-s4.7-breakers`):** S4.7a (`flow_journal` + `record_flow_event`/`flow_log` + `make_flow_recorder`/`compose_sinks`/`accepts_in_window`/`pending_in_window`), S4.7b (`ers/ramp.py` + `SafetyController.swap_caps` + the `active_caps()` re-plumb in `run_cycle`), and S4.7c (`make_flow_gate` + `wire_flow_gate` + ALL nine new `REASON_*` constants in `safety.py`) are green and committed before D1 starts. Verify with a one-liner before beginning: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/python -c "from polybot.ers.flow import pending_in_window, make_flow_gate, make_flow_recorder, compose_sinks; from polybot.ers.ramp import step_daily, step_weekly; from polybot.ers.safety import REASON_WEEKLY_LOSS, REASON_CONSECUTIVE_LOSS, REASON_DAILY_PENDING_PAUSE, REASON_FLOW_DATA_ERROR, REASON_RAMP_DOWN; from polybot.ers.safety import SafetyController; assert hasattr(SafetyController, \"swap_caps\") and hasattr(SafetyController, \"wire_flow_gate\"); print(\"S4.7a-c OK\")"'` â€” if this fails, STOP: earlier sub-slices are not landed.

**Line-ref caveat:** `controller.py` refs below are against the pre-S4.7 file (S4.7b only changes the `caps=` arg of the `process_pending` call, currently `controller.py:79`). Anchor edits on the quoted code, not the line numbers.

**SACRED check for every task:** no edits to `validator.py`, `evaluate_intent`, `propose_trade`/`record_decision`/`record_op_event` bodies, `process_pending` signature/decision flow, `core/clock.py`, `heartbeat.py`, `supervisor.py`, `breaker.py`, `anomaly.py`, `gtd.py`, `reconcile.py`.

---

### Task D1: lossbreaker vocab + LossState unrepresentability + structural pin

**Files:**
- Create: `src/polybot/ers/lossbreaker.py`
- Create/Test: `tests/test_ers_lossbreaker.py`

- [ ] **Step 1: Write the failing test** â€” create `tests/test_ers_lossbreaker.py`:

```python
"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

LossState/LossBreakers over the durable flow_journal, the run_cycle consult (idempotent ramp
swaps in any op-state + edge-guarded sticky transitions + the weekly one-shot best-effort
cancel_all), and the DESIGN-S4.7 Â§8.3 whole-slice e2e. Clocks are injected 0-arg callables;
money is Decimal from string literals; helpers are copied per file per convention (no conftest).
"""

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore

_NOW = 1000000.0   # the injected wall-clock instant every direct-unit test evaluates at


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _breakers(store):
    from polybot.ers.lossbreaker import LossBreakers
    return LossBreakers(store=store, caps_provider=lambda: RiskCaps(),
                        wall_clock=lambda: _NOW)


def _realized(store, amount, *, age, token_id="t1"):
    # A realized-PnL journal row `age` seconds before _NOW (negative amount == a loss).
    store.record_flow_event(kind="realized", token_id=token_id, amount=Decimal(amount),
                            wall_at=_NOW - age)


def _accept_row(store, amount, *, age, token_id="t1"):
    # An accept-flow journal row (amount == the position's worst_case_risk).
    store.record_flow_event(kind="accept", token_id=token_id, amount=Decimal(amount),
                            wall_at=_NOW - age)


def test_lossbreaker_module_action_vocab_is_none_pause_halt_exact_strings():
    # Kills: changing any action constant's string (the controller compares by value).
    from polybot.ers import lossbreaker as _lb
    assert _lb.NONE == "NONE"
    assert _lb.PAUSE == "PAUSE"
    assert _lb.HALT == "HALT"


def test_loss_state_is_a_frozen_dataclass_with_action_triggers_and_ramp_steps():
    # Kills: dropping frozen=True or renaming action/triggers/ramp_steps.
    from polybot.ers.lossbreaker import HALT, LossState
    state = LossState(action=HALT, triggers=("weekly_loss_halt",), ramp_steps=("weekly",))
    assert state.action == "HALT"
    assert state.triggers == ("weekly_loss_halt",)
    assert state.ramp_steps == ("weekly",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.action = "NONE"


def test_loss_state_halt_with_empty_triggers_is_unrepresentable():
    # Kills: removing the __post_init__ guard -- a triggerless HALT reaches the controller's
    # triggers[0] as an IndexError at the exact moment the weekly breaker fires.
    from polybot.ers.lossbreaker import HALT, LossState
    with pytest.raises(ValueError):
        LossState(action=HALT, triggers=(), ramp_steps=())


def test_loss_state_pause_with_empty_triggers_is_unrepresentable():
    # Kills: narrowing the guard to HALT-only (the PAUSE path also indexes triggers[0]).
    from polybot.ers.lossbreaker import PAUSE, LossState
    with pytest.raises(ValueError):
        LossState(action=PAUSE, triggers=(), ramp_steps=())


def test_loss_state_none_with_empty_triggers_constructs_fine():
    # Boundary partner of the two tests above. Kills: over-widening the guard to NONE.
    from polybot.ers.lossbreaker import NONE, LossState
    state = LossState(action=NONE, triggers=(), ramp_steps=())
    assert state.triggers == ()


def test_lossbreaker_module_source_never_references_the_resume_state_or_set_state():
    # STICKY structural pin (DESIGN Â§6.2, mirrors the anomaly.py scan): nothing in
    # ers/lossbreaker.py may transition op-state or even NAME the resume state -- the ONLY
    # automatic HALTED->resume stays the clean boot-reconcile.
    # Kills: any auto-resume or op-state mutation creeping into the module.
    from polybot.ers import lossbreaker as _lb
    src = Path(_lb.__file__).read_text(encoding="utf-8")
    assert "set_state" not in src
    assert "RUNNING" not in src
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: all 6 tests ERROR with `ModuleNotFoundError: No module named 'polybot.ers.lossbreaker'`.

- [ ] **Step 3: Minimal implementation** â€” create `src/polybot/ers/lossbreaker.py` (NOTE: this module must NEVER contain the strings `set_state` or the all-caps resume-state name â€” not even in comments; the structural test enforces it):

```python
"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

AnomalyMonitor-shaped pure evidence producer over the durable flow_journal: evaluate() reads
store.flow_log() plus the injected caps_provider / wall_clock and returns an immutable
LossState. STICKY BY CONSUMER: this module only ever REPORTS; recovery is operator-owned, so
nothing here touches the op-state machine (structurally pinned). Windows are rolling
wall-clock seconds over wall_at -- the monotonic `at` column is never used for windowing.
"""

from dataclasses import dataclass

NONE = "NONE"
PAUSE = "PAUSE"
HALT = "HALT"


@dataclass(frozen=True)
class LossState:
    action: str        # NONE | PAUSE | HALT (HALT beats PAUSE)
    triggers: tuple    # reason strings, most-severe-first; () when NONE
    ramp_steps: tuple  # ("weekly",) / ("daily",) / both -- consumed by run_cycle for swap_caps

    def __post_init__(self):
        # The S4.4 AnomalyState lesson: the consumer indexes triggers[0] on the halt/pause
        # path, so an actionable state with no trigger is unrepresentable.
        if self.action in (PAUSE, HALT) and not self.triggers:
            raise ValueError(
                "PAUSE/HALT requires at least one trigger (the consumer indexes triggers[0])")
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 6 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures (660 pre-S4.7 baseline + all S4.7aâ€“c additions + these 6).

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D1: lossbreaker action vocab + frozen LossState (empty-trigger PAUSE/HALT unrepresentable) + no-resume structural pin"'`

---

### Task D2: LossBreakers skeleton â€” dormant NONE + fail-closed data error

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (append after `LossState`)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_evaluate_over_an_empty_journal_returns_none_the_shadow_data_gated_state(tmp_path):
    # DESIGN Â§7: realized rows don't exist until POL-4/S9, so in shadow the breakers evaluate
    # an empty set and stay NONE forever. Kills: any arm firing over zero rows.
    with _store(tmp_path) as store:
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()
        assert state.ramp_steps == ()


class _RaisingFlowLogStore:
    """A store whose flow_log raises -- corruption in OUR OWN safety ledger."""

    def flow_log(self):
        raise RuntimeError("journal corrupted")


def test_a_raising_flow_log_fails_closed_to_halt_with_flow_data_error(tmp_path):
    # DESIGN Â§6.4: a raising/malformed flow_journal read makes the breakers HALT with
    # flow_data_error -- never silent, never propagating. ramp_steps stays () (no blind
    # tightening off unreadable data). Kills: letting the raise escape evaluate, or
    # except-ing to NONE (which would let the loop keep trading on corrupt safety data).
    from polybot.ers.lossbreaker import LossBreakers
    breakers = LossBreakers(store=_RaisingFlowLogStore(), caps_provider=lambda: RiskCaps(),
                            wall_clock=lambda: _NOW)
    state = breakers.evaluate()   # must NOT raise
    assert state.action == "HALT"
    assert state.triggers == ("flow_data_error",)
    assert state.ramp_steps == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: 2 new tests fail with `ImportError: cannot import name 'LossBreakers'` (inside `_breakers`); the D1 six stay green.

- [ ] **Step 3: Minimal implementation** â€” in `src/polybot/ers/lossbreaker.py`, add the import below the existing `from dataclasses import dataclass` and append the class:

```python
from polybot.ers.safety import REASON_FLOW_DATA_ERROR
```

```python
class LossBreakers:
    """evaluate(frozen_tokens=...) -> LossState, once per controller cycle (consumed by
    ERSController AFTER the L5 anomaly consult). The fail-closed wrapper is the load-bearing
    frame: ANY raise inside the journal read + window math becomes the data-error halt."""

    def __init__(self, *, store, caps_provider, wall_clock):
        self._store = store
        self._caps_provider = caps_provider   # 0-arg -> RiskCaps (follows the ramp ratchet)
        self._wall_clock = wall_clock         # 0-arg -> float epoch seconds (windowing domain)

    def evaluate(self, *, frozen_tokens=frozenset()):
        try:
            return self._evaluate(frozen_tokens)
        except Exception:
            # FAIL CLOSED (DESIGN Â§6.4): corruption in our own safety ledger is never skipped
            # and never propagates -- it IS a halt. No ramp step off unreadable data.
            return LossState(HALT, (REASON_FLOW_DATA_ERROR,), ())

    def _evaluate(self, frozen_tokens):
        self._store.flow_log()   # the arms land in D3-D7; the read must happen (fail-closed)
        return LossState(NONE, (), ())
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 8 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D2: LossBreakers skeleton -- empty journal stays NONE; raising flow_log fails closed to HALT(flow_data_error)"'`

---

### Task D3: the weekly arm â€” $36 boundary pair + the 7d window edge pair

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (`_evaluate` body + imports)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append (losses are aged 100000s: inside the 7d window, OUTSIDE the 24h pending window, so only the weekly arm is in play; two rows keep the streak at 2 < 3):

```python
def test_weekly_losses_summing_to_exactly_36_do_not_halt(tmp_path):
    # Boundary pair, at-the-cap side: DESIGN row 71 is a STRICT > on weekly_loss_halt ($36).
    # Kills: >= instead of > on the weekly sum.
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100000.0)
        _realized(store, "-18", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()


def test_weekly_losses_summing_to_36_01_halt_with_the_weekly_trigger_and_ramp_step(tmp_path):
    # Boundary pair, just-over side: 36.01 > 36 -> HALT(weekly_loss_halt) + ramp step B.
    # Kills: dropping the weekly arm, wrong reason string, or forgetting the "weekly" step.
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100000.0)
        _realized(store, "-18.01", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt",)
        assert state.ramp_steps == ("weekly",)


def test_a_loss_exactly_at_the_7d_window_edge_is_included(tmp_path):
    # Window boundary pair, in side: now - wall_at == 604800 is INCLUSIVE (the breaker/ApiStorm
    # convention -- keeping the boundary row is tighter). Kills: < instead of <= on the edge.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=604800.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt",)


def test_a_loss_just_older_than_the_7d_window_is_excluded(tmp_path):
    # Window boundary pair, out side: age 604801 falls out of the weekly sum (and the single
    # trailing loss is a streak of 1 < 3, so nothing else fires). Kills: a windowless weekly
    # sum, or an off-by-one widening of the window.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=604801.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: `test_weekly_losses_summing_to_36_01...` and `test_a_loss_exactly_at_the_7d_window_edge...` fail with `AssertionError: assert 'NONE' == 'HALT'` (the skeleton always returns NONE); the two no-fire tests pass; everything else stays green.

- [ ] **Step 3: Minimal implementation** â€” add `from decimal import Decimal` and extend the safety import to `from polybot.ers.safety import REASON_FLOW_DATA_ERROR, REASON_WEEKLY_LOSS`; add the module constant `_WEEKLY_WINDOW_SECONDS = 604800` under the action vocab; replace `_evaluate` with:

```python
    def _evaluate(self, frozen_tokens):
        rows = self._store.flow_log()
        caps = self._caps_provider()
        now = self._wall_clock()
        realized = [r for r in rows if r["kind"] == "realized"]
        # Weekly arm (DECISIONS row 71): sum of |realized losses| in the rolling 7d wall
        # window, STRICT > (at-the-cap does not fire). INCLUSIVE old edge (<=).
        weekly_loss_total = sum(
            (abs(r["amount"]) for r in realized
             if r["amount"] < 0 and now - r["wall_at"] <= _WEEKLY_WINDOW_SECONDS),
            Decimal(0))
        if weekly_loss_total > caps.weekly_loss_halt:
            return LossState(HALT, (REASON_WEEKLY_LOSS,), ("weekly",))
        return LossState(NONE, (), ())
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 12 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D3: weekly realized-loss arm -- strict >36 boundary pair + inclusive 7d window edge pair + ramp step weekly"'`

---

### Task D4: the streak arm â€” 3-in-a-row boundary, win resets, NO time window

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (`_evaluate` + imports)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_two_trailing_losses_do_not_pause(tmp_path):
    # Streak boundary pair, under side: caps.consecutive_loss == 3. Kills: > vs >= confusion
    # lowering the threshold to 2.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_three_trailing_losses_pause_with_the_consecutive_trigger_and_no_ramp_step(tmp_path):
    # Streak boundary pair, at side: 3 >= 3 -> PAUSE(consecutive_loss). The streak arm carries
    # NO ramp step (only the weekly and pending arms tighten caps). Kills: dropping the streak
    # arm, wrong reason string, or attaching a ramp step to it.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("consecutive_loss",)
        assert state.ramp_steps == ()


def test_a_positive_win_mid_sequence_resets_the_streak(tmp_path):
    # The streak is the TRAILING run at the END of the realized sequence: 4 losses total but a
    # +1 win splits them into a trailing run of 2. Kills: counting ALL losses instead of the
    # trailing run (4 >= 3 would wrongly pause).
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_a_zero_amount_realized_row_counts_as_a_win_and_resets_the_streak(tmp_path):
    # amount >= 0 breaks the trail -- zero is the boundary value of "win". Kills: treating
    # amount <= 0 as a loss (a scratch exit would wrongly extend the streak).
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "0", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_the_streak_has_no_time_window_so_ancient_losses_still_count(tmp_path):
    # DESIGN row 72: the streak is windowless (only a WIN resets it). Losses far older than 7d
    # contribute nothing to the weekly sum yet still form the trailing streak. Kills: adding a
    # wall-clock window filter to the streak arm.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=10000000.0)
        _realized(store, "-1", age=10000000.0)
        _realized(store, "-1", age=10000000.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("consecutive_loss",)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: `test_three_trailing_losses...` and `test_the_streak_has_no_time_window...` fail with `AssertionError: assert 'NONE' == 'PAUSE'`; the reset/under-boundary tests pass; all else green.

- [ ] **Step 3: Minimal implementation** â€” extend the safety import to include `REASON_CONSECUTIVE_LOSS`; in `_evaluate`, insert between the weekly `if` and the final `return LossState(NONE, (), ())`:

```python
        # Streak arm (DECISIONS row 72): trailing consecutive losses at the END of the
        # realized sequence (flow order). NO time window -- only a win (amount >= 0) breaks it.
        streak = 0
        for row in reversed(realized):
            if row["amount"] < 0:
                streak += 1
            else:
                break
        if streak >= caps.consecutive_loss:
            return LossState(PAUSE, (REASON_CONSECUTIVE_LOSS,), ())
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 17 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D4: consecutive-loss streak arm -- trailing-run 2/3 boundary, +1 and 0 wins reset, windowless"'`

---

### Task D5: the pending arm â€” $24 boundary pair + ramp step "daily"

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (`_evaluate` + imports)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_pending_of_exactly_24_does_not_pause(tmp_path):
    # Pending-arm boundary pair, at side: pending_in_window == daily_pending_ceiling ($24) is
    # a STRICT >, so at-the-ceiling does not fire. Kills: >= on the pending comparison.
    with _store(tmp_path) as store:
        _accept_row(store, "12", age=100.0)
        _accept_row(store, "12", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_pending_of_24_01_pauses_with_daily_pending_pause_and_the_daily_ramp_step(tmp_path):
    # Pending-arm boundary pair, over side (rows 70-vs-72 interplay: only a REALIZED LOSS can
    # push pending past the gate-guarded ceiling -- here a $0.01 loss joins $24 of accepts).
    # 24.01 > 24 -> PAUSE(daily_pending_pause) + ramp step A ("daily"). Kills: dropping the
    # pending arm, wrong reason, or forgetting the "daily" step.
    with _store(tmp_path) as store:
        _accept_row(store, "12", age=100.0)
        _accept_row(store, "12", age=100.0)
        _realized(store, "-0.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("daily_pending_pause",)
        assert state.ramp_steps == ("daily",)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: `test_pending_of_24_01...` fails with `AssertionError: assert 'NONE' == 'PAUSE'`; the at-24 test passes; all else green.

- [ ] **Step 3: Minimal implementation** â€” add `from polybot.ers.flow import pending_in_window` and extend the safety import with `REASON_DAILY_PENDING_PAUSE`; in `_evaluate`, insert between the streak block and the final `return LossState(NONE, (), ())`:

```python
        # Pending arm (rows 70 vs 72 interplay): accepts + |realized losses| in the rolling
        # 24h window, via the shared pending_in_window helper. Fed the concatenated list of
        # ALL accept rows + the realized rows (the helper ignores wins and raises on
        # malformed rows -- the fail-closed wrapper converts that raise into the data halt).
        accepts = [r for r in rows if r["kind"] == "accept"]
        pending_today = pending_in_window(accepts + realized, wall_now=now)
        if pending_today > caps.daily_pending_ceiling:
            return LossState(PAUSE, (REASON_DAILY_PENDING_PAUSE,), ("daily",))
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 19 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D5: pending arm -- strict >24 boundary pair over accepts+|losses| in 24h + ramp step daily"'`

---

### Task D6: frozen-token exclusion across all three arms (accepts unaffected)

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (the `realized` list comprehension)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_frozen_token_losses_are_excluded_from_the_weekly_sum(tmp_path):
    # DECISIONS row 74: disputed/frozen tokens leave the realized counters (their PnL is not
    # yet real). The same -36.01 that halts in D3 is inert when its token is frozen.
    # Kills: dropping the frozen filter from the weekly sum.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=100000.0, token_id="tf")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_frozen_token_losses_are_excluded_from_the_streak(tmp_path):
    # Three trailing losses, the middle one frozen -> a filtered trailing run of 2 < 3.
    # Kills: filtering the weekly sum but streak-counting the unfiltered sequence.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0, token_id="t1")
        _realized(store, "-1", age=100000.0, token_id="tf")
        _realized(store, "-1", age=100000.0, token_id="t1")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_frozen_token_losses_are_excluded_from_the_pending_loss_component(tmp_path):
    # $20 accepts + a $10 frozen loss = pending 20 (not 30) -> under the $24 ceiling.
    # Kills: passing the UNfiltered realized list to pending_in_window.
    with _store(tmp_path) as store:
        _accept_row(store, "20", age=100.0, token_id="t1")
        _realized(store, "-10", age=100.0, token_id="tf")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_accept_rows_on_a_frozen_token_still_count_toward_pending(tmp_path):
    # Row 74's exclusion is for REALIZED counters only -- frozen positions still count toward
    # open/pending flow. $20 frozen-token accept + $10 live loss = pending 30 > 24 -> PAUSE.
    # Kills: over-widening the frozen filter to the accept rows.
    with _store(tmp_path) as store:
        _accept_row(store, "20", age=100.0, token_id="tf")
        _realized(store, "-10", age=100.0, token_id="t1")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "PAUSE"
        assert state.triggers == ("daily_pending_pause",)
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: the three exclusion tests fail (`assert 'HALT' == 'NONE'` / `assert 'PAUSE' == 'NONE'` â€” the arms fire despite `frozen_tokens`); `test_accept_rows_on_a_frozen_token...` passes; all else green.

- [ ] **Step 3: Minimal implementation** â€” in `_evaluate`, change the `realized` comprehension to filter frozen tokens (the accepts list is deliberately NOT filtered):

```python
        # Frozen exclusion (DECISIONS row 74): disputed/frozen tokens leave the realized
        # counters entirely (weekly, streak, AND the pending loss component); accept rows are
        # NOT filtered -- frozen positions still count toward pending/open flow.
        realized = [r for r in rows
                    if r["kind"] == "realized" and r["token_id"] not in frozen_tokens]
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 23 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D6: frozen-token exclusion from weekly+streak+pending-loss counters; accept flow unaffected (row 74)"'`

---

### Task D7: severity ordering â€” HALT beats PAUSE, all triggers collected, ramp_steps ("weekly","daily")

**Files:**
- Modify: `src/polybot/ers/lossbreaker.py` (restructure `_evaluate` from early-return to collect-all)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_weekly_and_pending_both_firing_halt_with_both_triggers_and_both_ramp_steps(tmp_path):
    # Losses inside 24h: weekly sum 36.01 > 36 AND pending 36.01 > 24 (streak 2 < 3 stays
    # quiet). HALT beats PAUSE; triggers most-severe-first; ramp_steps ordered
    # ("weekly", "daily") deduped. Kills: the early-return implementation that reports only
    # the first firing arm (the consumer would miss the daily tightening + the audit detail
    # would under-report provenance).
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100.0)
        _realized(store, "-18.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt", "daily_pending_pause")
        assert state.ramp_steps == ("weekly", "daily")


def test_all_three_arms_firing_order_triggers_most_severe_first(tmp_path):
    # Three losses inside 24h: weekly 36.01 > 36, streak 3 >= 3, pending 36.01 > 24. Pinned
    # severity order (weekly_loss_halt, consecutive_loss, daily_pending_pause); ramp_steps
    # stay ("weekly", "daily") -- the streak arm never adds a step. Kills: any reordering of
    # the trigger tuple, or dedupe loss on ramp_steps.
    with _store(tmp_path) as store:
        _realized(store, "-12", age=100.0)
        _realized(store, "-12", age=100.0)
        _realized(store, "-12.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt", "consecutive_loss", "daily_pending_pause")
        assert state.ramp_steps == ("weekly", "daily")
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: both new tests fail with `AssertionError: assert ('weekly_loss_halt',) == ('weekly_loss_halt', 'daily_pending_pause')` (the early-return weekly arm masks the rest); all else green.

- [ ] **Step 3: Minimal implementation** â€” replace `_evaluate` in full with the collect-all final form:

```python
    def _evaluate(self, frozen_tokens):
        rows = self._store.flow_log()
        caps = self._caps_provider()
        now = self._wall_clock()
        # Frozen exclusion (DECISIONS row 74): disputed/frozen tokens leave the realized
        # counters entirely (weekly, streak, AND the pending loss component); accept rows are
        # NOT filtered -- frozen positions still count toward pending/open flow.
        realized = [r for r in rows
                    if r["kind"] == "realized" and r["token_id"] not in frozen_tokens]
        triggers = []
        ramp_steps = []
        # Arm 1 (most severe -- DECISIONS row 71): |realized losses| over the rolling 7d wall
        # window, STRICT >, INCLUSIVE old edge.
        weekly_loss_total = sum(
            (abs(r["amount"]) for r in realized
             if r["amount"] < 0 and now - r["wall_at"] <= _WEEKLY_WINDOW_SECONDS),
            Decimal(0))
        weekly_fired = weekly_loss_total > caps.weekly_loss_halt
        if weekly_fired:
            triggers.append(REASON_WEEKLY_LOSS)
            ramp_steps.append("weekly")
        # Arm 2 (row 72): trailing consecutive losses at the END of the realized sequence
        # (flow order). NO time window -- only a win (amount >= 0) breaks the trail.
        streak = 0
        for row in reversed(realized):
            if row["amount"] < 0:
                streak += 1
            else:
                break
        if streak >= caps.consecutive_loss:
            triggers.append(REASON_CONSECUTIVE_LOSS)
        # Arm 3 (rows 70 vs 72 interplay): accepts + |realized losses| in the rolling 24h
        # window via the shared helper (ignores wins; raises on malformed rows -- the
        # fail-closed wrapper converts that raise into the data halt).
        accepts = [r for r in rows if r["kind"] == "accept"]
        pending_today = pending_in_window(accepts + realized, wall_now=now)
        if pending_today > caps.daily_pending_ceiling:
            triggers.append(REASON_DAILY_PENDING_PAUSE)
            ramp_steps.append("daily")
        if not triggers:
            return LossState(NONE, (), ())
        action = HALT if weekly_fired else PAUSE
        return LossState(action, tuple(triggers), tuple(ramp_steps))
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 25 passed (D3â€“D6 tests must ALL stay green against the restructure).
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/lossbreaker.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D7: collect-all evaluate -- HALT beats PAUSE, triggers most-severe-first, ramp_steps (weekly,daily) deduped"'`

---

### Task D8: the ERSController lossbreakers= seam (ctor + consult + frozen plumb; None == today)

**Files:**
- Modify: `src/polybot/ers/controller.py` â€” ctor signature (currently `:22-23`), seam assignment after `self._anomaly = anomaly` (currently `:42`), consult insertion after the anomaly block / before the `process_pending` call (currently between `:77` and `:78`; anchor on the quoted code â€” S4.7b has already re-plumbed the `caps=` arg)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append the controller-wiring helper block + tests:

```python
# --- ERSController lossbreakers= seam (the run_cycle consult wiring) ---------------------------
from polybot.ers import safety as _safety
from polybot.ers.controller import ERSController
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _loss_state(action, triggers=(), ramp_steps=()):
    from polybot.ers.lossbreaker import LossState
    return LossState(action=action, triggers=triggers, ramp_steps=ramp_steps)


class _LossDouble:
    """Duck-typed LossBreakers double (.evaluate(frozen_tokens=...) -> LossState) recording
    the frozen_tokens it was consulted with; mutable so the sticky tests can CLEAR it."""

    def __init__(self, state):
        self.state = state
        self.frozen_seen = []

    def evaluate(self, *, frozen_tokens=frozenset()):
        self.frozen_seen.append(frozen_tokens)
        return self.state


def _rc(store, ctl, signer, *, lossbreakers=None):
    return ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                         signer=signer, controller=ctl, lossbreakers=lossbreakers,
                         clock=lambda: 0)


def test_a_none_action_lossbreakers_is_consulted_but_the_cycle_trades_exactly_as_today(tmp_path):
    # The seam exists and is consulted once per cycle (with the empty frozen set for an empty
    # portfolio), and a NONE state changes nothing: the intent ACCEPTs, no cancel_all, no
    # caps_swap, only the setup state_change in op_audit. Kills: making the seam mandatory,
    # forgetting the consult, or acting on a NONE state.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("NONE"))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert double.frozen_seen == [frozenset()]
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_lossbreakers_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Dormant-by-default: an ERSController WITHOUT the lossbreakers kwarg trades exactly as
    # before S4.7d. Expected GREEN from birth (pins the None default; the full-suite baseline
    # is the wider proof). Kills: consulting/acting when the seam is None.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)  # lossbreakers unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_frozen_position_tokens_are_plumbed_into_the_consult(tmp_path):
    # run_cycle feeds evaluate(frozen_tokens=...) the token_ids of FROZEN positions only
    # (row 74's live-Portfolio filter). Direct _portfolio assignment mimics the S4.5
    # boot-reconcile rebuild. Kills: passing all tokens, or never passing frozen ones.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot HALTED ok
        signer = PaperSigner()
        double = _LossDouble(_loss_state("NONE"))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc._portfolio = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m9", event_id="e9", resolution_source="s9",
                         cluster_id="c9", worst_case_risk=Decimal("8"), matrix_cold=False,
                         token_id="t9", entry_price=Decimal("0.50"), frozen=True),
            OpenPosition(condition_id="m8", event_id="e8", resolution_source="s8",
                         cluster_id="c8", worst_case_risk=Decimal("8"), matrix_cold=False,
                         token_id="t8", entry_price=Decimal("0.50"), frozen=False),
        ))
        rc.run_cycle()
        assert double.frozen_seen == [frozenset({"t9"})]
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: tests 1 and 3 fail with `TypeError: ERSController.__init__() got an unexpected keyword argument 'lossbreakers'`; test 2 passes (green-from-birth pin â€” probe it in Step 4).

- [ ] **Step 3: Minimal implementation** â€” three edits to `src/polybot/ers/controller.py`:

Edit 1 â€” ctor signature (currently `:22-23`):
```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 clock):
```

Edit 2 â€” after `self._anomaly = anomaly` (currently `:42`):
```python
        self._anomaly = anomaly
        # lossbreakers (S4.7d seam): the opt-in realized-loss breakers consulted each cycle
        # AFTER the L5 anomaly block. lossbreakers=None (the default) == today byte-for-byte.
        self._lossbreakers = lossbreakers
```

Edit 3 â€” in `run_cycle`, immediately after the anomaly block's closing `record_op_event(... f"FAILED: {exc}")` lines and BEFORE `self._portfolio = process_pending(`:
```python
        if self._lossbreakers is not None:
            # S4.7d: realized-loss breakers, consulted every cycle. Frozen positions (row 74)
            # are excluded from the realized counters via the live Portfolio's frozen flags.
            frozen = frozenset(p.token_id for p in self._portfolio.positions if p.frozen)
            self._lossbreakers.evaluate(frozen_tokens=frozen)
```

- [ ] **Step 4: Task file green + FULL suite green + probe the green-from-birth pin**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 28 passed.
  - Mutation probe for test 2: temporarily change `if self._lossbreakers is not None:` to `if True:` â†’ run `.../.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q -k lossbreakers_none_default` â†’ expect `AttributeError: 'NoneType' object has no attribute 'evaluate'`. Revert, then sweep: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && find src tests -name __pycache__ -prune -exec rm -rf {} +'`
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D8: ERSController lossbreakers= seam -- per-cycle consult with live frozen-token plumb; None default == today"'`

---

### Task D9: ramp swaps applied from ramp_steps in ANY op-state, idempotently

**Files:**
- Modify: `src/polybot/ers/controller.py` (imports currently `:16-18`; the D8 consult block)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def _daily_swap_detail():
    from polybot.ers.ramp import step_daily
    return RiskCaps().content_hash()[:16] + "->" + step_daily(RiskCaps()).content_hash()[:16]


def _weekly_swap_detail():
    from polybot.ers.ramp import step_weekly
    return RiskCaps().content_hash()[:16] + "->" + step_weekly(RiskCaps()).content_hash()[:16]


def test_ramp_steps_tighten_active_caps_even_on_a_halted_loop_with_a_caps_swap_audit_row(tmp_path):
    # DESIGN Â§2/Â§6.7: swaps are applied from ls.ramp_steps in ANY op-state (tightening while
    # halted is harmless and desirable) -- and via SafetyController.swap_caps, so the audit
    # row carries reason=ramp_down and the old->new hash detail. A PAUSE verdict on a
    # boot-HALTED loop must NOT transition state (edge guard) but MUST still tighten.
    # Kills: gating the swap loop on op-state, wiring step_daily to the "weekly" key (or vice
    # versa), or bypassing swap_caps (no audit row).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("daily_pending_pause",), ("daily",)))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED                       # no downgrade, no upgrade
        assert ctl.active_caps().per_trade == Decimal("9")         # step A bit
        assert ctl.active_caps().total_open_risk == Decimal("45")
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("caps_swap", "ramp_down", _daily_swap_detail()),
        ]


def test_reapplying_the_same_ramp_step_next_cycle_is_a_hash_identical_no_op(tmp_path):
    # Idempotent swaps (DESIGN Â§6.7): the second cycle's step_daily(min'd caps) is
    # hash-identical -> swap_caps returns False -> NO second audit row, caps unchanged.
    # Kills: audit spam on re-application, or a step that keeps compounding.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("daily_pending_pause",), ("daily",)))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        rc.run_cycle()
        assert ctl.active_caps().per_trade == Decimal("9")
        assert len([r for r in store.op_audit_log() if r["kind"] == "caps_swap"]) == 1
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: both fail with `AssertionError` on `active_caps().per_trade` (`Decimal('12') == Decimal('9')` â€” the D8 consult ignores ramp_steps); all else green.

- [ ] **Step 3: Minimal implementation** â€” in `controller.py`: add `from polybot.ers.ramp import step_daily, step_weekly` below the anomaly import, extend the safety import line to `from polybot.ers.safety import HALTED, PAUSED, REASON_RAMP_DOWN, RUNNING`, and grow the D8 consult block:

```python
        if self._lossbreakers is not None:
            # S4.7d: realized-loss breakers, consulted every cycle. Frozen positions (row 74)
            # are excluded from the realized counters via the live Portfolio's frozen flags.
            frozen = frozenset(p.token_id for p in self._portfolio.positions if p.frozen)
            ls = self._lossbreakers.evaluate(frozen_tokens=frozen)
            for step in ls.ramp_steps:
                # Idempotent tighten-only ratchet (DESIGN Â§6.7): applied in ANY op-state --
                # re-application is a hash-identical no-op inside swap_caps (no audit spam),
                # and tightening while halted is harmless and desirable.
                step_fn = step_weekly if step == "weekly" else step_daily
                self._controller.swap_caps(step_fn(self._controller.active_caps()),
                                           reason=REASON_RAMP_DOWN)
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 30 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D9: run_cycle applies ramp_steps via swap_caps in any op-state -- audited once, idempotent on re-application"'`

---

### Task D10: the weekly HALT path â€” halt-first, ONE best-effort cancel_all, exact audit rows

**Files:**
- Modify: `src/polybot/ers/controller.py` (the consult block + one import)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append (mirrors `tests/test_ers_anomaly.py`'s `_StateSnoopingSigner`/`_RaisingCancelSigner` idioms, copied per file):

```python
class _StateSnoopingSigner(PaperSigner):
    """PaperSigner recording the op-state AT THE MOMENT cancel_all is called -- proves the
    gate closed (HALTED) BEFORE the de-risk fired."""

    def __init__(self, ctl):
        super().__init__()
        self._ctl = ctl
        self.state_at_cancel = []

    def cancel_all(self):
        self.state_at_cancel.append(self._ctl.state())
        super().cancel_all()


def test_loss_halt_from_running_swaps_then_halts_first_then_cancels_once_with_exact_rows(tmp_path):
    # DESIGN Â§2 step 3: swaps FIRST (any op-state), then the edge-guarded halt (set_state
    # audits it), THEN exactly ONE cancel_all with reason=triggers[0] and
    # detail=",".join(triggers). The daily step composes into the weekly one (min'd) so only
    # ONE caps_swap row appears. Kills: swapping the halt/cancel order (state_at_cancel would
    # read the live state), double-firing cancel_all, wrong reason/detail strings, or
    # applying the swaps after the halt (row order).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = _StateSnoopingSigner(ctl)
        double = _LossDouble(_loss_state(
            "HALT", ("weekly_loss_halt", "daily_pending_pause"), ("weekly", "daily")))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert signer.state_at_cancel == [_safety.HALTED]   # already closed at cancel time
        assert len(signer.cancelled_all) == 1
        assert ctl.active_caps().per_trade == Decimal("6")  # step B bit
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("caps_swap", "ramp_down", _weekly_swap_detail()),
            ("state_change", "weekly_loss_halt", _safety.HALTED),
            ("cancel_all", "weekly_loss_halt", "weekly_loss_halt,daily_pending_pause"),
        ]


def test_loss_halt_escalates_a_paused_loop_to_halted_with_the_one_shot(tmp_path):
    # PAUSED is a LIVE loop -- a weekly loss halt must still escalate it (edge guard is
    # (RUNNING, PAUSED), the S4.4 doctrine). Kills: over-tightening the guard to
    # RUNNING-only.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("HALT", ("weekly_loss_halt",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
        assert ("cancel_all", "weekly_loss_halt") in [
            (r["kind"], r["reason"]) for r in store.op_audit_log()]


class _RaisingCancelSigner(PaperSigner):
    """cancel_all raises (venue/RPC down at the worst moment): the halt must already be in
    place and must SURVIVE; the failure is audited; the cycle continues."""

    def cancel_all(self):
        raise RuntimeError("venue rejected cancelAll")


def test_raising_cancel_all_is_audited_failed_and_never_unwinds_the_loss_halt_or_the_cycle(tmp_path):
    # The S4.4 pattern verbatim: gate closed FIRST, the failure lands in op_audit as
    # detail="FAILED: ...", and process_pending still runs (the pending intent REJECTs under
    # the stored weekly_loss_halt reason; the standing GTD exits are the backstop).
    # Kills: letting the exception propagate out of run_cycle (the S4.3 supervisor would
    # SIGKILL a healthy loop), or auditing an unconditional success detail.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = _RaisingCancelSigner()
        double = _LossDouble(_loss_state("HALT", ("weekly_loss_halt",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()                                   # must NOT raise
        assert ctl.state() == _safety.HALTED             # the halt held
        cancel_rows = [r for r in store.op_audit_log() if r["kind"] == "cancel_all"]
        assert len(cancel_rows) == 1
        assert cancel_rows[0]["reason"] == "weekly_loss_halt"
        assert cancel_rows[0]["detail"] == "FAILED: venue rejected cancelAll"
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "weekly_loss_halt"
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: all 3 fail with `AssertionError: assert 'RUNNING' == 'HALTED'` (respectively `'PAUSED' == 'HALTED'`) â€” the consult applies swaps but has no action branch yet.

- [ ] **Step 3: Minimal implementation** â€” add `from polybot.ers.lossbreaker import HALT as LOSS_HALT` below the anomaly import in `controller.py` (the existing `from polybot.ers.anomaly import HALT` stays as-is), and append inside the `if self._lossbreakers is not None:` block, after the swap loop:

```python
            if ls.action == LOSS_HALT and self._controller.state() in (RUNNING, PAUSED):
                # EDGE-triggered halt-first one-shot (the S4.4 pattern verbatim): close the
                # gate, THEN one best-effort cancel_all; a raising signer never unwinds the
                # halt or kills the cycle -- the pre-staged GTD exits are the backstop.
                self._controller.set_state(HALTED, reason=ls.triggers[0])
                try:
                    self._signer.cancel_all()
                    self._store.record_op_event(kind="cancel_all", reason=ls.triggers[0],
                                                detail=",".join(ls.triggers))
                except Exception as exc:
                    self._store.record_op_event(kind="cancel_all", reason=ls.triggers[0],
                                                detail=f"FAILED: {exc}")
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 33 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D10: weekly loss HALT -- swaps first, halt-first edge-triggered one-shot best-effort cancel_all with exact audit rows"'`

---

### Task D11: the PAUSE edges â€” RUNNING-only, no re-audit, never a downgrade

**Files:**
- Modify: `src/polybot/ers/controller.py` (the consult block + import extension)
- Test: `tests/test_ers_lossbreaker.py` (append)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_loss_pause_from_running_sets_paused_with_no_cancel_all(tmp_path):
    # DESIGN row 72/Fork 4: consecutive-loss PAUSE is sticky but NOT a de-risk -- set_state
    # only, no cancel_all, and the streak arm carries no ramp step. Kills: dropping the PAUSE
    # branch, or wiring a de-risk onto it.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.PAUSED
        assert signer.cancelled_all == []
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("state_change", "consecutive_loss", _safety.PAUSED),
        ]


def test_a_paused_loop_hit_by_a_pause_verdict_again_does_not_re_audit(tmp_path):
    # EDGE-triggered: the breakers evaluate every cycle, but a still-firing PAUSE on an
    # already-PAUSED loop appends nothing (no audit spam). Kills: level-triggered set_state
    # re-firing every cycle.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        rc.run_cycle()
        assert ctl.state() == _safety.PAUSED
        assert [r["kind"] for r in store.op_audit_log()].count("state_change") == 2


def test_a_halted_loop_is_never_downgraded_by_a_pause_verdict(tmp_path):
    # Severity/precedence (DESIGN Â§3): the loss consult never downgrades -- PAUSE fires from
    # the live state only, so a boot-HALTED loop stays HALTED with an untouched audit log.
    # Kills: widening the PAUSE edge guard to HALTED (a silent halt->pause downgrade would
    # REOPEN a killed loop to a weaker block).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert store.op_audit_log() == []
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'`
  - Expected: tests 1 and 2 fail with `AssertionError: assert 'RUNNING' == 'PAUSED'` (no PAUSE branch yet); test 3 passes (it pins the guard the implementation must not widen).

- [ ] **Step 3: Minimal implementation** â€” extend the lossbreaker import in `controller.py` to `from polybot.ers.lossbreaker import HALT as LOSS_HALT, PAUSE as LOSS_PAUSE`, and append the `elif` to the D10 branch (final consult-block form):

```python
            elif ls.action == LOSS_PAUSE and self._controller.state() == RUNNING:
                # Sticky pause (Fork 4): the streak counter resets on a win; the PAUSED
                # op-state does NOT -- recovery is operator RESUME. Fires from the live
                # trading state only (never downgrades a halt; never re-audits a pause).
                self._controller.set_state(PAUSED, reason=ls.triggers[0])
```

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 36 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_lossbreaker.py && git commit -m "S4.7d D11: consecutive-loss PAUSE -- edge-guarded from the live state only, no de-risk, no re-audit, never downgrades a halt"'`

---

### Task D12: sticky â€” a cleared loss state never resumes the loop

**Files:**
- Test: `tests/test_ers_lossbreaker.py` (append; no production change â€” this is a pin with a mandatory mutation probe)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_loss_halt_is_sticky_after_the_losses_clear_and_the_next_intent_rejects_with_it(tmp_path):
    # Fork 4 / DESIGN Â§6.2 STICKY: the loss state CLEARING (next cycle evaluates NONE) does
    # not resume the loop -- op-state stays HALTED with the stored weekly_loss_halt reason, a
    # fresh intent REJECTs with it verbatim, and the one-shot stayed one-shot. Recovery is
    # operator-owned. Kills: ANY auto-resume branch in the run_cycle consult (the Step-2
    # mutation probe proves this test catches exactly that).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        double = _LossDouble(_loss_state("HALT", ("weekly_loss_halt",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()                                   # cycle 1: halt + one-shot de-risk
        assert ctl.state() == _safety.HALTED

        double.state = _loss_state("NONE")               # the losses CLEAR...
        store.propose_trade("i1", **_P)                  # ...and a fresh intent arrives
        rc.run_cycle()                                   # cycle 2

        assert ctl.state() == _safety.HALTED             # ...but the halt is STICKY
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "weekly_loss_halt"
        assert len(signer.cancelled_all) == 1            # the one-shot stayed one-shot
        assert [r["kind"] for r in store.op_audit_log()].count("state_change") == 2
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” this pin is expected GREEN from birth (the D10/D11 edge guards already make halts sticky), so its killing power is proven by a mutation probe instead:
  - First confirm green: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q -k sticky'` â†’ 1 passed.
  - PROBE: in `controller.py`, temporarily append an auto-resume `else` to the action chain: `else:` / `    if self._controller.state() == HALTED:` / `        self._controller.set_state(RUNNING, reason="auto_resume")` â€” rerun the `-k sticky` command â†’ expect `AssertionError: assert 'RUNNING' == 'HALTED'`.
  - REVERT the probe, then sweep: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && find src tests -name __pycache__ -prune -exec rm -rf {} +'` â†’ rerun `-k sticky` â†’ 1 passed.

- [ ] **Step 3: Minimal implementation** â€” none (the pin holds against the already-final consult block; the probe in Step 2 is the RED evidence).

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 37 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures.

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_lossbreaker.py && git commit -m "S4.7d D12: sticky pin -- cleared losses never auto-resume; the halted loop rejects with weekly_loss_halt verbatim"'`

---

### Task D13: THE Â§8.3 WHOLE-SLICE E2E â€” gate â†’ slide â†’ weekly halt â†’ ramp â†’ sticky

**Files:**
- Test: `tests/test_ers_lossbreaker.py` (append; no production change â€” acceptance gate with a mandatory 3-probe mutation battery)

- [ ] **Step 1: Write the failing test** â€” append:

```python
def test_s4_7_whole_slice_e2e_rate_gate_slide_weekly_halt_ramp_and_sticky_reject(tmp_path):
    # DESIGN-S4.7 Â§8.3: the whole slice assembled -- flow recorder composed onto the fill
    # sink, the flow gate wired into verdict, the loss breakers + ramp in run_cycle.
    # Kills: cross-module mis-wiring invisible to the unit tests (gate never consulted,
    # recorder not journaling, ramp not biting active_caps, halt not sticky) -- see the
    # Step-2 probe battery.
    from polybot.ers.flow import compose_sinks, make_flow_gate, make_flow_recorder
    from polybot.ers.lossbreaker import LossBreakers
    from polybot.ers.service import make_fill_sink

    wall = [1000.0]                      # the injected, advanceable wall clock (epoch seconds)
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        ctl.wire_flow_gate(make_flow_gate(store, ctl.active_caps, wall_clock=lambda: wall[0]))
        signer = PaperSigner()
        books = {"t1": _book("0.50"), "t2": _book("0.50"), "t3": _book("0.50"),
                 "t4": _book("0.50")}
        rc = ERSController(
            store=store, book_for=books.get, caps=RiskCaps(), signer=signer, controller=ctl,
            fill_sink=compose_sinks(make_fill_sink(store),
                                    make_flow_recorder(store, wall_clock=lambda: wall[0])),
            lossbreakers=LossBreakers(store=store, caps_provider=ctl.active_caps,
                                      wall_clock=lambda: wall[0]),
            clock=lambda: 0)

        # Phase 1: two accepts flow; the recorder journals each ($12 worst-case at ask 0.50).
        store.propose_trade("i1", **_P)
        rc.run_cycle()
        store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="m2", event_id="e2"))
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i2").status == "ACCEPTED"
        assert [r["kind"] for r in store.flow_log()] == ["accept", "accept"]

        # Phase 2: the 3rd intent inside the hour REJECTs rate_cap_hourly -- the gate blocks
        # WITHOUT touching op-state.
        store.propose_trade("i3", **dict(_P, token_id="t3", condition_id="m3", event_id="e3"))
        rc.run_cycle()
        assert store.get("i3").status == "REJECTED"
        assert store.get("i3").decision_reason == "rate_cap_hourly"
        assert ctl.state() == _safety.RUNNING

        # Phase 3: the wall clock slides past BOTH windows and flow resumes with an ACCEPT.
        # (24h+, not just 1h+: the two $12 accepts hold pending AT the $24 ceiling, so the
        # conservative daily gate would keep blocking until they age out of the 24h window.)
        wall[0] = 1000.0 + 86401.0
        store.propose_trade("i4", **dict(_P, token_id="t4", condition_id="m4", event_id="e4"))
        rc.run_cycle()
        assert store.get("i4").status == "ACCEPTED"

        # Phase 4: realized losses cross the $36 weekly halt (streak 2 < 3: the weekly arm,
        # not the streak arm; they also push pending over $24, so step A rides along and
        # composes into step B -> exactly ONE caps_swap row).
        store.record_flow_event(kind="realized", token_id="t1", amount=Decimal("-18"),
                                wall_at=wall[0])
        store.record_flow_event(kind="realized", token_id="t2", amount=Decimal("-18.01"),
                                wall_at=wall[0])
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
        assert ctl.active_caps().per_trade == Decimal("6")        # step B bit active_caps
        assert ctl.active_caps().total_open_risk == Decimal("30")
        swap_rows = [r for r in store.op_audit_log() if r["kind"] == "caps_swap"]
        assert [(r["reason"], r["detail"]) for r in swap_rows] == [
            ("ramp_down", _weekly_swap_detail())]
        cancel_rows = [r for r in store.op_audit_log() if r["kind"] == "cancel_all"]
        assert [(r["reason"], r["detail"]) for r in cancel_rows] == [
            ("weekly_loss_halt", "weekly_loss_halt,daily_pending_pause")]

        # Phase 5: sticky -- the halted loop never re-fires (still-firing breakers, no new
        # rows) and a fresh intent REJECTs with the stored weekly_loss_halt reason.
        store.propose_trade("i5", **dict(_P, token_id="t1", condition_id="m5", event_id="e5"))
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert store.get("i5").status == "REJECTED"
        assert store.get("i5").decision_reason == "weekly_loss_halt"
        assert len(signer.cancelled_all) == 1
        assert len([r for r in store.op_audit_log() if r["kind"] == "caps_swap"]) == 1
```

- [ ] **Step 2: Run it, watch it FAIL for the right reason** â€” the assembly is complete by D11, so this is expected GREEN from birth; its killing power is proven by the mandatory 3-probe battery (apply â†’ observe the exact failure â†’ revert â†’ pycache sweep â†’ re-green after EACH probe; sweep command: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && find src tests -name __pycache__ -prune -exec rm -rf {} +'`; run command for all probes: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q -k whole_slice_e2e'`):
  - First confirm green â†’ 1 passed.
  - PROBE A (ramp never bites): in `controller.py` change `for step in ls.ramp_steps:` to `for step in ():` â†’ expect `AssertionError` at `ctl.active_caps().per_trade == Decimal("6")` (still 12). Revert + sweep.
  - PROBE B (gate never consulted): in `safety.py` verdict's RUNNING branch change `if self._flow_gate is not None:` to `if False:` â†’ expect `AssertionError` at `store.get("i3").status == "REJECTED"` (i3 ACCEPTED). Revert + sweep.
  - PROBE C (halt edge guard broken): in `controller.py` change `in (RUNNING, PAUSED)` to `in (PAUSED,)` on the LOSS_HALT line â†’ expect `AssertionError` at `ctl.state() == _safety.HALTED` after Phase 4 (still RUNNING). Revert + sweep.

- [ ] **Step 3: Minimal implementation** â€” none (acceptance gate; Probes Aâ€“C are the RED evidence).

- [ ] **Step 4: Task file green + FULL suite green**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_lossbreaker.py -o addopts="" -q'` â†’ 38 passed.
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q --tb=no'` â†’ 0 failures (660 pre-S4.7 baseline + S4.7aâ€“c + 38 from this file).

- [ ] **Step 5: Commit**
  - `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_ers_lossbreaker.py && git commit -m "S4.7d D13: whole-slice e2e -- rate gate blocks then slides, weekly halt one-shots + ramps active_caps to per_trade 6, sticky reject"'`

---

**Post-slice notes for the executor:** (1) The `record_op_event` docstring kind-set growth (`caps_swap`) belongs to S4.7b's swap_caps task â€” verify it landed; if not, add the docstring word there, never touching the method body. (2) The two-stage review (spec-compliance + pinned-opus with mutation batteries) runs after D13 per DESIGN Â§8.4; the probes above are per-task spot-checks, not a substitute. (3) `lossbreaker.py` must stay free of the literal strings `set_state` and the resume-state name â€” re-run the structural test after ANY edit to the module, including comment-only ones.