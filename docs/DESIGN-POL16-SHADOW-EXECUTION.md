# DESIGN — POL-16: shadow-execution wiring

**Date:** 2026-07-14 · **Ticket:** POL-16 · **Status:** owner-approved contract

## 1. Goal

Connect the existing deterministic ERS ACCEPT path to the existing S9 maker-only simulator and
the Maker/Shadow economic ledgers. A filled paper execution must carry POL-15 canonical settlement
identity, survive process crashes, settle through the existing resolution dispatcher, and expose a
single conservative mark callable.

This slice remains paper-only. It does not schedule a loop, start a service, sign, submit, cancel,
or move funds. POL-17 owns continuous runtime composition; POL-4 owns live execution.

## 2. Resolved forks

| Fork | Decision |
|---|---|
| Price source | Join the freshly re-fetched live **best bid** for the selected outcome token. Never use Hermes's untrusted `target_price`. `decision.price_exec` is the executable ask and would be rejected by the maker-only simulator. |
| Side | Always `BUY` the selected outcome token. A No view is represented by buying the No token; the proposal's `side` cannot choose accounting sign. |
| Size | `shares = decision.stake_usd / resting_price`, preserving the ERS-approved notional at the simulated maker price. Never round up. |
| Unfilled attempt | The ACCEPT decision remains in the intent audit, but no Maker/Shadow economic row is created. |
| Durability | Persist a typed simulated execution and two-role outbox atomically with the ACCEPT decision. Project idempotently into Maker then Shadow. A crash after either target commit replays safely. |
| Terminal race | A typed outbox execution may be applied after a target terminal receipt; the target atomically inserts it already settled from the immutable canonical terminal. Existing general `record_fill`/`record_trade` post-receipt rejection remains unchanged. |
| Mark precedence | A canonical terminal mark dominates. Until terminal, use a fresh `LocalBook.midpoint()`. DISPUTED/MANUAL, stale, absent, non-finite, conflicting, or unknown data returns `None` or fails loud on durable corruption; `adverse_selection` then fails closed. |
| Runtime boundary | Build the adapter, outbox, dispatcher, mark provider, and controller seam only. No cadence, systemd, feed polling, or service activation. |

## 3. Architecture

```text
PROPOSED intent
    │
    ▼
ERS re-validates against live ask and returns ACCEPT(stake)
    │
    ▼
Shadow planner re-fetches book → joins best bid → simulate_fill
    │ filled
    ▼
IntentStore transaction
  decision=ACCEPTED + audit + canonical execution + MAKER/SHADOW outbox
    │
    ▼
ShadowExecutionDispatcher
  MAKER → MakerLedger.apply_shadow_execution
  SHADOW → ShadowLedger.apply_shadow_execution
    │
    ▼
POL-15 ResolutionDispatcher applies the same immutable terminal to both rows
```

The outbox is the recovery authority. Target commit before acknowledgement is safe because target
creation is idempotent and conflict-detecting. A contradictory duplicate is never silently ignored.

## 4. Pinned public contract

```python
# ers/intent_store.py
@dataclass(frozen=True)
class ShadowExecutionRecord:
    execution_id: str
    token_id: str
    condition_id: str
    event_id: str
    category: str
    outcome_slot: int
    sibling_token_ids: tuple[str, str]
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal

@dataclass(frozen=True)
class ShadowExecutionOutboxRecord:
    sequence: int
    role: str
    execution: ShadowExecutionRecord

class IntentStore:
    def record_decision(self, intent_id, decision, *, shadow_execution=None): ...
    def pending_shadow_executions(self, limit): ...
    def acknowledge_shadow_execution(self, sequence, execution_id, role): ...
```

`record_decision` preserves its old behavior when `shadow_execution=None`. A supplied execution is
valid only for ACCEPT, must use the same intent ID and canonical condition/token/event identity, and
is inserted in the same SQLite transaction as the decision and audit. The outbox roles are exactly
`MAKER`, then `SHADOW`.

```python
# harness/execution.py
def make_shadow_execution_planner(*, book_for, subject_for, maker_config):
    # returns callable(intent, decision) -> ShadowExecutionRecord | None

class ShadowExecutionDispatcher:
    def __init__(self, store, maker_ledger, shadow_ledger): ...
    def drain(self, limit) -> int: ...

def make_mark_for(ledger, *, book_for):
    # returns callable(token_id) -> Decimal | None
```

The planner:

1. requires `Decision.verdict == "ACCEPT"` with finite positive stake;
2. resolves a `ResolutionSubjectMetadata` and rechecks all intent IDs;
3. fetches a fresh book and selects its finite best bid in `(0, 1)`;
4. computes exact shares from approved stake;
5. calls the existing `simulate_fill(... side="BUY" ...)`;
6. returns `None` when the maker simulation is unfilled; otherwise returns the canonical record.

```python
# maker/ledger.py and harness/ledger.py
def apply_shadow_execution(self, execution) -> bool:
    # True on new row; False on exact idempotent replay; conflict on contradiction.
    # If a canonical terminal receipt already exists, insert the row already projected to it.
```

The old `record_fill` and `record_trade` APIs keep rejecting post-terminal creation. Only the typed
POL-16 outbox path receives terminal-race recovery behavior.

```python
# ers/service.py
def process_pending(..., shadow_planner=None): ...

# ers/controller.py
class ERSController:
    def __init__(..., shadow_planner=None, ...): ...
```

`shadow_planner=None` is byte-for-byte behavior-compatible: no execution outbox and no target rows.
When wired, planning occurs only after the deterministic ACCEPT verdict and before decision
persistence/signing. A planner exception converts that intent to `REJECT shadow_execution_error`;
it never signs or creates a partial economic row.

## 5. Persistence contract

`shadow_executions` stores exact Decimal strings and canonical sibling JSON. Its primary key is the
intent ID. `shadow_execution_outbox` uses an autoincrement sequence, roles constrained to MAKER or
SHADOW, states constrained to PENDING or DELIVERED, and unique `(execution_id, role)`.

On store open, mixed/invalid identity, malformed Decimal text, impossible role/state, or an outbox
without its execution fails loud. `pending_shadow_executions(limit)` is ordered by sequence and
requires a positive integer limit.

Target replay rules:

- no existing row + no terminal receipt: insert canonical pending row;
- exact existing row: return `False` and acknowledge;
- contradictory existing row: raise `SettlementConflict`, leaving the outbox pending;
- terminal receipt already present: authenticate its canonical bytes and subject, then insert the
  row with the exact terminal ID, payout numerator/denominator, status, value, and settled timestamp;
- receipt subject mismatch or malformed terminal: raise `SettlementConflict`.

## 6. Mark contract

`make_mark_for` reads canonical rows from the supplied Maker or Shadow ledger:

- no row for token: `None`;
- any terminal row: all rows for that token must agree on terminal identity/status/value; return the
  exact resolution value for WON/LOST/SETTLED and `None` for DISPUTED/VOID;
- pending canonical rows only: fetch the live book and return its midpoint if finite in `[0, 1]`;
- stale/missing/crossed book: `None`;
- mixed terminal/pending or contradictory durable rows: fail loud as corruption.

The terminal check comes first, so a post-resolution live book can never override settlement.

## 7. Safety invariants

1. Hermes never selects execution price, side, size, category, or settlement identity.
2. Only ERS ACCEPT can create a shadow execution outbox.
3. Best bid is fetched after ACCEPT; the executable ask is never mislabeled as maker liquidity.
4. Every economic row is canonical and therefore settleable by POL-15.
5. ACCEPT/outbox persistence is atomic; cross-ledger fanout is replayable and idempotent.
6. A target-terminal race produces an already-settled row, never an orphan pending row.
7. Terminal marks dominate live marks; ambiguity fails closed.
8. `shadow_planner=None` preserves every existing call site.
9. `evaluate_intent`, the propose-only facade, signer protocol, caps, and resolution authority remain unchanged.
10. No POL-16 path signs, submits, starts a service, or authorizes live money.

## 8. Acceptance criteria

- Planner tests prove best-bid pricing, approved-notional shares, forced BUY semantics, canonical
  identity, reward delegation, unfilled stale/one-sided/crossed books, and rejection of malformed
  subject/decision data. Mutations to target price, ask price, proposed side, or proposed size die.
- Store tests prove ACCEPT+audit+execution+two outbox rows are one transaction, non-ACCEPT cannot
  enqueue, Decimal/canonical round-trip, restart persistence, sequence order, acknowledgement
  identity, and corrupt/outbox-orphan failure.
- Dispatcher tests prove both targets receive identical economics/identity; crash after Maker commit
  replays Maker idempotently then reaches Shadow; contradictory duplicates remain pending and loud.
- Terminal-race tests prove a terminal before execution replay creates exact already-settled Maker
  and Shadow rows without weakening the legacy post-receipt rejection APIs.
- Mark tests prove live midpoint before settlement, resolution value after settlement, terminal
  precedence, DISPUTED/VOID/stale/missing `None`, and loud durable contradictions.
- Service/controller tests prove planner invocation on ACCEPT only, unfilled ACCEPT audit without
  economic rows, planner error rejection without signer call, and `None` compatibility.
- Whole-slice e2e: proposed intent → ERS ACCEPT → atomic outbox → crash/replay fanout → POL-15
  terminal → both ledgers settled → terminal mark returned; 2,070-test baseline remains green.
- Full suite, compile, diff, clean-tree, independent specification/security review, and adversarial
  mutation battery pass before landing.

## 9. Files and non-goals

Expected implementation surface:

- new `src/polybot/harness/execution.py` and `tests/test_shadow_execution.py`;
- additive changes to `ers/intent_store.py`, `ers/service.py`, `ers/controller.py`;
- additive typed replay methods in `maker/ledger.py` and `harness/ledger.py`;
- focused existing tests plus one whole-slice e2e;
- HANDOFF/TICKETS/verification evidence at final reconciliation.

Explicitly out of scope: continuous run-loop composition, resolution polling cadence, live quote
placement/queue modeling, order signing, service activation, cap promotion, and real money.
