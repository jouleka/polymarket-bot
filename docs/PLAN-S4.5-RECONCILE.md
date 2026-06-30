# S4.5 Three-Way Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan
> task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Give the ERS an independent three-way reconcile that HALTS rather than trade whenever its own belief of what it holds diverges from the authoritative on-chain truth.

**Architecture:** A durable append-only `fills` ledger (the internal leg) feeds a pure `ThreeWayReconciler` that folds internal/CLOB/on-chain balances per `token_id` and a `RestartReconciler` that drives the boot HALTED→RUNNING transition — all shadow on `PaperSigner`. Everything wires around the sacred chokepoints (`evaluate_intent`, the validator, `propose_trade`'s INSERT-only path, `process_pending` steps 1-3) via additive `None`-default keyword seams (the `controller=`/`breaker=`/`pipeline=`/`gtd_for=`/`fill_sink=` pattern) plus new modules.

**Tech Stack:** Python 3.13, sqlite3, Decimal, pytest; strict TDD.

**Baseline:** 517 tests green; branch `pol-6-s4.5-reconcile` (design doc already committed at `ca82052`).

---

## Pinned new-symbol contract

```
# ============ PINNED NEW-SYMBOL CONTRACT - S4.5 (single source of truth; do NOT deviate) ============
# Sacred & UNCHANGED: ers/validator.py evaluate_intent + the validator; intent_store.propose_trade INSERT-only
# chokepoint; service.process_pending steps 1-3 / block_reason precedence. Extend ONLY via additive
# None-defaulting keyword seams (the controller=/breaker=/pipeline=/gtd_for= pattern) + new modules.

## caps.py  (S4.5d) - RiskCaps gains ONE field (additive/frozen/_verify-checked/content_hash-covered):
reconcile_settle_window_seconds: int = 90
#   _verify: must be > 0 (ADD its name to the existing strictly-positive-int loop alongside
#   clock_skew_tolerance_seconds etc). Tighten-only = a future ratchet may only DECREASE it (the
#   enforcement guard is S4.7; S4.5 only ADDS + hashes + _verify's the field). Adding it changes
#   content_hash() output -> any test pinning a literal caps hash must be updated to the new hash.

## ers/intent_store.py  (S4.5a) - new append-only table 'fills' (mirror op_audit / record_op_event EXACTLY):
#   CREATE TABLE IF NOT EXISTS fills (fill_id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
#     intent_id TEXT NOT NULL, token_id TEXT NOT NULL, condition_id TEXT NOT NULL, event_id TEXT NOT NULL,
#     side TEXT NOT NULL, shares TEXT NOT NULL, price_exec TEXT NOT NULL, worst_case_risk TEXT NOT NULL)
def record_fill(self, *, intent_id, token_id, condition_id, event_id, side, shares, price_exec, worst_case_risk) -> None
    #   INSERT with at=self._stamper.stamp(); store every Decimal as str(...); commit. Append-only.
def fills_log(self) -> list[dict]
    #   SELECT ... ORDER BY fill_id; each row -> {at:int, intent_id, token_id, condition_id, event_id, side,
    #   shares:Decimal, price_exec:Decimal, worst_case_risk:Decimal}  (Decimals via Decimal(str)).
def accepted(self) -> list[PendingIntent]
    #   SELECT {existing _COLUMNS} FROM pending_intents WHERE status='ACCEPTED' ORDER BY rowid. Mirrors
    #   pending(); reuses _row_to_intent. (Used by RestartReconciler to rebuild the Portfolio.)

## ers/service.py  (S4.5a) - process_pending gains a TRAILING keyword (additive, None-default):
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None,
                    gtd_for=None, fill_sink=None) -> Portfolio
    #   On ACCEPT, AFTER signer.place + _fold AND AFTER the existing gtd_for staging block:
    #       if fill_sink is not None:
    #           fill_sink(intent, decision, portfolio.positions[-1])
    #   fill_sink=None => byte-for-byte today's behavior (the 517 + all callers stay green).
def make_fill_sink(store):   # module-level helper in service.py
    #   def _sink(intent, decision, position):
    #       store.record_fill(intent_id=intent.intent_id, token_id=position.token_id,
    #           condition_id=position.condition_id, event_id=position.event_id, side="BUY",
    #           shares=(position.worst_case_risk / position.entry_price),
    #           price_exec=position.entry_price, worst_case_risk=position.worst_case_risk)
    #       return _sink
    #   Long convention: side ALWAYS "BUY"; shares = notional / entry. entry_price>0 holds on any ACCEPT.

## ers/controller.py  (S4.5a) - ERSController.__init__ gains fill_sink=None (PASS-THROUGH only):
#   self._fill_sink = fill_sink ; run_cycle passes fill_sink=self._fill_sink into process_pending(...).
#   Default None => the S4.1 controller tests stay green. NO reconciler= seam in S4.5 (that is S4.4).

## ers/reconcile.py  (NEW module, S4.5b parsers + S4.5c reconciler):
OK = "OK"; DIVERGED = "DIVERGED"; SETTLING = "SETTLING"; DORMANT = "DORMANT"
_SHARE_DECIMALS = 6   # raw ERC-1155 'value' -> shares = Decimal(value) / Decimal(10**6). Pinned; POL-4 verifies.

@dataclass(frozen=True)
class Balance:
    token_id: str
    shares: Decimal
    latest_fill_at: int | None = None   # monotonic-ns stamp of most-recent IN-SESSION fill; None=replayed (no grace)

@dataclass(frozen=True)
class Divergence:
    token_id: str
    internal_shares: Decimal
    onchain_shares: Decimal
    dollars: Decimal

@dataclass(frozen=True)
class ReconResult:
    status: str                          # OK | DIVERGED | SETTLING | DORMANT
    divergences: tuple                   # tuple[Divergence,...]
    onchain_confirmed_exposure: Decimal
    settling_tokens: tuple               # tuple[str,...]
    triggers: tuple                      # tuple[str,...]

def internal_balances(fills_log, *, in_session=True) -> dict[str, Balance]:
    #   Fold fills_log rows by token_id: shares = sum(+shares if side=="BUY" else -shares).
    #   latest_fill_at = max(row['at']) among that token's rows IF in_session else None.
    #   RestartReconciler calls with in_session=False (replayed rows: a prior monotonic epoch is NOT
    #   comparable to this process's now, so NO settle-window grace -> unconfirmed pre-restart fill = DIVERGED).

def clob_balances(envelopes) -> dict[str, Balance]:
    #   Keep Envelopes with source=="data-api" AND event_id.startswith("/positions:"). json.loads(content);
    #   key by item["asset"] (the decimal token_id); shares=Decimal(str(item["size"])); latest_fill_at=None.
    #   Any missing field / parse error on a row -> skip THAT row (fail-closed; a bad row never "agrees").

def onchain_balances(envelopes, *, wallet) -> "dict[str, Balance] | None":
    #   wallet is None -> return None  (the DORMANT sentinel).
    #   Keep source=="polygon-chain". json.loads(content) -> {"log":..,"event":..}; on event["kind"] in
    #   {transfer_single, transfer_batch}: for each (token_id, value): if to==wallet credit +value, if
    #   from==wallet debit -value (compare addresses lowercased). Net per token_id; shares =
    #   Decimal(net) / Decimal(10**_SHARE_DECIMALS). A row that fails to parse -> skip (fail-closed).

class ThreeWayReconciler:
    def __init__(self, *, caps): self._caps = caps
    def reconcile(self, internal, clob, onchain, *, wallet, now) -> ReconResult:
        #   internal,clob: dict[str,Balance]; onchain: dict[str,Balance] | None.
        #   if wallet is None or onchain is None: return ReconResult(DORMANT, (), Decimal(0), (), ("dormant_no_wallet",)).
        #   window_ns = self._caps.reconcile_settle_window_seconds * 1_000_000_000
        #   for token_id in internal.keys()|clob.keys()|onchain.keys():  # union catches orphans on ANY leg
        #       i=internal.get(token_id); o=onchain.get(token_id); si=(i.shares if i else 0); so=(o.shares if o else 0)
        #       d_dollars = abs(si-so) * Decimal(1)              # $1/share resolution ceiling (price-free)
        #       if d_dollars <= self._caps.reconcile_tolerance: continue
        #       if i and i.latest_fill_at is not None and (now - i.latest_fill_at) < window_ns:
        #           settling.append(token_id); continue
        #       divergences.append(Divergence(token_id, si, so, d_dollars))
        #   onchain_confirmed_exposure = sum(o.shares*Decimal(1) for o in onchain.values())
        #   CLOB is ADVISORY (on-chain authoritative): a CLOB-only mismatch never drives the verdict.
        #   status: DIVERGED if any divergences else SETTLING if any settling else OK.

## ers/restart.py  (NEW module, S4.5d):
class RestartReconciler:
    def __init__(self, *, store, event_store, reconciler, controller, caps, clock, wallet=None): ...
        #   clock = a 0-arg callable returning monotonic-ns now (time.monotonic_ns in prod; a fixed int in tests).
    def reconcile_on_boot(self) -> Portfolio:
        #   internal = internal_balances(store.fills_log(), in_session=False)   # restart: NO grace
        #   envs = event_store.all()
        #   clob = clob_balances(envs); onchain = onchain_balances(envs, wallet=self._wallet)
        #   result = self._reconciler.reconcile(internal, clob, onchain, wallet=self._wallet, now=self._clock())
        #   portfolio = self._rebuild_portfolio()    # from store.accepted(); see below
        #   if result.status in (OK, DORMANT): self._controller.set_state(RUNNING, reason=REASON_RESTART_RECONCILED)
        #   else: self._controller.set_state(HALTED, reason=REASON_UNCLEAN_RESTART)
        #   return portfolio
    def _rebuild_portfolio(self) -> Portfolio:
        #   For each PendingIntent r in store.accepted(): OpenPosition(condition_id=r.condition_id,
        #     event_id=r.event_id, resolution_source=r.condition_id, cluster_id=r.event_id,
        #     worst_case_risk=r.decision_stake_usd, token_id=r.token_id, entry_price=r.decision_price_exec,
        #     matrix_cold=True, frozen=False). Portfolio(nav=self._caps.nav, positions=tuple(...)).
        #   (DORMANT/shadow path. The live on-chain-confirmed-INTERSECT-ACCEPTED rebuild is DEFERRED to POL-4.)

## ers/safety.py  (S4.5d) - two NET-NEW module-level reason constants (additive; free-form strings):
REASON_L5_RECON_MISMATCH = "l5_recon_mismatch"     # the running-cadence consumer (S4.4) reason; DEFINED here now
REASON_RESTART_RECONCILED = "restart_reconciled"   # the clean HALTED->RUNNING transition reason
# RUNNING / HALTED / REASON_UNCLEAN_RESTART already exist in safety.py and are imported by restart.py.
```

---

## Sub-slice S4.5a — the durable fills ledger + the `fill_sink` seam

> SCOPE NOTE for the implementer: `process_pending` steps 1-3 (op-state gate / L7 breaker / per-intent precedence), the `Decision`/precedence flow, `evaluate_intent`, the validator dataclasses, and `propose_trade`'s INSERT-only chokepoint are **UNTOUCHED**. Every change here is additive: one new append-only table + two new `IntentStore` methods + one new `accepted()` reader, a trailing `fill_sink=None` keyword on `process_pending`, a module-level `make_fill_sink` helper, and a pass-through `fill_sink=None` on `ERSController`. `fill_sink=None` ⇒ byte-for-byte today's behavior; the 517-baseline and the S4.1 controller tests stay green. OBSERVE each true RED before writing code.

---

### Task 1: `IntentStore.record_fill` + `fills_log` (new append-only `fills` table)

**Files:** Modify `src/polybot/ers/intent_store.py` (add the `fills` CREATE TABLE in `__init__` after the `op_audit` table at line ~109; add `record_fill` + `fills_log` after `op_audit_log` at line ~184) / Test `tests/test_ers_intent_store.py` (append at end of file, after `test_op_audit_log_persists_across_restart`)

- [ ] **Step 1: Write the failing test**

```python
# --- S4.5a: durable fills ledger (POL-6) -----------------------------------------------------
from decimal import Decimal  # noqa: F401 (harmless if already imported at top of file)
from polybot.core.clock import MonotonicStamper  # noqa: F401
from polybot.ers.intent_store import IntentStore  # noqa: F401


def _fills_store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_fill_appends_ordered_decimal_exact_rows(tmp_path):
    # The fills ledger is the durable INTERNAL leg of S4.5 reconciliation: append-only, ordered by
    # fill_id, every Decimal round-tripped EXACTLY (stored as string, read back as Decimal), and each
    # row carries the shared monotonic stamp. Mirrors record_op_event / op_audit_log.
    with _fills_store(str(tmp_path / "i.db")) as store:
        store.record_fill(intent_id="i1", token_id="t1", condition_id="0xabc", event_id="e1",
                          side="BUY", shares=Decimal("24"), price_exec=Decimal("0.50"),
                          worst_case_risk=Decimal("12"))
        store.record_fill(intent_id="i2", token_id="t2", condition_id="0xdef", event_id="e2",
                          side="BUY", shares=Decimal("13.333333"), price_exec=Decimal("0.45"),
                          worst_case_risk=Decimal("6"))

        rows = store.fills_log()
        assert [r["intent_id"] for r in rows] == ["i1", "i2"]   # ORDER BY fill_id
        assert [r["token_id"] for r in rows] == ["t1", "t2"]
        assert rows[0]["condition_id"] == "0xabc" and rows[0]["event_id"] == "e1"
        assert rows[0]["side"] == "BUY"
        # Decimal-exact round-trip (NOT float):
        assert rows[0]["shares"] == Decimal("24") and isinstance(rows[0]["shares"], Decimal)
        assert rows[0]["price_exec"] == Decimal("0.50")
        assert rows[0]["worst_case_risk"] == Decimal("12")
        assert rows[1]["shares"] == Decimal("13.333333")
        # Each row carries the shared monotonic stamp, strictly increasing in id-order.
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 2 and ats[0] > 0


def test_fills_log_persists_across_restart(tmp_path):
    # Append-only + committed: a fill survives a process restart and a fresh stamper, and a new fill
    # appends AFTER the persisted one (id ordering, not the per-process stamp clock).
    db = str(tmp_path / "i.db")
    with _fills_store(db) as store:
        store.record_fill(intent_id="i1", token_id="t1", condition_id="0xabc", event_id="e1",
                          side="BUY", shares=Decimal("24"), price_exec=Decimal("0.50"),
                          worst_case_risk=Decimal("12"))
    with _fills_store(db) as reopened:
        rows = reopened.fills_log()
        assert len(rows) == 1 and rows[0]["token_id"] == "t1"
        reopened.record_fill(intent_id="i2", token_id="t2", condition_id="0xdef", event_id="e2",
                             side="BUY", shares=Decimal("4"), price_exec=Decimal("0.50"),
                             worst_case_risk=Decimal("2"))
        assert [r["intent_id"] for r in reopened.fills_log()] == ["i1", "i2"]
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_record_fill_appends_ordered_decimal_exact_rows -v'
```
Expected failure: `AttributeError: 'IntentStore' object has no attribute 'record_fill'`.

- [ ] **Step 3: Minimal implementation** — In `IntentStore.__init__`, add this CREATE TABLE immediately after the `op_audit` table block (before the final `self._conn.commit()` at line 110):

```python
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                fill_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                at              INTEGER NOT NULL,
                intent_id       TEXT    NOT NULL,
                token_id        TEXT    NOT NULL,
                condition_id    TEXT    NOT NULL,
                event_id        TEXT    NOT NULL,
                side            TEXT    NOT NULL,
                shares          TEXT    NOT NULL,
                price_exec      TEXT    NOT NULL,
                worst_case_risk TEXT    NOT NULL
            )
            """
        )
```

Then add these two methods immediately after `op_audit_log` (after line 184):

```python
    def record_fill(self, *, intent_id, token_id, condition_id, event_id, side, shares,
                    price_exec, worst_case_risk):
        """Append an IMMUTABLE fill row -- the durable INTERNAL leg the S4.5 reconcile + restart
        replays. Append-only + the shared monotonic stamp (mirrors record_op_event); every Decimal
        is stored as an exact string. ``side`` is "BUY" for a long entry."""
        self._conn.execute(
            "INSERT INTO fills (at, intent_id, token_id, condition_id, event_id, side, shares, "
            "price_exec, worst_case_risk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._stamper.stamp(), intent_id, token_id, condition_id, event_id, side,
             str(shares), str(price_exec), str(worst_case_risk)),
        )
        self._conn.commit()

    def fills_log(self):
        rows = self._conn.execute(
            "SELECT at, intent_id, token_id, condition_id, event_id, side, shares, price_exec, "
            "worst_case_risk FROM fills ORDER BY fill_id"
        ).fetchall()
        return [{"at": r[0], "intent_id": r[1], "token_id": r[2], "condition_id": r[3],
                 "event_id": r[4], "side": r[5], "shares": Decimal(r[6]),
                 "price_exec": Decimal(r[7]), "worst_case_risk": Decimal(r[8])} for r in rows]
```

- [ ] **Step 4: Run green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_record_fill_appends_ordered_decimal_exact_rows tests/test_ers_intent_store.py::test_fills_log_persists_across_restart -v'
```
Expected: both PASS. Then confirm the baseline grew, never shrank:
```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c'
```
Expected: `exit=0` and the dot-count is `519` (517 + 2).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/intent_store.py tests/test_ers_intent_store.py && git commit -m "feat(ers): add append-only fills ledger (record_fill/fills_log) for S4.5 reconcile"'
```

---

### Task 2: `IntentStore.accepted()` (status=ACCEPTED reader, mirrors `pending()`)

**Files:** Modify `src/polybot/ers/intent_store.py` (add `accepted` immediately after `pending` at line ~155) / Test `tests/test_ers_intent_store.py` (append after the Task-1 tests)

- [ ] **Step 1: Write the failing test**

```python
def test_accepted_returns_accepted_rows_in_rowid_order(tmp_path):
    # accepted() mirrors pending() but selects status=ACCEPTED, ORDER BY rowid. RestartReconciler
    # uses it to rebuild the Portfolio at boot. PROPOSED/REJECTED/SKIPPED rows are excluded; the
    # decision fields (stake/price) round-trip so the rebuild can reconstruct each OpenPosition.
    with _fills_store(str(tmp_path / "i.db")) as store:
        store.propose_trade("acc", **_PROPOSAL)
        store.propose_trade("rej", **_PROPOSAL)
        store.propose_trade("prop", **_PROPOSAL)  # left PROPOSED
        store.record_decision("acc", Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly"))
        store.record_decision("rej", Decision("REJECT", None, Decimal("0.55"), "book_stale"))

        acc = store.accepted()
        assert [i.intent_id for i in acc] == ["acc"]   # only the ACCEPTED row
        assert acc[0].status == "ACCEPTED"
        assert acc[0].decision_stake_usd == Decimal("8")
        assert acc[0].decision_price_exec == Decimal("0.55")
        assert acc[0].token_id == "t1"
```

(The test reuses the module-level `_PROPOSAL`, `_fills_store`, and the already-imported `Decision`/`Decimal`.)

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_accepted_returns_accepted_rows_in_rowid_order -v'
```
Expected failure: `AttributeError: 'IntentStore' object has no attribute 'accepted'`.

- [ ] **Step 3: Minimal implementation** — Add this method immediately after `pending` (after line 155, before `get`):

```python
    def accepted(self):
        # The ACCEPTED set, ORDER BY rowid -- mirrors pending(); RestartReconciler (S4.5d) reads it
        # to rebuild the in-memory Portfolio at boot. Re-uses _row_to_intent (the decision fields
        # round-trip so each OpenPosition can be reconstructed).
        return self._query(
            f"SELECT {_COLUMNS} FROM pending_intents WHERE status=? ORDER BY rowid",
            ("ACCEPTED",),
        )
```

- [ ] **Step 4: Run green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_intent_store.py::test_accepted_returns_accepted_rows_in_rowid_order -v'
```
Expected: PASS. Re-confirm the full baseline:
```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c'
```
Expected: `exit=0`, dot-count `520`.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/intent_store.py tests/test_ers_intent_store.py && git commit -m "feat(ers): add IntentStore.accepted() reader for S4.5 restart rebuild"'
```

---

### Task 3: `make_fill_sink(store)` + the `fill_sink=None` seam in `process_pending`

**Files:** Modify `src/polybot/ers/service.py` (add `fill_sink=None` to the `process_pending` signature at line ~53-55; add the recording call inside the ACCEPT branch after the `gtd_for` block at line ~120; add module-level `make_fill_sink` helper, e.g. directly after `process_pending` ends at line ~121) / Test `tests/test_ers_service.py` (append at end of file)

- [ ] **Step 1: Write the failing test**

```python
# --- S4.5a (POL-6): durable fills ledger via the fill_sink seam ------------------------------
from polybot.ers.service import make_fill_sink


def test_no_fill_recorded_when_fill_sink_is_none(tmp_path):
    # fill_sink=None (the default) == today's behavior: an ACCEPT places + folds but writes NO
    # fills row. Guards the 520 baseline -- the seam is purely additive.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, fill_sink=None)
        assert store.get("i1").status == "ACCEPTED"
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1
        assert store.fills_log() == []   # NO durable fill recorded


def test_wired_fill_sink_records_one_fill_per_accept_decimal_exact(tmp_path):
    # A make_fill_sink(store) wired sink records exactly one fill per ACCEPT, with shares =
    # worst_case_risk / entry_price (Decimal-exact), side="BUY", and the folded position's ids.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)  # token t1, condition m1, event e1
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, fill_sink=make_fill_sink(store))
        assert store.get("i1").status == "ACCEPTED"
        pos = final.positions[-1]   # stake $12 @ entry 0.50
        fills = store.fills_log()
        assert len(fills) == 1
        f = fills[0]
        assert f["intent_id"] == "i1" and f["token_id"] == "t1"
        assert f["condition_id"] == "m1" and f["event_id"] == "e1"
        assert f["side"] == "BUY"
        assert f["price_exec"] == Decimal("0.50") == pos.entry_price
        assert f["worst_case_risk"] == Decimal("12") == pos.worst_case_risk
        # shares = worst_case_risk / entry_price = 12 / 0.50 = 24 (Decimal-exact, no float)
        assert f["shares"] == Decimal("24") and isinstance(f["shares"], Decimal)


def test_fill_sink_records_nothing_on_a_reject(tmp_path):
    # A REJECT (missing book -> no_book) never reaches the ACCEPT branch, so the wired sink writes
    # no fill -- recording is strictly on ACCEPT.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={}.get,  # no book for t1 -> REJECT(no_book)
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, fill_sink=make_fill_sink(store))
        assert store.get("i1").status == "REJECTED"
        assert store.fills_log() == []
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py::test_wired_fill_sink_records_one_fill_per_accept_decimal_exact -v'
```
Expected failure: `ImportError: cannot import name 'make_fill_sink' from 'polybot.ers.service'` (the collector fails at the new `from polybot.ers.service import make_fill_sink` import — observe THIS true RED).

- [ ] **Step 3: Minimal implementation** — Change the `process_pending` signature (lines 53-55) to add the trailing keyword:

```python
def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None,
                    gtd_for=None, fill_sink=None):
```

Inside the `if decision.verdict == "ACCEPT":` block, AFTER the entire `if gtd_for is not None:` block (i.e. as the last statement of the ACCEPT branch, after line 120), add:

```python
            if fill_sink is not None:
                # Durable INTERNAL leg of the S4.5 reconcile: record the just-folded position.
                # fill_sink=None (the default) => no fills row => byte-for-byte today's behavior.
                fill_sink(intent, decision, portfolio.positions[-1])
```

Then add the module-level helper immediately after `process_pending` returns (after line 121, before `_process_intent_slice3`):

```python
def make_fill_sink(store):
    """Return the recording callable wired into process_pending(fill_sink=...) so every ACCEPT
    appends a durable fill (the internal reconcile leg). Long convention: side is ALWAYS "BUY";
    shares = worst_case_risk / entry_price (notional / entry). entry_price > 0 holds on any ACCEPT,
    so the division is exact and never divides by zero."""
    def _sink(intent, decision, position):
        store.record_fill(
            intent_id=intent.intent_id, token_id=position.token_id,
            condition_id=position.condition_id, event_id=position.event_id, side="BUY",
            shares=(position.worst_case_risk / position.entry_price),
            price_exec=position.entry_price, worst_case_risk=position.worst_case_risk)
    return _sink
```

- [ ] **Step 4: Run green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_service.py::test_no_fill_recorded_when_fill_sink_is_none tests/test_ers_service.py::test_wired_fill_sink_records_one_fill_per_accept_decimal_exact tests/test_ers_service.py::test_fill_sink_records_nothing_on_a_reject -v'
```
Expected: all three PASS. Re-confirm the full baseline:
```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c'
```
Expected: `exit=0`, dot-count `523`.

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/service.py tests/test_ers_service.py && git commit -m "feat(ers): add fill_sink=None seam + make_fill_sink to process_pending"'
```

---

### Task 4: `ERSController` `fill_sink=None` pass-through

**Files:** Modify `src/polybot/ers/controller.py` (add `fill_sink=None` to `__init__` signature at line ~20-21; store `self._fill_sink` near line ~33; pass `fill_sink=self._fill_sink` into the `process_pending(...)` call at lines ~48-51) / Test `tests/test_ers_controller.py` (append at end of file)

- [ ] **Step 1: Write the failing test**

```python
# --- S4.5a (POL-6): fill_sink pass-through ---------------------------------------------------
from polybot.ers.service import make_fill_sink


def test_fill_sink_none_default_records_no_fills(tmp_path):
    # Default fill_sink=None: a RUNNING controller's cycle ACCEPTs and places, but writes NO fills
    # row. Guards the S4.1 controller tests (the seam is additive).
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert store.fills_log() == []   # no sink wired -> no durable fill
    finally:
        store.close()


def test_wired_fill_sink_reaches_the_store_on_a_cycle_accept(tmp_path):
    # A make_fill_sink(store) passed to the controller is threaded into process_pending, so a
    # RUNNING-cycle ACCEPT records exactly one durable fill for the folded position.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    try:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0,
                           fill_sink=make_fill_sink(store))
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        fills = store.fills_log()
        assert len(fills) == 1
        assert fills[0]["token_id"] == "t1" and fills[0]["side"] == "BUY"
        assert fills[0]["shares"] == Decimal("24")  # 12 / 0.50, Decimal-exact
    finally:
        store.close()
```

(Reuses the module-level `_book`, `_P`, `IntentStore`, `MonotonicStamper`, `SafetyController`, `_safety`, `RiskCaps`, `ERSController`, `PaperSigner`, `Decimal` already imported at the top of `test_ers_controller.py`.)

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_controller.py::test_wired_fill_sink_reaches_the_store_on_a_cycle_accept -v'
```
Expected failure: `TypeError: ERSController.__init__() got an unexpected keyword argument 'fill_sink'`.

- [ ] **Step 3: Minimal implementation** — Change the `ERSController.__init__` signature (lines 20-21) to add `fill_sink=None`:

```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, clock):
```

Add the attribute assignment after `self._gtd_for = gtd_for` (line 33):

```python
        # fill_sink (S4.5a seam): an opt-in recording callable (make_fill_sink(store)) passed
        # straight through to process_pending so every ACCEPT appends a durable fill. fill_sink=None
        # (the default) == today's behavior -- no fills recorded -- so the S4.1 tests stay green.
        self._fill_sink = fill_sink
```

Update the `process_pending(...)` call in `run_cycle` (lines 48-51) to thread the sink through:

```python
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio, caps=self._caps,
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink)
```

- [ ] **Step 4: Run green**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_ers_controller.py::test_fill_sink_none_default_records_no_fills tests/test_ers_controller.py::test_wired_fill_sink_reaches_the_store_on_a_cycle_accept -v'
```
Expected: both PASS. Final full-baseline confirmation for the whole sub-slice:
```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c'
```
Expected: `exit=0`, dot-count `525` (517 + 8 new tests across the sub-slice).

- [ ] **Step 5: Commit**

```
wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_ers_controller.py && git commit -m "feat(ers): thread fill_sink=None pass-through into ERSController.run_cycle"'
```

---

## Sub-slice S4.5b — the three pure leg parsers (`ers/reconcile.py`)

**Module context.** This sub-slice creates the NEW module `src/polybot/ers/reconcile.py`. It builds the frozen dataclasses + status constants (so S4.5c/S4.5d can import them), then the three pure leg parsers. The parsers fold *already-fetched* rows: `internal_balances` over `fills_log` rows (S4.5a's ledger), `clob_balances`/`onchain_balances` over `Envelope` lists (the EventStore filter seam lives in the parsers themselves). No `ThreeWayReconciler` here — that is S4.5c. Each test self-contains its fixtures (no `conftest.py`). Money is `Decimal`; addresses compared lowercased; every parse failure on a row is fail-closed (skip the row, never "agree"). The shared dataclass/constants test file is `tests/test_ers_reconcile.py`; the parser tests extend the same file.

---

### Task 5: Pin the dataclasses + status constants + `_SHARE_DECIMALS`
**Files:** Create `src/polybot/ers/reconcile.py` / Test `tests/test_ers_reconcile.py`

- [ ] **Step 1: Write the failing test** — a trivial RED that imports the not-yet-existing symbols and pins their shapes (frozen, field names, defaults, constant values).

```python
"""Tests for the S4.5b leg parsers in ers/reconcile.py (POL-6 / S4.5).

These pin the pure per-token_id balance folders: the internal fills fold, the
Data-API /positions fold, and the on-chain ERC-1155 transfer fold. The reconciler
itself (S4.5c) is NOT exercised here. Fixtures are module-level helpers (no conftest).
"""

import json
from decimal import Decimal

import pytest

from polybot.core.models import Envelope
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    _SHARE_DECIMALS,
    Balance,
    Divergence,
    ReconResult,
)


def test_status_constants_and_share_decimals_are_pinned():
    """The four status strings and the ERC-1155 share scaling are the single
    source of truth the reconciler + restart machine import; pin their literals."""
    assert (OK, DIVERGED, SETTLING, DORMANT) == ("OK", "DIVERGED", "SETTLING", "DORMANT")
    assert _SHARE_DECIMALS == 6


def test_balance_is_frozen_with_default_latest_fill_at_none():
    """Balance carries shares + an optional in-session monotonic-ns fill stamp;
    latest_fill_at defaults to None (the replayed / no-grace marker) and the
    dataclass is frozen so a parsed balance can't be mutated downstream."""
    b = Balance(token_id="42", shares=Decimal("3"))
    assert (b.token_id, b.shares, b.latest_fill_at) == ("42", Decimal("3"), None)
    with pytest.raises(Exception):
        b.shares = Decimal("9")


def test_divergence_and_reconresult_field_shapes():
    """Divergence is (token_id, internal_shares, onchain_shares, dollars); ReconResult
    is (status, divergences, onchain_confirmed_exposure, settling_tokens, triggers)."""
    d = Divergence(token_id="42", internal_shares=Decimal("3"),
                   onchain_shares=Decimal("0"), dollars=Decimal("3"))
    assert d.dollars == Decimal("3")
    r = ReconResult(status=OK, divergences=(), onchain_confirmed_exposure=Decimal("0"),
                    settling_tokens=(), triggers=())
    assert r.status == "OK" and r.divergences == ()
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile.py -v`
  Expected: collection error `ModuleNotFoundError: No module named 'polybot.ers.reconcile'` (the module does not exist yet). OBSERVE this true RED before writing any code.

- [ ] **Step 3: Minimal implementation** — create the module with ONLY the constants + dataclasses (no parsers yet; those are Tasks 6-8).

```python
"""S4.5 (POL-6) three-way reconciliation: leg parsers + pure reconciler.

The ERS independently checks its own belief of what it holds (the durable `fills`
ledger) against two external truths -- Polymarket's Data-API `/positions` (advisory)
and the authoritative on-chain ERC-1155 CTF balance -- and HALTS rather than trade on
an unexplained divergence. This module owns the pure leg parsers (S4.5b) that fold
already-fetched rows into per-`token_id` balance maps; the `ThreeWayReconciler` (S4.5c)
and `RestartReconciler` (S4.5d) consume them. Fail-closed throughout: a malformed row
is skipped (never silently "agrees"); money is Decimal; addresses compare lowercased.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal

OK = "OK"
DIVERGED = "DIVERGED"
SETTLING = "SETTLING"
DORMANT = "DORMANT"

# Raw ERC-1155 `value` -> shares = Decimal(value) / Decimal(10**6). Pinned constant;
# empirical verification of the 6-decimal scaling is deferred to POL-4 (a real receipt).
_SHARE_DECIMALS = 6


@dataclass(frozen=True)
class Balance:
    token_id: str
    shares: Decimal
    # Monotonic-ns stamp of the most-recent IN-SESSION fill; None == replayed/pre-restart
    # (a prior monotonic epoch is not comparable to this process's now -> no settle grace).
    latest_fill_at: int | None = None


@dataclass(frozen=True)
class Divergence:
    token_id: str
    internal_shares: Decimal
    onchain_shares: Decimal
    dollars: Decimal


@dataclass(frozen=True)
class ReconResult:
    status: str                    # OK | DIVERGED | SETTLING | DORMANT
    divergences: tuple             # tuple[Divergence, ...]
    onchain_confirmed_exposure: Decimal
    settling_tokens: tuple         # tuple[str, ...]
    triggers: tuple                # tuple[str, ...]
```

- [ ] **Step 4: Run green** — `./.venv/bin/pytest tests/test_ers_reconcile.py -v` → 3 PASS. Then full count: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, dots **528** (525 running baseline + 3 new).

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/reconcile.py tests/test_ers_reconcile.py && git commit -m "feat(ers): reconcile.py dataclasses + status constants (S4.5b)"`

---

### Task 6: `internal_balances` — fold the durable fills rows
**Files:** Modify `src/polybot/ers/reconcile.py` (add `internal_balances`) / Test `tests/test_ers_reconcile.py` (append)

- [ ] **Step 1: Write the failing test** — two same-token fills fold (BUY +, SELL −); `latest_fill_at` = max `at` when `in_session=True`, and is nulled when `in_session=False`. `fills_log` rows are dicts shaped exactly as S4.5a's `fills_log()` returns (`at:int`, `shares`/`price_exec`/`worst_case_risk` as `Decimal`).

```python
def _fill(token, side, shares, at, *, intent="i1"):
    # Mirrors IntentStore.fills_log() row shape (S4.5a): Decimals already converted.
    return {"at": at, "intent_id": intent, "token_id": token, "condition_id": "0xcond",
            "event_id": "evt", "side": side, "shares": Decimal(shares),
            "price_exec": Decimal("0.50"), "worst_case_risk": Decimal(shares) * Decimal("0.50")}


def test_internal_balances_folds_buys_and_sells_per_token():
    """Two in-session fills on one token net to (BUY - SELL) shares, and
    latest_fill_at is the max `at` among that token's rows (newest fill stamp)."""
    from polybot.ers.reconcile import internal_balances
    rows = [_fill("42", "BUY", "5", at=100), _fill("42", "SELL", "2", at=250)]
    out = internal_balances(rows, in_session=True)
    assert set(out) == {"42"}
    assert out["42"].shares == Decimal("3")
    assert out["42"].latest_fill_at == 250


def test_internal_balances_replayed_nulls_latest_fill_at():
    """With in_session=False (the RestartReconciler's replay path) latest_fill_at is
    None for every token: a prior monotonic epoch is not comparable to this `now`, so
    a replayed unconfirmed fill gets NO settle-window grace (fail-closed at boot)."""
    from polybot.ers.reconcile import internal_balances
    rows = [_fill("42", "BUY", "5", at=100)]
    out = internal_balances(rows, in_session=False)
    assert out["42"].shares == Decimal("5")
    assert out["42"].latest_fill_at is None
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest "tests/test_ers_reconcile.py::test_internal_balances_folds_buys_and_sells_per_token" -v`
  Expected: `ImportError: cannot import name 'internal_balances' from 'polybot.ers.reconcile'`. OBSERVE the true RED.

- [ ] **Step 3: Minimal implementation** — append to `reconcile.py`:

```python
def internal_balances(fills_log, *, in_session=True):
    """Fold the durable fills rows into {token_id: Balance}. shares = sum(+shares if
    side == "BUY" else -shares). latest_fill_at = max(at) among that token's rows when
    in_session, else None (replayed rows get no settle-window grace -> fail-closed)."""
    shares: dict[str, Decimal] = {}
    latest: dict[str, int] = {}
    for row in fills_log:
        token = row["token_id"]
        signed = row["shares"] if row["side"] == "BUY" else -row["shares"]
        shares[token] = shares.get(token, Decimal(0)) + signed
        at = row["at"]
        if token not in latest or at > latest[token]:
            latest[token] = at
    return {
        token: Balance(token_id=token, shares=total,
                       latest_fill_at=(latest[token] if in_session else None))
        for token, total in shares.items()
    }
```

- [ ] **Step 4: Run green** — `./.venv/bin/pytest "tests/test_ers_reconcile.py" -v` → 5 PASS. Full count: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, **530**.

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/reconcile.py tests/test_ers_reconcile.py && git commit -m "feat(ers): internal_balances fills fold (S4.5b)"`

---

### Task 7: `clob_balances` — fold Data-API `/positions` Envelopes (fail-closed)
**Files:** Modify `src/polybot/ers/reconcile.py` (add `clob_balances`) / Test `tests/test_ers_reconcile.py` (append)

- [ ] **Step 1: Write the failing test** — parses a real `/positions` Envelope (content = `json.dumps(item)` matching `data_api.py`, `source="data-api"`, `event_id` starts `/positions:`); a malformed row (missing `asset`) is SKIPPED; a non-`/positions` data-api Envelope (e.g. `/trades:`) is ignored.

```python
def _positions_env(asset, size, *, eid_suffix="0xwallet"):
    # Mirrors data_api.py: content is json.dumps(item); event_id is "/positions:<id>".
    item = {"asset": asset, "size": size, "conditionId": "0xcond"}
    return Envelope(source="data-api", source_tier="DATA",
                    event_id=f"/positions:{eid_suffix}", observed_at=1,
                    content=json.dumps(item, sort_keys=True, default=str))


def test_clob_balances_parses_a_positions_envelope():
    """A /positions Envelope folds to a Balance keyed by item['asset'] (token_id) with
    shares = Decimal(str(size)); latest_fill_at is None (CLOB leg carries no fill stamp)."""
    from polybot.ers.reconcile import clob_balances
    out = clob_balances([_positions_env("42", "7")])
    assert out["42"].shares == Decimal("7")
    assert out["42"].latest_fill_at is None


def test_clob_balances_skips_a_malformed_row_fail_closed():
    """A /positions Envelope whose content lacks 'asset' (or won't parse) is skipped,
    not folded -- a bad row must never silently 'agree' with the other legs."""
    from polybot.ers.reconcile import clob_balances
    bad = Envelope(source="data-api", source_tier="DATA", event_id="/positions:x",
                   observed_at=1, content=json.dumps({"size": "7"}))  # no 'asset'
    out = clob_balances([bad, _positions_env("42", "7")])
    assert set(out) == {"42"}  # the bad row contributed nothing


def test_clob_balances_ignores_non_positions_data_api_envelope():
    """A data-api Envelope from another path (e.g. /trades) is not a positions row;
    only event_id starting '/positions:' is folded into the CLOB balance leg."""
    from polybot.ers.reconcile import clob_balances
    trade = Envelope(source="data-api", source_tier="DATA", event_id="/trades:abc",
                     observed_at=1, content=json.dumps({"asset": "99", "size": "5"}))
    out = clob_balances([trade, _positions_env("42", "7")])
    assert set(out) == {"42"}
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest "tests/test_ers_reconcile.py::test_clob_balances_parses_a_positions_envelope" -v`
  Expected: `ImportError: cannot import name 'clob_balances' from 'polybot.ers.reconcile'`. OBSERVE the true RED.

- [ ] **Step 3: Minimal implementation** — append to `reconcile.py`:

```python
def clob_balances(envelopes):
    """Fold Data-API /positions Envelopes into {token_id: Balance}. Keep only
    source == "data-api" with an event_id starting "/positions:"; key by item["asset"]
    (the decimal token_id), shares = Decimal(str(item["size"])). Any missing field or
    parse error on a row -> skip THAT row (fail-closed; a bad row never "agrees")."""
    out: dict[str, Balance] = {}
    for env in envelopes:
        if env.source != "data-api" or not env.event_id.startswith("/positions:"):
            continue
        try:
            item = json.loads(env.content)
            token = item["asset"]
            shares = Decimal(str(item["size"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue  # malformed row: skip, never fold a bad value as agreement
        prev = out.get(token)
        total = shares + (prev.shares if prev else Decimal(0))
        out[token] = Balance(token_id=token, shares=total, latest_fill_at=None)
    return out
```

- [ ] **Step 4: Run green** — `./.venv/bin/pytest "tests/test_ers_reconcile.py" -v` → 8 PASS. Full count: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, **533**.

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/reconcile.py tests/test_ers_reconcile.py && git commit -m "feat(ers): clob_balances /positions fold, fail-closed (S4.5b)"`

---

### Task 8: `onchain_balances` — fold Polygon ERC-1155 transfers (DORMANT sentinel + fail-closed)
**Files:** Modify `src/polybot/ers/reconcile.py` (add `onchain_balances`) / Test `tests/test_ers_reconcile.py` (append)

- [ ] **Step 1: Write the failing test** — `wallet=None` ⇒ `None` (DORMANT sentinel); a `transfer_single` crediting our wallet folds `+value/10**6` shares; a transfer NOT involving our wallet yields 0/absent; addresses compare lowercased. Envelope content matches `polygon.py`'s `{"log":.., "event":..}` shape (the parser reads only `event`).

```python
def _chain_env(event, *, eid="0xtx:0"):
    # Mirrors polygon.py: content is json.dumps({"log": log, "event": event}).
    return Envelope(source="polygon-chain", source_tier="CHAIN", event_id=eid,
                    observed_at=1, content=json.dumps({"log": {}, "event": event},
                                                      sort_keys=True, default=str))


def _single(frm, to, token, value):
    return {"kind": "transfer_single", "operator": "0xop", "from": frm, "to": to,
            "token_id": token, "value": value}


WALLET = "0xCAFE000000000000000000000000000000000001"


def test_onchain_balances_wallet_none_is_dormant_sentinel():
    """In pure shadow there is no chain truth; wallet=None returns the DORMANT
    sentinel None so the reconciler short-circuits to DORMANT (permits RUNNING)."""
    from polybot.ers.reconcile import onchain_balances
    assert onchain_balances([_chain_env(_single("0xa", WALLET, "42", "5000000"))],
                            wallet=None) is None


def test_onchain_balances_credits_a_transfer_to_our_wallet():
    """A TransferSingle with to == our wallet credits +value/10**6 shares for that
    token_id; address comparison is case-insensitive (the chain emits checksum case)."""
    from polybot.ers.reconcile import onchain_balances
    out = onchain_balances([_chain_env(_single("0xseller", WALLET, "42", "5000000"))],
                           wallet=WALLET.lower())
    assert out["42"].shares == Decimal("5")  # 5_000_000 / 10**6
    assert out["42"].latest_fill_at is None


def test_onchain_balances_ignores_a_transfer_not_involving_our_wallet():
    """A transfer between two third parties touches neither from nor to == our wallet,
    so it nets to nothing -- the token is absent (no spurious balance)."""
    from polybot.ers.reconcile import onchain_balances
    out = onchain_balances([_chain_env(_single("0xa", "0xb", "42", "5000000"))],
                           wallet=WALLET.lower())
    assert out.get("42") is None or out["42"].shares == Decimal("0")
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest "tests/test_ers_reconcile.py::test_onchain_balances_wallet_none_is_dormant_sentinel" -v`
  Expected: `ImportError: cannot import name 'onchain_balances' from 'polybot.ers.reconcile'`. OBSERVE the true RED.

- [ ] **Step 3: Minimal implementation** — append to `reconcile.py`:

```python
def onchain_balances(envelopes, *, wallet):
    """Fold Polygon ERC-1155 transfer Envelopes into {token_id: Balance}, or return
    the DORMANT sentinel None when wallet is None (pure shadow: no chain truth).

    Keep source == "polygon-chain"; on event kind in {transfer_single, transfer_batch}
    credit +value where to == wallet and debit -value where from == wallet (addresses
    compared lowercased), netting per token_id. shares = net / 10**_SHARE_DECIMALS. A
    row that fails to parse is skipped (fail-closed; a bad row never "agrees")."""
    if wallet is None:
        return None
    wallet = wallet.lower()
    net: dict[str, int] = {}
    for env in envelopes:
        if env.source != "polygon-chain":
            continue
        try:
            event = json.loads(env.content)["event"]
            kind = event["kind"]
            if kind == "transfer_single":
                pairs = [(event["token_id"], event["value"])]
            elif kind == "transfer_batch":
                pairs = list(zip(event["token_ids"], event["values"]))
            else:
                continue
            frm = event["from"].lower()
            to = event["to"].lower()
        except (ValueError, KeyError, TypeError, AttributeError):
            continue  # malformed row: skip, never fold a bad value as agreement
        sign = 0
        if to == wallet:
            sign += 1
        if frm == wallet:
            sign -= 1
        if sign == 0:
            continue
        for token, raw in pairs:
            try:
                net[token] = net.get(token, 0) + sign * int(raw)
            except (ValueError, TypeError):
                continue  # non-integer value -> fail-closed: drop this entry
    scale = Decimal(10 ** _SHARE_DECIMALS)
    return {
        token: Balance(token_id=token, shares=Decimal(value) / scale, latest_fill_at=None)
        for token, value in net.items()
    }
```

- [ ] **Step 4: Run green** — `./.venv/bin/pytest "tests/test_ers_reconcile.py" -v` → 11 PASS. Full count: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, **536**. Confirm the baseline grew (517 → 536) and never shrank.

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/reconcile.py tests/test_ers_reconcile.py && git commit -m "feat(ers): onchain_balances ERC-1155 fold + DORMANT sentinel (S4.5b)"`

---

## Sub-slice S4.5c — the pure `ThreeWayReconciler.reconcile`

**Scope:** the pure verdict function `ThreeWayReconciler.reconcile(internal, clob, onchain, *, wallet, now) -> ReconResult` in `src/polybot/ers/reconcile.py`. Depends on S4.5b's `Balance`, `Divergence`, `ReconResult`, and the constants `OK / DIVERGED / SETTLING / DORMANT` already in `reconcile.py` — **reference them, do NOT redefine them**. All money is `Decimal`; `now` is an injected fixed `int` in the monotonic-ns domain; `latest_fill_at` is monotonic-ns or `None` (replayed). These are safety-critical tests: each pins **both** `status` **and** the specific `Divergence`/dollars so a mutation flips them.

**Shared test header** (copy verbatim into `tests/test_ers_reconcile_verdict.py` — there is no `conftest.py`, every file self-contains its fixtures):

```python
"""S4.5c / POL-6 -- the pure ThreeWayReconciler verdict over three per-token balance maps.

On-chain (ERC-1155) is ground truth; the reconciler trades only when the ERS's own belief
matches that truth within the signed reconcile_tolerance, and FAILS CLOSED (DIVERGED) on any
divergence, orphan, or replayed-unconfirmed state. wallet=None / onchain=None -> DORMANT
(shadow-clean). CLOB is advisory: a CLOB-only mismatch never drives the verdict. The
settle-window (caps.reconcile_settle_window_seconds, keyed on the INTERNAL fill stamp in the
SAME monotonic-ns domain as `now`) exempts a just-placed not-yet-confirmed fill as SETTLING.
"""

from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.reconcile import (
    DIVERGED,
    DORMANT,
    OK,
    SETTLING,
    Balance,
    Divergence,
    ReconResult,
    ThreeWayReconciler,
)

# reconcile_tolerance defaults to $0.50; reconcile_settle_window_seconds to 90 (S4.5d cap).
_WINDOW_NS = 90 * 1_000_000_000
_CAPS = RiskCaps()


def _recon():
    return ThreeWayReconciler(caps=_CAPS)


def _bal(token, shares, *, latest_fill_at=None):
    return Balance(token_id=token, shares=Decimal(shares), latest_fill_at=latest_fill_at)
```

> Implementer: before writing code, confirm S4.5b has already landed `reconcile.py` with `Balance`, `Divergence`, `ReconResult`, and the four string constants (this plan sequences S4.5b first). `RiskCaps()` carries `reconcile_settle_window_seconds` once S4.5d Task 15 lands; this plan orders the cap-field task ahead of this consumer (see Acceptance note on execution ordering) so the attribute exists when these tests run. Pin `_WINDOW_NS` to 90 s and read `caps.reconcile_settle_window_seconds`. Do NOT redefine the cap.

---

### Task 9: DORMANT when there is no chain truth (`wallet=None`)

**Files:** Modify `src/polybot/ers/reconcile.py` (add `class ThreeWayReconciler` with `__init__(self, *, caps)` and `reconcile(...)`) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test** (append to the shared header above)

```python
def test_wallet_none_is_dormant_not_a_divergence():
    """No wallet => no chain truth => DORMANT (shadow-clean), even though internal holds
    positions the empty chain would otherwise 'diverge' against. The dormant_no_wallet trigger
    is always recorded; nothing is reported as a divergence."""
    internal = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, None, wallet=None, now=0)
    assert result.status == DORMANT
    assert result.divergences == ()
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal(0)
    assert result.triggers == ("dormant_no_wallet",)


def test_onchain_none_with_a_wallet_is_still_dormant():
    """Even with a wallet set, a None on-chain leg (the DORMANT sentinel from onchain_balances)
    means there is no chain to reconcile against -> DORMANT, not a false DIVERGED."""
    internal = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, None, wallet="0xabc", now=0)
    assert result.status == DORMANT
    assert result.triggers == ("dormant_no_wallet",)
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_wallet_none_is_dormant_not_a_divergence -v`
  Expected RED: `ImportError: cannot import name 'ThreeWayReconciler' from 'polybot.ers.reconcile'` (if S4.5b left the class out) or, once the class stub exists, `AttributeError`/`TypeError` on `.reconcile`. OBSERVE the true RED — it must be the missing `ThreeWayReconciler`, not a fixture typo.

- [ ] **Step 3: Minimal implementation** (add to `src/polybot/ers/reconcile.py`, after the S4.5b dataclasses/constants)

```python
class ThreeWayReconciler:
    """Pure three-way reconcile (S4.5c). On-chain is AUTHORITATIVE; CLOB advisory; default = HOLD.

    Returns DORMANT when there is no wallet / no chain leg (shadow); otherwise compares the
    internal ledger against the on-chain set per token_id over the UNION of all three legs
    (orphans on any leg surface as the absent leg's 0 shares). A per-token share-delta valued at
    the $1 outcome-resolution ceiling that exceeds caps.reconcile_tolerance, and whose internal
    fill is NOT inside the settle-window, is a DIVERGED divergence."""

    def __init__(self, *, caps):
        self._caps = caps

    def reconcile(self, internal, clob, onchain, *, wallet, now):
        if wallet is None or onchain is None:
            return ReconResult(
                status=DORMANT,
                divergences=(),
                onchain_confirmed_exposure=Decimal(0),
                settling_tokens=(),
                triggers=("dormant_no_wallet",),
            )
        window_ns = self._caps.reconcile_settle_window_seconds * 1_000_000_000
        divergences = []
        settling = []
        triggers = []
        for token_id in internal.keys() | clob.keys() | onchain.keys():
            i = internal.get(token_id)
            o = onchain.get(token_id)
            si = i.shares if i is not None else Decimal(0)
            so = o.shares if o is not None else Decimal(0)
            d_dollars = abs(si - so) * Decimal(1)
            if d_dollars <= self._caps.reconcile_tolerance:
                continue
            if i is not None and i.latest_fill_at is not None and (now - i.latest_fill_at) < window_ns:
                settling.append(token_id)
                triggers.append(f"settling:{token_id}")
                continue
            divergences.append(Divergence(
                token_id=token_id,
                internal_shares=si,
                onchain_shares=so,
                dollars=d_dollars,
            ))
            c = clob.get(token_id)
            if c is not None and c.shares == so:
                triggers.append(f"clob_confirms_chain:{token_id}")
        onchain_confirmed_exposure = sum(
            (b.shares * Decimal(1) for b in onchain.values()), Decimal(0)
        )
        if divergences:
            status = DIVERGED
        elif settling:
            status = SETTLING
        else:
            status = OK
        return ReconResult(
            status=status,
            divergences=tuple(divergences),
            onchain_confirmed_exposure=onchain_confirmed_exposure,
            settling_tokens=tuple(settling),
            triggers=tuple(triggers),
        )
```

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — expect both DORMANT tests PASS. Then full suite: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` — expect exit=0 and the dot-count to have GROWN past the running baseline (536 + 2 = **538**), never shrunk.

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/reconcile.py tests/test_ers_reconcile_verdict.py && git commit -m "feat(S4.5c/POL-6): ThreeWayReconciler.reconcile DORMANT path (wallet/onchain None -> shadow-clean)"`

---

### Task 10: OK when internal agrees with on-chain within tolerance

**Files:** Modify `src/polybot/ers/reconcile.py` (no new code expected — verdict path) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test**

```python
def test_internal_equals_onchain_within_tolerance_is_ok():
    """When every token's internal shares equal the on-chain shares (delta 0 <= $0.50 tol),
    the verdict is OK with no divergences; onchain_confirmed_exposure sums the on-chain shares
    at the $1/share resolution ceiling."""
    internal = {"t1": _bal("t1", "10"), "t2": _bal("t2", "3")}
    onchain = {"t1": _bal("t1", "10"), "t2": _bal("t2", "3")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == OK
    assert result.divergences == ()
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal("13")


def test_sub_tolerance_delta_is_still_ok():
    """A share-delta valued under the $0.50 reconcile_tolerance (0.4 shares = $0.40) does NOT
    diverge -- the tolerance band is inclusive at the boundary's interior."""
    internal = {"t1": _bal("t1", "10.4")}
    onchain = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == OK
    assert result.divergences == ()
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_internal_equals_onchain_within_tolerance_is_ok -v`
  Expected: PASS immediately if Task 9's implementation is complete (this is a confirming test for the OK branch already written). If it does NOT pass, OBSERVE the failure (e.g. wrong `onchain_confirmed_exposure` sum) — the impl from Task 9 fully covers OK, so a RED here means a real bug to fix minimally. This task is primarily test-coverage of an existing branch; no new production code is expected.

- [ ] **Step 3: Minimal implementation**
  None expected — the OK branch and `onchain_confirmed_exposure` sum already exist from Task 9. If Step 2 was RED, apply the minimal fix to make `onchain_confirmed_exposure` sum `b.shares * Decimal(1)` over `onchain.values()` and to treat `d_dollars <= reconcile_tolerance` as agreement (already present). Do not add code if green.

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — all four tests so far PASS. Full suite count grows by 2 (→ **540**) vs the post-Task-9 baseline.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_reconcile_verdict.py && git commit -m "test(S4.5c/POL-6): OK verdict when internal==onchain within reconcile_tolerance; exposure sum"`

---

### Task 11: DIVERGED — the headline injected-divergence (internal N shares, on-chain 0)

**Files:** Modify `src/polybot/ers/reconcile.py` (verdict path, no new code expected) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test** (this is the **acceptance-criterion** test — mutation-resistant: pins `status` AND the exact `Divergence`)

```python
def test_injected_divergence_internal_holds_onchain_empty_is_diverged():
    """HEADLINE acceptance criterion: the ERS believes it holds 7 shares of a token the chain
    shows ZERO of (wallet injected). The 7-share gap valued at the $1 resolution ceiling = $7.00
    > $0.50 tolerance, and the internal fill is replayed (latest_fill_at=None -> no settle grace)
    -> DIVERGED, with a single Divergence pinning internal=7, onchain=0, dollars=$7.00."""
    internal = {"t1": _bal("t1", "7", latest_fill_at=None)}
    onchain = {}  # chain shows nothing for t1
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("7"),
                   onchain_shares=Decimal("0"), dollars=Decimal("7")),
    )
    assert result.settling_tokens == ()
    assert result.onchain_confirmed_exposure == Decimal("0")


def test_just_over_tolerance_diverges():
    """A 0.6-share gap = $0.60 > the $0.50 tolerance with a replayed fill -> DIVERGED (the band
    is exclusive just past the tolerance), pinning dollars=$0.60."""
    internal = {"t1": _bal("t1", "10.6", latest_fill_at=None)}
    onchain = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences[0].dollars == Decimal("0.6")
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_injected_divergence_internal_holds_onchain_empty_is_diverged -v`
  Expected: PASS with the Task-9 implementation (the DIVERGED branch + `Divergence` construction already exist). If RED, OBSERVE the exact mismatch — e.g. a `Divergence` field-order/name error or a missing `* Decimal(1)` dollars term — and fix minimally. This test is the load-bearing mutation gate; it must FAIL if the divergence branch is broken.

- [ ] **Step 3: Minimal implementation**
  None expected — covered by Task 9. If Step 2 was RED, correct only the `Divergence(...)` construction (`internal_shares`/`onchain_shares`/`dollars`) and the `d_dollars` computation to match the pinned shape.

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — all PASS. Full-suite count grows by 2 (→ **542**).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_reconcile_verdict.py && git commit -m "test(S4.5c/POL-6): headline injected-divergence (internal N / onchain 0) -> DIVERGED with pinned Divergence"`

---

### Task 12: Orphan detection via the union (on-chain-only and internal-only)

**Files:** Modify `src/polybot/ers/reconcile.py` (verdict path, no new code expected) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test**

```python
def test_onchain_only_orphan_diverges():
    """A token present ONLY on-chain (the internal ledger never recorded it) is an orphan: the
    union iteration sees internal=absent=0 vs onchain=5 -> $5 > tol -> DIVERGED, pinning
    internal=0, onchain=5, dollars=$5."""
    internal = {}
    onchain = {"t1": _bal("t1", "5")}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("0"),
                   onchain_shares=Decimal("5"), dollars=Decimal("5")),
    )
    assert result.onchain_confirmed_exposure == Decimal("5")


def test_internal_only_orphan_past_window_diverges():
    """A token present ONLY internally with a REPLAYED fill (latest_fill_at=None -> no grace) and
    nothing on-chain is the inverse orphan -> DIVERGED, internal=4, onchain=0, dollars=$4."""
    internal = {"t1": _bal("t1", "4", latest_fill_at=None)}
    onchain = {}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("4"),
                   onchain_shares=Decimal("0"), dollars=Decimal("4")),
    )
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_onchain_only_orphan_diverges -v`
  Expected: PASS with the Task-9 union iteration (`internal.keys() | clob.keys() | onchain.keys()`). If RED, the likely cause is iterating only `internal.keys()` (orphan missed) — OBSERVE that the on-chain-only token never appears, then fix the loop to iterate the UNION. This is the mutation gate proving the union, not a single-leg, drives detection.

- [ ] **Step 3: Minimal implementation**
  None expected — the union is in Task 9. If Step 2 was RED (loop iterated a single leg), change the loop header to `for token_id in internal.keys() | clob.keys() | onchain.keys():` and re-run.

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — all PASS. Full-suite count grows by 2 (→ **544**).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_reconcile_verdict.py && git commit -m "test(S4.5c/POL-6): union-driven orphan detection (onchain-only and internal-only both DIVERGE)"`

---

### Task 13: SETTLING — the settle-window exemption and its boundary

**Files:** Modify `src/polybot/ers/reconcile.py` (verdict path, no new code expected) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test** (three concerns, but they form ONE invariant — the window's two sides + the replayed case; keep them as three tight functions)

```python
def test_in_session_fill_inside_window_is_settling_not_diverged():
    """An in-session internal fill (latest_fill_at set, now - latest_fill_at < 90s window) that
    the chain has not confirmed yet is SETTLING, NOT DIVERGED -- expected in-flight state. With
    now = _WINDOW_NS and latest_fill_at = 1ns, age = _WINDOW_NS-1 < window -> exempt."""
    internal = {"t1": _bal("t1", "9", latest_fill_at=1)}
    onchain = {}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=_WINDOW_NS)
    assert result.status == SETTLING
    assert result.divergences == ()
    assert result.settling_tokens == ("t1",)
    assert "settling:t1" in result.triggers


def test_same_fill_aged_past_window_flips_to_diverged():
    """The SAME unconfirmed fill, once its age reaches the window (age == window is NOT < window),
    loses the exemption and DIVERGES -- a SETTLING token can never permanently mask a real
    divergence. now = latest_fill_at + _WINDOW_NS -> age == window -> DIVERGED."""
    internal = {"t1": _bal("t1", "9", latest_fill_at=1)}
    onchain = {}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=1 + _WINDOW_NS)
    assert result.status == DIVERGED
    assert result.settling_tokens == ()
    assert result.divergences == (
        Divergence(token_id="t1", internal_shares=Decimal("9"),
                   onchain_shares=Decimal("0"), dollars=Decimal("9")),
    )


def test_replayed_fill_latest_fill_at_none_gets_no_grace():
    """A replayed/pre-restart fill carries latest_fill_at=None (a prior monotonic epoch is not
    comparable to this `now`), so it receives NO settle-window grace -> DIVERGED even at now=0."""
    internal = {"t1": _bal("t1", "9", latest_fill_at=None)}
    onchain = {}
    result = _recon().reconcile(internal, {}, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert result.settling_tokens == ()
    assert result.divergences[0].token_id == "t1"
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_in_session_fill_inside_window_is_settling_not_diverged -v`
  Expected: PASS with Task-9's settle-window guard `i.latest_fill_at is not None and (now - i.latest_fill_at) < window_ns`. If RED, the boundary is the suspect: a `<=` instead of `<` would make `test_same_fill_aged_past_window_flips_to_diverged` fail. OBSERVE which side breaks and fix the comparator to strict `<` (age == window must DIVERGE). The `latest_fill_at is not None` clause is what makes the replayed case DIVERGE — verify it's present.

- [ ] **Step 3: Minimal implementation**
  None expected — the guard is in Task 9. If Step 2 was RED, the only minimal fix is the settle guard line:
  ```python
  if i is not None and i.latest_fill_at is not None and (now - i.latest_fill_at) < window_ns:
      settling.append(token_id)
      triggers.append(f"settling:{token_id}")
      continue
  ```
  (strict `<`, explicit `is not None` on `latest_fill_at`). Re-run.

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — all PASS. Full-suite count grows by 3 (→ **547**).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_reconcile_verdict.py && git commit -m "test(S4.5c/POL-6): settle-window exemption -- in-window SETTLING, aged-out DIVERGED, replayed(None) no-grace"`

---

### Task 14: CLOB is advisory — a CLOB-only mismatch does not halt

**Files:** Modify `src/polybot/ers/reconcile.py` (verdict path, no new code expected) / Test `tests/test_ers_reconcile_verdict.py`

- [ ] **Step 1: Write the failing test**

```python
def test_clob_only_mismatch_does_not_halt():
    """On-chain is authoritative; CLOB advisory. Internal and on-chain AGREE (both 10 shares),
    but the CLOB/positions leg disagrees (3 shares -- e.g. post-redemption lossy /positions).
    The verdict is OK: a CLOB-only mismatch never drives a halt and never appears as a
    Divergence."""
    internal = {"t1": _bal("t1", "10")}
    clob = {"t1": _bal("t1", "3")}
    onchain = {"t1": _bal("t1", "10")}
    result = _recon().reconcile(internal, clob, onchain, wallet="0xabc", now=0)
    assert result.status == OK
    assert result.divergences == ()
    assert result.settling_tokens == ()


def test_clob_confirms_chain_trigger_on_a_real_divergence():
    """When a genuine on-chain divergence exists AND the CLOB leg corroborates the chain
    (clob shares == onchain shares), the advisory cross-check records a clob_confirms_chain
    trigger -- evidence the chain truth is doubly-attested -- without changing the DIVERGED
    verdict (still driven by on-chain)."""
    internal = {"t1": _bal("t1", "9", latest_fill_at=None)}
    clob = {"t1": _bal("t1", "0")}
    onchain = {"t1": _bal("t1", "0")}
    result = _recon().reconcile(internal, clob, onchain, wallet="0xabc", now=0)
    assert result.status == DIVERGED
    assert "clob_confirms_chain:t1" in result.triggers
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py::test_clob_only_mismatch_does_not_halt -v`
  Expected: PASS — Task-9's loop computes `d_dollars` from `internal` vs `onchain` only; `clob` enters solely the advisory `clob_confirms_chain` trigger. If RED (e.g. `clob` was folded into the divergence math), OBSERVE the spurious divergence and remove `clob` from the share-delta computation — `clob` must be read ONLY for the advisory trigger. This is the mutation gate proving on-chain authority.

- [ ] **Step 3: Minimal implementation**
  None expected — Task 9 already keeps `clob` out of the divergence math. If Step 2 was RED, ensure `si`/`so` derive only from `internal`/`onchain`, and `clob.get(token_id)` is consulted only inside the `clob_confirms_chain` trigger block.

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_reconcile_verdict.py -v` — all PASS. Then run the FULL suite a final time: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` — expect exit=0 and the dot-count strictly grown over the 517 baseline (→ **549**), never shrunk.

- [ ] **Step 5: Commit**
  `git add tests/test_ers_reconcile_verdict.py && git commit -m "test(S4.5c/POL-6): on-chain authoritative -- CLOB-only mismatch is OK; clob_confirms_chain advisory trigger"`

---

**Notes for the implementer (S4.5c):**
- The production code lands entirely in **Task 9**; Tasks 10–14 are characterization/mutation tests that lock each branch of the pure verdict and must each be run RED-first. If any of Tasks 10–14 is unexpectedly RED, that is a real Task-9 bug — fix it minimally under that task, do not paper over it.
- All `Decimal` math; `now` and `latest_fill_at` are `int` monotonic-ns; no floats anywhere.
- Do **not** redefine `Balance` / `Divergence` / `ReconResult` / `OK` / `DIVERGED` / `SETTLING` / `DORMANT` — they are owned by S4.5b and imported.
- `Divergence` field names per the pinned contract: `token_id`, `internal_shares`, `onchain_shares`, `dollars`. `ReconResult`: `status`, `divergences`, `onchain_confirmed_exposure`, `settling_tokens`, `triggers`.
- Mutation checks for the code-review gate: (1) flip the settle comparator `<`→`<=` ⇒ `test_same_fill_aged_past_window_flips_to_diverged` must FAIL; (2) drop the `latest_fill_at is not None` clause ⇒ `test_replayed_fill_latest_fill_at_none_gets_no_grace` must FAIL; (3) iterate only `internal.keys()` ⇒ `test_onchain_only_orphan_diverges` must FAIL; (4) fold `clob` into the share-delta ⇒ `test_clob_only_mismatch_does_not_halt` must FAIL; (5) reach DORMANT with a wallet set ⇒ `test_wallet_none_is_dormant_not_a_divergence` / `test_onchain_none_with_a_wallet_is_still_dormant` discriminate it.

---

## Sub-slice S4.5d — the cap field + safety reason constants + RestartReconciler

> **Dependency note for the implementer:** this sub-slice builds *on top of* S4.5a (`IntentStore.record_fill` / `fills_log` / `accepted`, `service.make_fill_sink`, the `fill_sink=` seam) and S4.5b/c (`ers/reconcile.py`: `internal_balances`, `clob_balances`, `onchain_balances`, `ThreeWayReconciler`, `OK/DIVERGED/SETTLING/DORMANT`, `Balance`). Do not re-implement those symbols here — import them. Run the FULL `./.venv/bin/pytest` after the prior sub-slices land and confirm the baseline grew before starting Task 15 of this section.
>
> **Execution-ordering note:** `ThreeWayReconciler.reconcile` (S4.5c Task 9) reads `caps.reconcile_settle_window_seconds`, which is ADDED in Task 15 below. When executing the whole plan top-to-bottom, run **Task 15 (the cap field) before the S4.5c verdict tasks** so the attribute exists, OR land Task 15 first and then return to S4.5c — the tasks are otherwise independent. The default of 90 means `_WINDOW_NS` in S4.5c is correct either way.

---

### Task 15: `caps.py` — add `reconcile_settle_window_seconds` field (frozen, `_verify > 0`, content_hash-covered)

**Files:** Modify `src/polybot/ers/caps.py` (add field ~line 59 after `reconcile_tolerance`; add name to the strictly-positive-int loop ~line 144) / Test `tests/test_ers_caps.py` (append after `test_non_positive_new_field_fails_verify`, ~line 173)

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_settle_window_default_and_hashed():
    # S4.5d: the settle-window is a hashed, _verify-checked RiskCaps field (default 90s); changing
    # it changes the signed envelope's content_hash (so a tamper to the window is detectable).
    caps = RiskCaps()
    assert caps.reconcile_settle_window_seconds == 90
    base = RiskCaps().content_hash()
    tweaked = RiskCaps(reconcile_settle_window_seconds=60).content_hash()
    assert base != tweaked


def test_reconcile_settle_window_non_positive_fails_verify():
    # A non-positive settle-window is a wiring error: fail loud at construction (joins the
    # strictly-positive-int loop alongside clock_skew_tolerance_seconds etc).
    with pytest.raises(ValueError, match="reconcile_settle_window_seconds|> 0"):
        RiskCaps(reconcile_settle_window_seconds=0)
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_caps.py::test_reconcile_settle_window_default_and_hashed tests/test_ers_caps.py::test_reconcile_settle_window_non_positive_fails_verify -v`
  Expected RED: `TypeError: __init__() got an unexpected keyword argument 'reconcile_settle_window_seconds'` (both tests — the field does not exist yet).

- [ ] **Step 3: Minimal implementation**

  In `src/polybot/ers/caps.py`, add the field immediately after the `reconcile_tolerance` line (currently ~line 59):

```python
    reconcile_tolerance: Decimal = Decimal("0.50")     # 3-way divergence tolerance (settle-window-aware)
    reconcile_settle_window_seconds: int = 90          # internal-fill age under which a not-yet-on-chain
    # token is SETTLING (exempt from the divergence halt), NOT DIVERGED. Tighten-only: a future S4.7
    # ratchet may only DECREASE it. Added + hashed + _verify'd here (the guard enforcement is S4.7).
```

  And extend the existing strictly-positive-int loop in `_verify` (currently ends `"dead_man_switch_timeout_seconds"):`) to include the new name:

```python
        for name in ("consecutive_loss", "new_positions_per_hour", "new_positions_per_day",
                     "clock_skew_tolerance_seconds", "signing_canary_interval_seconds",
                     "dead_man_switch_timeout_seconds", "reconcile_settle_window_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")
```

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_caps.py -v` → all PASS (the two new + the pre-existing relational `content_hash` tests stay green; no literal hash is pinned anywhere, so adding a hashed field needs no digest update).
  Then full: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, dot-count = (prior-sub-slice baseline) **+2** (→ **551**), never lower.

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/caps.py tests/test_ers_caps.py && git commit -m "feat(caps): add reconcile_settle_window_seconds (frozen, _verify>0, hashed)"`

---

### Task 16: `safety.py` — add `REASON_L5_RECON_MISMATCH` + `REASON_RESTART_RECONCILED` constants

**Files:** Modify `src/polybot/ers/safety.py` (add two module-level constants after `REASON_UNCLEAN_RESTART`, ~line 35) / Test `tests/test_ers_safety.py` (append a trivial import-and-value test)

- [ ] **Step 1: Write the failing test**

```python
def test_s4_5_reason_constants_exist():
    # S4.5d defines the two NET-NEW op-state reason codes here: the running-cadence recon-mismatch
    # reason (S4.4 consumes it) and the clean HALTED->RUNNING restart-reconciled reason. Both are
    # free-form strings (NO validator/schema change), matching the existing REASON_* convention.
    from polybot.ers import safety as _s
    assert _s.REASON_L5_RECON_MISMATCH == "l5_recon_mismatch"
    assert _s.REASON_RESTART_RECONCILED == "restart_reconciled"
```

  > Add `import` for whatever the file's existing tests already import (the module is imported `as _s` locally inside this test, so no top-of-file edit is needed; mirror the existing `tests/test_ers_safety.py` import style if it differs).

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_safety.py::test_s4_5_reason_constants_exist -v`
  Expected RED: `AttributeError: module 'polybot.ers.safety' has no attribute 'REASON_L5_RECON_MISMATCH'`.

- [ ] **Step 3: Minimal implementation**

  In `src/polybot/ers/safety.py`, add immediately after the `REASON_UNCLEAN_RESTART = "unclean_restart"` line:

```python
REASON_UNCLEAN_RESTART = "unclean_restart"
# --- S4.5 reason codes (NET-NEW; free-form Decision.reason / op-audit strings) ----------------
REASON_L5_RECON_MISMATCH = "l5_recon_mismatch"     # running-cadence recon-mismatch (S4.4 consumer)
REASON_RESTART_RECONCILED = "restart_reconciled"   # the clean restart-reconcile HALTED->RUNNING reason
```

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_safety.py -v` → all PASS.
  Full: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, dot-count grew by **1** (→ **552**).

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/safety.py tests/test_ers_safety.py && git commit -m "feat(safety): add l5_recon_mismatch + restart_reconciled reason codes"`

---

### Task 17: `ers/restart.py` — `RestartReconciler`, DORMANT (wallet=None) → RUNNING + portfolio rebuilt from ACCEPTED

**Files:** Create `src/polybot/ers/restart.py` / Test Create `tests/test_ers_restart.py`

- [ ] **Step 1: Write the failing test** (self-contained fixtures — no `conftest.py` exists)

```python
"""RestartReconciler boot state machine (S4.5d / POL-6).

At boot the controller starts HALTED(unclean_restart). The restart-reconcile is the ONLY automatic
path to RUNNING: it replays the durable stores, three-way-reconciles, rebuilds the Portfolio, and
flips HALTED->RUNNING *only* on a clean (OK/DORMANT) result. Crash defaults to HOLD. wallet=None is
DORMANT (pure shadow: no chain truth) -> treated as clean -> RUNNING, portfolio rebuilt from the
internal ACCEPTED set. Clocks are injected (monotonic-ns); money is Decimal.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.reconcile import ThreeWayReconciler
from polybot.ers.restart import RestartReconciler
from polybot.ers.safety import SafetyController
from polybot.ers.service import make_fill_sink
from polybot.ers.validator import Decision, Portfolio
from polybot.storage.market_memory import EventStore

_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _accept_one(store, intent_id="i1", **over):
    # Drive one intent to ACCEPTED + record its fill, mirroring what process_pending+make_fill_sink
    # do on an ACCEPT: stake $8 of a token entered at $0.50 -> 16 shares, $8 worst-case risk.
    store.propose_trade(intent_id, **dict(_P, **over))
    store.record_decision(intent_id, Decision("ACCEPT", Decimal("8"), Decimal("0.50"), "kelly"))
    store.record_fill(intent_id=intent_id, token_id=over.get("token_id", "t1"),
                      condition_id=over.get("condition_id", "m1"),
                      event_id=over.get("event_id", "e1"), side="BUY",
                      shares=Decimal("16"), price_exec=Decimal("0.50"),
                      worst_case_risk=Decimal("8"))


def test_dormant_no_wallet_transitions_running_and_rebuilds_portfolio(tmp_path):
    # wallet=None => DORMANT (pure shadow). The RestartReconciler transitions the HALTED controller
    # to RUNNING(restart_reconciled) and returns a Portfolio rebuilt from the ACCEPTED rows.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        assert ctl.state() == _safety.HALTED  # boot default
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 0, wallet=None)
        portfolio = rr.reconcile_on_boot()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
        assert isinstance(portfolio, Portfolio)
        assert [p.token_id for p in portfolio.positions] == ["t1"]
        pos = portfolio.positions[0]
        assert pos.worst_case_risk == Decimal("8")
        assert pos.entry_price == Decimal("0.50")
        assert pos.condition_id == "m1" and pos.event_id == "e1"
    finally:
        store.close()
        events.close()
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_restart.py::test_dormant_no_wallet_transitions_running_and_rebuilds_portfolio -v`
  Expected RED: `ModuleNotFoundError: No module named 'polybot.ers.restart'` (then, after the module skeleton exists but `reconcile_on_boot` is unwritten, `AttributeError`/`NotImplementedError`). Observe the TRUE red before writing code.

- [ ] **Step 3: Minimal implementation** — create `src/polybot/ers/restart.py`:

```python
"""RestartReconciler — the boot state machine (S4.5d / POL-6).

At process boot the SafetyController is HALTED(unclean_restart). reconcile_on_boot() is the ONLY
automatic HALTED->RUNNING path: it folds the durable internal fills ledger (replayed, NO settle
grace), the CLOB /positions leg, and the authoritative on-chain leg, three-way-reconciles, rebuilds
the Portfolio, and transitions RUNNING *only* on a clean (OK/DORMANT) result. Anything else stays
HALTED(unclean_restart) -> a human reconciles. Crash = HOLD. wallet=None => DORMANT (pure shadow,
no chain truth) => treated as clean => RUNNING, portfolio rebuilt from the internal ACCEPTED set.

The live on-chain-confirmed ∩ ACCEPTED rebuild is DEFERRED to POL-4 (a funded clean-box wallet);
the per-cycle running-cadence reconcile is DEFERRED to S4.4. This slice produces the pure result
and drives the boot transition only.
"""

from polybot.ers.reconcile import (
    DORMANT,
    OK,
    clob_balances,
    internal_balances,
    onchain_balances,
)
from polybot.ers.safety import (
    HALTED,
    REASON_RESTART_RECONCILED,
    REASON_UNCLEAN_RESTART,
    RUNNING,
)
from polybot.ers.validator import OpenPosition, Portfolio


class RestartReconciler:
    def __init__(self, *, store, event_store, reconciler, controller, caps, clock, wallet=None):
        # clock: a 0-arg callable returning a monotonic-ns now (time.monotonic_ns in prod; a fixed
        # int in tests) -- SAME domain as MonotonicStamper.stamp(), so the reconciler's settle-window
        # arithmetic is unit-consistent.
        self._store = store
        self._event_store = event_store
        self._reconciler = reconciler
        self._controller = controller
        self._caps = caps
        self._clock = clock
        self._wallet = wallet

    def reconcile_on_boot(self):
        # Replayed rows: in_session=False => latest_fill_at=None => NO settle-window grace (a prior
        # monotonic epoch is NOT comparable to this process's now; an unconfirmed pre-restart fill is
        # fail-closed DIVERGED, never SETTLING).
        internal = internal_balances(self._store.fills_log(), in_session=False)
        envs = self._event_store.all()
        clob = clob_balances(envs)
        onchain = onchain_balances(envs, wallet=self._wallet)
        result = self._reconciler.reconcile(
            internal, clob, onchain, wallet=self._wallet, now=self._clock())
        portfolio = self._rebuild_portfolio()
        if result.status in (OK, DORMANT):
            # The ONLY automatic HALTED->RUNNING transition.
            self._controller.set_state(RUNNING, reason=REASON_RESTART_RECONCILED)
        else:
            # DIVERGED / SETTLING / orphan -> stay HALTED; a human must reconcile. Never auto-resume.
            self._controller.set_state(HALTED, reason=REASON_UNCLEAN_RESTART)
        return portfolio

    def _rebuild_portfolio(self):
        # DORMANT/shadow path: rebuild from the internal ACCEPTED rows (there is no chain to confirm
        # against in shadow). The live on-chain-confirmed ∩ ACCEPTED rebuild is DEFERRED to POL-4.
        positions = tuple(
            OpenPosition(
                condition_id=r.condition_id, event_id=r.event_id,
                resolution_source=r.condition_id, cluster_id=r.event_id,
                worst_case_risk=r.decision_stake_usd, token_id=r.token_id,
                entry_price=r.decision_price_exec, matrix_cold=True, frozen=False)
            for r in self._store.accepted()
        )
        return Portfolio(nav=self._caps.nav, positions=positions)
```

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_restart.py::test_dormant_no_wallet_transitions_running_and_rebuilds_portfolio -v` → PASS.
  Full: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, dot-count grew by **1** (→ **553**).

- [ ] **Step 5: Commit**
  `git add src/polybot/ers/restart.py tests/test_ers_restart.py && git commit -m "feat(restart): RestartReconciler DORMANT-shadow boot path -> RUNNING + portfolio rebuild"`

---

### Task 18: `ers/restart.py` — injected on-chain divergence (wallet set) STAYS HALTED(unclean_restart) *(THE ACCEPTANCE CRITERION)*

**Files:** Modify (no source change expected — the Task-17 impl already handles DIVERGED; this test PROVES it) `src/polybot/ers/restart.py` / Test `tests/test_ers_restart.py` (append)

- [ ] **Step 1: Write the failing test** (add the on-chain Envelope helper at module top, then the test)

```python
import json
from polybot.core.models import Envelope


def _onchain_env(token_id, value, *, wallet, observed_at, kind="transfer_single"):
    # A Polygon ERC-1155 credit TO the wallet: source="polygon-chain", content carries the decoded
    # transfer event in the {"log":.., "event":..} shape onchain_balances parses. For a single
    # transfer the event provides from/to/token_id/value (value is the RAW 6-decimal-scaled int).
    content = json.dumps({"log": {"block": 1},
                          "event": {"kind": kind, "operator": "0xop",
                                    "from": "0x0", "to": wallet,
                                    "token_id": token_id, "value": str(value)}})
    return Envelope(source="polygon-chain", source_tier="CHAIN", event_id=f"chain:{token_id}",
                    observed_at=observed_at, content=content)


def test_injected_onchain_divergence_stays_halted(tmp_path):
    # ACCEPTANCE CRITERION: internal holds 16 shares of t1; the chain (wallet injected) shows a
    # DIFFERENT balance (0 shares of t1 -> a $16 divergence well past the $0.50 tolerance). The
    # replayed fill carries latest_fill_at=None (no boot settle grace) => DIVERGED => the controller
    # STAYS HALTED(unclean_restart). A human must reconcile; never auto-resume.
    wallet = "0xabc"
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)  # internal: 16 shares of t1
        # Chain shows a DIFFERENT token entirely (t2), so t1 is on-chain-absent => 16-share orphan.
        events.append(_onchain_env("t2", 16_000_000, wallet=wallet, observed_at=1))
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 5_000_000_000, wallet=wallet)
        rr.reconcile_on_boot()
        assert ctl.state() == _safety.HALTED
        assert ctl.state() != _safety.RUNNING
        # The boot decision is audited under unclean_restart (NOT restart_reconciled).
        last = store.op_audit_log()[-1]
        assert last["reason"] == "unclean_restart"
        assert all(r["reason"] != "restart_reconciled" for r in store.op_audit_log())
    finally:
        store.close()
        events.close()
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_restart.py::test_injected_onchain_divergence_stays_halted -v`
  Expected: this should PASS directly off the Task-17 impl (the DIVERGED branch already exists). **If it does not fail first, that is fine — but PROVE it is non-vacuous in Step 3 via the mandated mutation.** Before committing, run the mutation: temporarily change the `else` branch in `reconcile_on_boot` to `self._controller.set_state(RUNNING, reason=REASON_RESTART_RECONCILED)` and re-run — observe the test now FAILS on `assert ctl.state() == HALTED`. Revert the mutation immediately.

- [ ] **Step 3: Minimal implementation**
  No new source needed — Task 17's `reconcile_on_boot` already sets `HALTED(unclean_restart)` on a non-(OK/DORMANT) status. The mutation in Step 2 proves the assertion is load-bearing. (If, and only if, the test reveals a real gap, the minimal fix is to confirm the `else` branch is reached: the `onchain_balances(envs, wallet=wallet)` leg must return a non-DORMANT map when a wallet is set and at least one `polygon-chain` Envelope exists — verified by S4.5b. Do NOT widen the contract here.) Verify the working tree is clean (`git status --porcelain` empty, no stray `MUTATION` markers).

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_restart.py -v` → all PASS.
  Full: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0, dot-count grew by **1** (→ **554**).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_restart.py && git commit -m "test(restart): injected on-chain divergence stays HALTED (acceptance criterion)"`

---

### Task 19: `ers/restart.py` — a clean live reconcile (internal == on-chain, wallet set) → RUNNING

**Files:** Modify (no source change expected — proves the OK branch under a live wallet) `src/polybot/ers/restart.py` / Test `tests/test_ers_restart.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_clean_live_reconcile_transitions_running(tmp_path):
    # Live (wallet injected) AND internal == on-chain within tolerance => OK => the controller
    # transitions HALTED->RUNNING(restart_reconciled). This proves DORMANT is NOT the only RUNNING
    # path: a genuine clean 3-way match also resumes. (Distinguishes a real clean chain from shadow.)
    wallet = "0xabc"
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)  # internal: 16 shares of t1
        # Chain shows EXACTLY 16 shares of t1 to the wallet (16 * 10**6 raw, 6-decimal scaled).
        events.append(_onchain_env("t1", 16_000_000, wallet=wallet, observed_at=1))
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 5_000_000_000, wallet=wallet)
        rr.reconcile_on_boot()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
    finally:
        store.close()
        events.close()
```

- [ ] **Step 2: Run it, watch it fail for the RIGHT reason**
  `./.venv/bin/pytest tests/test_ers_restart.py::test_clean_live_reconcile_transitions_running -v`
  Expected: PASS off the Task-17 impl. PROVE non-vacuous via mutation: temporarily change `if result.status in (OK, DORMANT):` to `if result.status in (DORMANT,):` and re-run — observe the test FAILS (state stays HALTED). Revert immediately. (This guards that the live-OK branch genuinely resumes and isn't accidentally only the DORMANT path.)

- [ ] **Step 3: Minimal implementation**
  No new source — Task 17 already covers `OK -> RUNNING`. The mutation proves the `OK` membership is load-bearing. Confirm the `_SHARE_DECIMALS = 6` scaling in `onchain_balances` (S4.5b) yields exactly `Decimal("16")` from raw `16_000_000`, so `|16 - 16| * $1 = $0 <= $0.50` tolerance → OK. Verify tree clean (`git status --porcelain` empty, no `MUTATION` markers).

- [ ] **Step 4: Run green**
  `./.venv/bin/pytest tests/test_ers_restart.py -v` → all PASS.
  Full: `./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c` → exit=0; confirm the dot-count grew by **1** (→ **555**) and the cumulative S4.5d total is **+6 over the post-S4.5c baseline** (Task 15 +2, Task 16 +1, Tasks 17/18/19 +1 each).

- [ ] **Step 5: Commit**
  `git add tests/test_ers_restart.py && git commit -m "test(restart): clean live 3-way reconcile transitions RUNNING"`

---

## Acceptance & review

- [ ] **Full suite green, baseline grown.** Run `./.venv/bin/pytest` to completion: exit 0, and the **517 baseline GROWN** to **555** (S4.5a +8 → 525; S4.5b +11 → 536; S4.5c +13 → 549; S4.5d +6 → 555). Confirm the dot-count only ever grew across every task and never shrank:
  ```
  wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -q > /tmp/t.txt 2>&1; echo exit=$?; tr -cd "." < /tmp/t.txt | wc -c'
  ```
- [ ] **The headline safety tests pass.** Explicitly confirm: the injected-divergence acceptance criterion (`tests/test_ers_restart.py::test_injected_onchain_divergence_stays_halted` and `tests/test_ers_reconcile_verdict.py::test_injected_divergence_internal_holds_onchain_empty_is_diverged`), the DORMANT path (`test_wallet_none_is_dormant_not_a_divergence`, `test_onchain_none_with_a_wallet_is_still_dormant`, `test_dormant_no_wallet_transitions_running_and_rebuilds_portfolio`), and the settle-window pair (`test_in_session_fill_inside_window_is_settling_not_diverged` + `test_same_fill_aged_past_window_flips_to_diverged` + `test_replayed_fill_latest_fill_at_none_gets_no_grace`) all PASS.
- [ ] **Two pinned-opus code-review passes, in order.** First run the **spec-compliance reviewer** (superpowers:requesting-code-review) against this plan's pinned contract — verify every new symbol matches the contract exactly (`record_fill`/`fills_log`/`accepted`, the `fill_sink=` seam + `make_fill_sink`, the `reconcile.py` parsers + `ThreeWayReconciler.reconcile`, `RestartReconciler`, the cap field, the two safety reason constants), with NO method-name drift across sections and NO placeholders left. THEN run a **pinned-opus** superpowers:code-reviewer pass for bugs/regressions/hallucinations. Both must come back clean (no CRITICAL/HIGH) before merge.
- [ ] **Mutation-test the safety-critical tests.** Apply each mutation and confirm the named test FAILS, then revert: (1) settle comparator `<`→`<=` ⇒ `test_same_fill_aged_past_window_flips_to_diverged` FAILS; (2) drop the `latest_fill_at is not None` clause ⇒ `test_replayed_fill_latest_fill_at_none_gets_no_grace` FAILS; (3) iterate only `internal.keys()` ⇒ `test_onchain_only_orphan_diverges` FAILS; (4) fold `clob` into the share-delta ⇒ `test_clob_only_mismatch_does_not_halt` FAILS; (5) `reconcile_on_boot` `else`-branch → RUNNING ⇒ `test_injected_onchain_divergence_stays_halted` FAILS; (6) restart resume set `(OK, DORMANT)`→`(DORMANT,)` ⇒ `test_clean_live_reconcile_transitions_running` FAILS.
- [ ] **Tree clean, no stray markers.** Confirm `git status --porcelain` is EMPTY and there are no stray `MUTATION` markers left anywhere:
  ```
  wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git status --porcelain; grep -rn "MUTATION" src tests || echo "no MUTATION markers"'
  ```
- [ ] **Update HANDOFF + memory + a POL-6 comment.** Update `docs/HANDOFF.md` (S4.5 three-way reconcile shipped: durable fills ledger + pure reconciler + restart-reconcile, shadow-only, on-chain-confirmed∩ACCEPTED rebuild + running-cadence L5 deferred to POL-4/S4.4); append the S4.5 milestone to the polymarket-bot memory note (`555` tests, branch `pol-6-s4.5-reconcile`); and post a POL-6 comment summarizing the slice, the two Opus reviews, the mutation results, and the deferrals.
- [ ] **Merge `--no-ff`, then CONFIRM before push.** Merge `pol-6-s4.5-reconcile` into `main` with `--no-ff`. **Do NOT push** — surface the merge result and explicitly CONFIRM with the user before any `git push`.
