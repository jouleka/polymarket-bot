# POL-15 — D2 resolution and settlement feed

**Status:** owner-approved lean scope 2026-07-13
**Ticket:** POL-15 (child of POL-13)
**Branch:** `pol-15-resolution-settlement`

## 1. Purpose

POL-15 turns finalized Polygon Conditional Tokens Framework (CTF) payouts into immutable,
restart-safe settlement evidence for the existing forecast, maker-fill, and shadow-trade ledgers.
It is read-only with respect to Polygon: it never signs or submits a transaction.

The safety rule is deliberately narrow: Gamma discovers and identifies markets, but only confirmed
CTF state determines payout economics. UMA history classifies whether the path is clean. A condition
with disputed, manual, unsupported, contradictory, or historically unprovable path evidence is
retained as excluded evidence and never becomes a positive calibration or PnL sample.

POL-17 owns continuous scheduling, provider credentials, deployment, and promotion integration.
POL-15 supplies pure classification, a durable feed store, an explicit polling operation, and
idempotent delivery primitives.

## 2. Authority and supported scope

Authority order is:

1. Polygon chain ID `137`.
2. CTF `payoutDenominator` and `payoutNumerators` at one provider-agreed finalized block.
3. Reviewed UMA adapter history for dispute classification.
4. Gamma metadata for candidate discovery and immutable event/condition/token identity only.

The CTF contract address is
`0x4d97dcd97ec945f40cf65f87097ace5ea0476045`. Authority addresses and ABI policy are frozen in code,
never injected from runtime input or learned from a provider response. Supporting a new adapter is a
code, test, design-review, and owner-approval change.

The exact v1 authority registry is code-owned and not caller-extensible:

| Policy ID | Adapter address | Deployment block | Normal event ABI |
|---|---|---:|---|
| `UMA_V1_0_1` | `0xb97455fcf78eb37375e8be6f26df895341ca073d` | `29,838,630` | resolution recognized; never sufficient for `CLEAR` |
| `UMA_V2_0_0` | `0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74` | `34,876,144` | `QuestionResolved(bytes32,int256,uint256[])` |
| `UMA_V3_0_0` | `0x71392e133063cc0d16f40e1f9b60227404bc03f7` | `43,375,847` | `QuestionResolved(bytes32,int256,uint256[])` |
| `UMA_V3_1_0` | `0x157ce2d672854c848c9b79c49a8cc6cc89176a49` | `46,755,254` | `QuestionResolved(bytes32,int256,uint256[])` |

CTF deployment block is `4,023,686`. On 2026-07-13, `polygon.drpc.org` and
`polygon.gateway.tenderly.co` independently proved empty code at each preceding block and non-empty
code at each listed block. Runtime also verifies
code is empty/non-empty at those two coordinates through both providers before accepting a terminal.
A fifth address, changed coordinate, or changed event ABI is rejected by construction and requires
an owner-reviewed source change.

The only v1 root-position collateral is pUSD
`0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`. A different collateral or non-root/neg-risk identity is
unsupported. This allowlist is used only to derive token positions; CTF remains payout authority.

The address registry is frozen from the official
[Polymarket contracts page](https://docs.polymarket.com/resources/contracts) and official
adapter releases [v1.0.1](https://github.com/Polymarket/uma-ctf-adapter/releases/tag/v1.0.1),
[v2.0.0](https://github.com/Polymarket/uma-ctf-adapter/releases/tag/v2.0.0),
[v3.0.0](https://github.com/Polymarket/uma-ctf-adapter/releases/tag/v3.0.0), and
[v3.1.0](https://github.com/Polymarket/uma-ctf-adapter/releases/tag/v3.1.0). Event layouts come from
those exact tagged source trees; future documentation drift does not change code authority.

Exactly two distinct Polygon providers are required when accepting a terminal or re-checking one
during recovery. Both providers must agree on chain ID, the acceptance block number and hash, CTF
payout vector, and normalized path classification. One provider or any disagreement is unavailable;
it never creates or changes a terminal.

Finality is five blocks:

```text
acceptance_block = min(provider_a_head, provider_b_head) - 5
```

The acceptance block must be non-negative and both providers must return the same block hash.

## 3. Separate lifecycle, economics, and dispute domains

These domains are never collapsed into a single status.

```python
LifecyclePhase = UNRESOLVED | FINALIZED
DisputeState = CLEAR | DISPUTED | MANUAL | UNKNOWN
```

`UNRESOLVED` means CTF's payout denominator is zero. `FINALIZED` means it is positive. Intermediate
UMA proposal states may be retained as audit events, but are not fabricated when history is absent
and are not settlement authority.

Terminal economics are exact non-negative integers:

```python
PayoutVector(numerators: tuple[int, int], denominator: int)
```

`PayoutVector.fraction_for(slot: int) -> Fraction` is exact and rejects non-integer/out-of-range
slots. `decimal_for(slot: int) -> Decimal` uses a private `decimal.Context(prec=78,
rounding=ROUND_HALF_EVEN)` inside `localcontext`; it never reads or mutates ambient context.

For a finalized vector:

- there are exactly two numerators in POL-15's binary MarketRegistry scope;
- every numerator is a non-negative integer (not `bool`);
- the denominator is positive and equals `sum(numerators)`;
- exact slot authority is `Fraction(numerator, denominator)`.

Every valid CTF fraction is preserved as numerator/denominator authority. It is never rounded to
binary WON/LOST. Existing Decimal PnL consumers receive only the deterministic 78-significant-digit
projection. Economic status is `WON` only for exact fraction `1`, `LOST` only for exact fraction `0`,
and `SETTLED` for a strict fractional value. Calibration labels a strict fraction `VOID` while
retaining its exact rational authority because it is not a binary ground-truth outcome.

Path classification is independent:

- `CLEAR`: complete reviewed adapter history contains a positive allowlisted request-to-resolution
  chain tied to the exact condition and has no dispute/manual marker;
- `DISPUTED`: complete history contains a matching dispute/reset marker;
- `MANUAL`: complete history contains a matching manual/emergency marker;
- `UNKNOWN`: history is missing, incomplete, unsupported, or cannot be tied to the condition.

Precedence is `MANUAL > DISPUTED > UNKNOWN > CLEAR`. `CLEAR`, `DISPUTED`, and `MANUAL` are immutable
terminal paths and create outbox rows. Only `CLEAR` projects economic values. `DISPUTED` and `MANUAL`
project excluded non-economic statuses with no value, allowing the existing tail-risk counters to
remain honest. `UNKNOWN` is mutable availability/classification state: it remains a central
assessment, creates no outbox, and may become classifiable on a later complete poll. An empty scan or
history containing only unrelated logs is `UNKNOWN`, never `CLEAR`.

`fold_dispute(states: tuple[DisputeState, ...]) -> DisputeState` implements only that precedence and
rejects an empty/non-enum input.

## 4. Canonical identity

`ResolutionSubject` is immutable and contains:

- non-empty `event_id`;
- canonical 32-byte lowercase `0x` `condition_id`;
- exactly two ordered, distinct decimal-string `token_ids`;
- non-empty `category`.

Event/category/provider identifiers must be non-empty strings equal to their stripped form.
Condition/question/block/transaction hashes are lowercase `0x` plus exactly 64 hex digits; addresses
are lowercase `0x` plus exactly 40 hex digits. Token IDs are canonical base-10 uint256 strings
matching `[1-9][0-9]*`. Integers reject `bool`; block/log/slot values are non-negative.

Token position in `token_ids` is the outcome slot. The number of tokens must equal the number of CTF
payout numerators. Gamma ordering is only a candidate assertion: each provider independently calls
CTF `getCollectionId(bytes32(0), condition_id, 1 << slot)` and
`getPositionId(pUSD, collection_id)` for slots zero and one. The resulting decimal position IDs must
exactly equal `token_ids` in order. A swapped Gamma array is unavailable and cannot swap a payout.
Labels such as Yes/No are not settlement inputs.

`MarketRegistry.resolution_subject_for(intent)` is the sole Gamma identity bridge. It performs the
same condition/token/event cross-check as `metadata_for` and returns the complete sibling ordering.
Forecast, maker, and shadow rows created after POL-15 carry event ID, token ID, outcome slot, and
ordered siblings. Existing rows are migrated with nullable identity fields and are
`legacy_unsettleable`; they are never guessed or backfilled.

```python
@dataclass(frozen=True)
class ResolutionSubjectMetadata:
    event_id: str
    condition_id: str
    category: str
    token_id: str
    outcome_slot: int
    sibling_token_ids: tuple[str, str]

class MarketRegistry:
    def resolution_subject_for(self, intent) -> ResolutionSubjectMetadata: ...
```

Forecast also adds nullable `token_id TEXT`. Each target table adds nullable `event_id TEXT`,
`outcome_slot INTEGER`,
`sibling_token_ids TEXT`, `resolution_value TEXT` where Forecast lacks it, and
`resolution_numerator TEXT`, `resolution_denominator TEXT`, and `terminal_id TEXT`. Numerator and
denominator retain exact rational authority; `resolution_value` is the fixed-context projection.
Siblings are the canonical JSON array of two decimal strings. The public record
dataclasses append fields, preserving existing positional construction:

```python
event_id: str | None = None
token_id: str | None = None                    # Forecast only; Maker/Shadow field stays put
outcome_slot: int | None = None
sibling_token_ids: tuple[str, str] | None = None
resolution_value: Decimal | None = None     # Forecast only; existing Maker/Shadow field stays put
resolution_numerator: int | None = None
resolution_denominator: int | None = None
terminal_id: str | None = None
```

A row is legacy only when `event_id`, `outcome_slot`, `sibling_token_ids`, and `terminal_id` are all
null (and Forecast `token_id` is null). A canonical pending row has identity non-null and
`terminal_id` null. Mixed identity is
corruption and fails on read/application. Canonical creation requires
`sibling_token_ids[outcome_slot] == token_id` and a slot in `{0,1}`.

Only an actual `StubMarketMeta` instance may deliberately write a legacy Forecast row. Every other
metadata provider must expose `resolution_subject_for`; a missing method or unavailable identity
returns `REJECT resolution_identity_unavailable` before component/forecast writes. Unexpected method
failures retain the outer `internal_error` behavior. Existing ERS metadata fakes must implement the
method or explicitly subclass `StubMarketMeta`; duck-typed absence is never a legacy grant.
Maker/Shadow legacy creation remains test compatibility until POL-16 supplies canonical subjects.

ERS calls `ForecastLedger.require_condition_open(condition_id)` before its component write. A known
target receipt returns `REJECT market_resolved`. If a dispatcher wins the narrow race after that
precheck but before `record_forecast`, the ledger raises `ConditionAlreadyTerminal`; ERS returns the
same rejection. The already-appended component is retained as an audit row, but no forecast, trade
evaluation, ACCEPT decision, signing, or submission occurs. This is the only permitted component-only
race artifact; the fail-closed REJECT decision is recorded normally.

## 5. Provider seam and observations

The production boundary is typed rather than exposing raw JSON-RPC envelopes to feed logic. This is
the exact public model ABI; enums are `str, Enum` values using the uppercase spellings shown:

```python
@dataclass(frozen=True)
class ProviderObservation:
    provider_id: str
    block_number: int
    block_hash: str
    phase: LifecyclePhase
    payout: PayoutVector | None
    dispute: DisputeState
    collateral_address: str | None
    derived_token_ids: tuple[str, str] | None
    adapter_address: str | None
    question_id: str | None
    audit_event_ids: tuple[str, ...]

class ResolutionProvider(Protocol):
    provider_id: str
    def chain_id(self) -> int: ...
    def latest_block(self) -> int: ...
    def block_hash(self, block_number: int) -> str: ...
    def observe(self, subject: ResolutionSubject, block_number: int) -> ProviderObservation: ...
    def verify_terminal(self, terminal: "TerminalResolution") -> None: ...
```

An audit event ID is
`"{block_number}:{log_index}:{lowercase_32_byte_transaction_hash}:{event_kind}"`, ordered by
`(block_number, log_index, transaction_hash)`. Exact duplicate logs are idempotent; two different
logs at the same `(block_number, log_index)` are malformed. `FINALIZED` requires payout, pUSD,
derived tokens equal to the subject in order, adapter/question identity from matching CTF preparation
and resolution events, and those CTF audit IDs. `CLEAR`/`DISPUTED`/`MANUAL` additionally require the
matching supported adapter resolution evidence; `UNKNOWN` does not. `UNRESOLVED` requires dispute
`UNKNOWN` and forbids all terminal fields. The two observations must be equal
after removing only `provider_id`. A provider exception or malformed observation yields
`ResolutionUnavailable` without a write.

`TerminalResolution.from_observations(subject, first, second)` requires distinct non-empty provider
IDs and exact equality of every other field. It accepts only `FINALIZED` with path `CLEAR`,
`DISPUTED`, or `MANUAL`; `UNKNOWN` raises `ResolutionUnavailable`. It sorts provider IDs and copies
the agreed authority/audit fields into the immutable terminal.

The first production provider is `JsonRpcResolutionProvider`. It issues individual JSON-RPC calls
with monotonically increasing request IDs and checks JSON-RPC version, exact response ID, result vs
error exclusivity, hex quantity canonicality, fixed byte widths, and ABI word shapes. It uses:

```python
class JsonRpcClient:
    def __init__(self, endpoint: str, client: httpx.Client | None = None): ...
    def call(self, method: str, params: list[object]) -> object: ...

class JsonRpcResolutionProvider:
    def __init__(self, provider_id: str, rpc: JsonRpcClient): ...
```

- `eth_chainId`, `eth_blockNumber`, and `eth_getBlockByNumber`;
- `eth_call` for CTF outcome-slot count, payout denominator, and numerators;
- `eth_call` for both chain-derived root position IDs;
- `eth_getLogs` for CTF preparation/resolution and allowlisted adapter history.

It does not use RPC batching. Network overlap and persisted cursor semantics therefore cannot
conflict. It never scans from genesis. Because CTF slot count and payout denominator change only once,
the provider binary-searches `[CTF_DEPLOYMENT_BLOCK, acceptance_block]` for the first nonzero slot
count (preparation) and first positive denominator (resolution), then requires the matching CTF event
at each exact transition block. It pages only that closed preparation-to-resolution interval in exact
non-overlapping ranges of at most 10,000 blocks. Each page requests indexed path topics together plus,
for v1, the unindexed manual flag topic. All pages must succeed before classification; a range
error or incomplete response is `ResolutionUnavailable`, never `UNKNOWN` or `CLEAR`. This removes
chain-height/genesis work; the one initial terminal classification is proportional only to that
market's actual lifetime.

The frozen selectors are `d42dc0c2` (`getOutcomeSlotCount`), `dd34de67`
(`payoutDenominator`), `0504c814` (`payoutNumerators`), `856296f7` (`getCollectionId`), and
`39dd7530` (`getPositionId`). Frozen event topic zero values are:

| Event | Topic zero |
|---|---|
| `ConditionPreparation(bytes32,address,bytes32,uint256)` | `0xab3760c3bd2bb38b5bcf54dc79802ed67338b4cf29f3054ded67ed24661e4177` |
| `ConditionResolution(bytes32,address,bytes32,uint256,uint256[])` | `0xb44d84d3289691f71497564b85d4233648d9dbae8cbdbb4329f301c3a0185894` |
| `QuestionReset(bytes32)` | `0x7981b5832932948db4e32a4a16a0f44b2ce7ff088574afb9364b313f70f82e8f` |
| `QuestionFlaggedForAdminResolution(bytes32)` | `0xd96b8927b38f8cc48e678eeb45ee1c3a281d2ba49078ed4a5c00895d251e573b` |
| v1 `QuestionUpdated(bytes32,bytes,uint256,address,uint256,uint256,bool)` | `0x32da4770ea275a14ae9d822d58709fe7bfb296969d46357149ed02fb4135a17b` |
| v1 `QuestionResolved(bytes32,bool)` | `0x5c3937ed929cd157b73b417381d743daf6e1ef65999e3ccb5dd64bc3247e28d6` |
| v2+ `QuestionFlagged(bytes32)` | `0x2435a0347185933b12027c6f394a5fd9c03646dba233e956f50658719dfc0b35` |
| v2+ `QuestionResolved(bytes32,int256,uint256[])` | `0x566c3fbdd12dd86bb341787f6d531f79fd7ad4ce7e3ae2d15ac0ca1b601af9df` |
| v2+ `QuestionEmergencyResolved(bytes32,uint256[])` | `0x6edb5841a476c9c29c34a652d1a44f785fe71a6157a3da9a6a6a589a1bd2945a` |

Path normalization requires exactly one CTF preparation and one CTF resolution for the condition,
with the same allowlisted adapter oracle, question ID, binary slot count, and payout. Adapter history
is read from the derived preparation block through the derived resolution block and must contain a
matching positive resolution event. The CTF `ConditionResolution` and adapter terminal event must
share one transaction hash, with the CTF log index first, matching the reviewed adapter call order.
No matching supported preparation/resolution chain, including an empty or
unsupported-adapter history, is `UNKNOWN`. A reset is `DISPUTED`. A flag, v1 emergency boolean, or
v2+ emergency resolution is `MANUAL` and wins even if later unflagged. A normal positive resolution
with no reset/manual/unknown marker is `CLEAR` only for v2+. V1.0.1 cannot prove absence of a DVM
dispute from adapter events—a disputed non-ignore result emits the same normal resolution—so every
otherwise-normal v1 terminal is `UNKNOWN`. Any v1 `QuestionUpdated` also makes the path `UNKNOWN`
unless higher-precedence dispute/manual evidence exists. Conflicting terminal events or malformed
ABI/log order raise `ResolutionUnavailable`; unrelated event kinds are ignored because queries use
this exact topic allowlist. Every matching relevant event remains in `audit_event_ids` even when a
higher-precedence event determines classification; folding never erases lower-precedence history.

## 6. Canonical terminal bytes

There is one serializer for hashes and outbox identity:

```python
json.dumps(
    primitive_payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

The primitive payload contains only dictionaries with string keys, lists, strings, integers,
booleans, and null; floats and `Decimal` objects are forbidden. The exact v1 terminal primitive is:

```python
{
    "acceptance": {"block_hash": block_hash, "block_number": block_number},
    "authority": {
        "adapter_address": adapter_address,
        "audit_event_ids": list(audit_event_ids),
        "chain_id": 137,
        "collateral_address": PUSD_ADDRESS,
        "ctf_address": CTF_ADDRESS,
        "question_id": question_id,
    },
    "path": dispute.value,                 # CLEAR | DISPUTED | MANUAL; never UNKNOWN
    "payout": {"denominator": denominator, "numerators": list(numerators)},
    "providers": list(sorted_provider_ids),
    "subject": {
        "category": category,
        "condition_id": condition_id,
        "event_id": event_id,
        "token_ids": list(token_ids),
    },
    "version": 1,
}
```

`TerminalResolution` has fields `subject`, `payout`, `dispute`, `block_number`, `block_hash`,
`adapter_address`, `question_id`, `audit_event_ids`, and `provider_ids`; its derived `payload` follows
the schema above and `terminal_id = sha256(canonical_bytes(payload)).hexdigest()`. Audit events bind
recovery to the exact accepted path. No wall clock is a terminal field, so replay at the same
authority coordinate is byte-identical. A duplicate condition with identical bytes is idempotent;
different bytes raise `SettlementConflict` and leave the first terminal unchanged. A store-local
observation timestamp may be recorded beside the payload but is not hashed authority.

The canonical regression vector uses block `100`, block-hash byte `0x22`, the v3.1 adapter, audit
events `(90,1,0x33…,CONDITION_PREPARATION)`, `(99,2,0x44…,CONDITION_RESOLUTION)`, and
`(99,3,0x44…,QUESTION_RESOLVED)`, question byte `0x66`, path `CLEAR`, payout `[3,1]/4`, providers
`archive-a/archive-b`, category `política`, condition byte `0x11`, event `event-1`, tokens `101/202`,
and version `1`. Its canonical UTF-8 length is `997` and SHA-256 is
`499af1bbfcdd6989ffbcf31a2d8898b78a1b573e1db20619c635285310f3759b`. The fixture constructs the
complete primitive above from those explicit repeated-byte values, asserts every byte and hash, then
reconstructs it with reversed provider/dictionary input order and asserts equality.

## 7. Durable store and recovery

`ResolutionStore` is one SQLite database using WAL, `synchronous=FULL`, foreign keys, and explicit
transactions. It stores:

- immutable subjects;
- latest non-terminal assessments, including excluded path reason;
- immutable `CLEAR`/`DISPUTED`/`MANUAL` terminals;
- one outbox row per `(terminal_id, target_role)`;
- scan/recovery audit records.

The store has no periodic 901-second expiry and no four-database atomic coordinator. CTF terminal
state is immutable by contract. Recovery verifies every terminal having at least one `PENDING`
outbox row at its original acceptance block with both providers before any drain. Each provider must
return the stored acceptance block hash and re-read the exact CTF payout, deployment code, and pUSD
token mapping. The acceptance block hash cryptographically commits its entire ancestor/path history,
so an equal hash preserves the stored audit events and absence checks without replaying thousands of
log pages; a changed hash is a contradiction. Any changed hash, payout, identity, or authority raises
`SettlementConflict`, records an integrity halt, and performs no delivery. Already-delivered
terminals are also checkable explicitly through `verify_terminal`; POL-17 decides scheduling.

On open, a store with any pending outbox sets process-local `recovery_required=True`. Dispatcher
drain refuses with `RecoveryRequired` until `ResolutionFeed.recover_pending()` verifies every pending
terminal in that invocation. Provider unavailability or partial verification leaves the flag true and
allows no delivery. Terminals accepted by the current healthy feed instance are already verified and
do not set the flag.

Outbox state is `PENDING` or `DELIVERED`. Ordering is terminal insertion ID ascending, then role
`FORECAST`, `MAKER`, `SHADOW`. Claiming is not durable; delivery retries the oldest pending row.
Target application and acknowledgement are separate transactions, so a crash in between simply
replays the immutable payload. Target writes must therefore be idempotent and conflict detecting.

The store persists a one-way `integrity_halt` reason. Once halted, accepting terminals and delivering
outbox rows fail closed. Clearing a halt requires operator investigation and is outside POL-15.

The exact public store records and methods are:

```python
@dataclass(frozen=True)
class ResolutionAssessment:
    subject: ResolutionSubject
    phase: LifecyclePhase
    dispute: DisputeState
    payout: PayoutVector | None
    block_number: int
    block_hash: str
    detail: str

@dataclass(frozen=True)
class OutboxRecord:
    sequence: int
    terminal: TerminalResolution
    role: str                         # FORECAST | MAKER | SHADOW

class ResolutionStore:
    def __init__(self, path, stamper): ...
    @property
    def recovery_required(self) -> bool: ...
    def record_assessment(self, assessment: ResolutionAssessment) -> None: ...
    def assessment_for(self, condition_id: str) -> ResolutionAssessment | None: ...
    def accept_terminal(self, terminal: TerminalResolution) -> bool: ...
    def terminal_for(self, condition_id: str) -> TerminalResolution | None: ...
    def pending_terminals(self) -> tuple[TerminalResolution, ...]: ...
    def pending_outbox(self, limit: int) -> tuple[OutboxRecord, ...]: ...
    def acknowledge(self, sequence: int, terminal_id: str, role: str) -> bool: ...
    def halt(self, reason: str) -> None: ...
    def require_healthy(self) -> None: ...
    def _complete_recovery(self, terminal_ids: tuple[str, ...]) -> None: ...
```

`accept_terminal` returns true only for a new terminal. `acknowledge` returns true only for the first
matching acknowledgement. Invalid limits/identities raise `ValueError`; missing or mismatched keys
raise `SettlementConflict`. Public store mutators use explicit transactions and check the halt first.
`_complete_recovery` clears the process-local barrier only when its IDs exactly equal a fresh query of
all terminals with pending outbox rows.

Terminal acceptance atomically deletes any earlier assessment for the same condition. Once a terminal
exists, `record_assessment` rejects that condition with `SettlementConflict`; therefore
`assessment_for` and `terminal_for` can never simultaneously return records for one condition.

## 8. Target-ledger projection

Each target ledger exposes one condition-level method:

```python
apply_terminal(terminal: TerminalResolution) -> int
```

Each target database adds `resolution_receipts(condition_id TEXT PRIMARY KEY, terminal_id TEXT
UNIQUE NOT NULL, payload BLOB NOT NULL)`. `payload` is the exact canonical terminal bytes; a matching
condition/ID with different bytes is a conflict.

It finds pending canonical rows for the condition and validates their stored identity against the
terminal subject. Validation of every matching row, insertion of an immutable condition-level target
receipt, and all row updates occur in one target transaction; one conflict rolls back the whole
application. The receipt is written even when no rows match. Return value is the number newly
settled. Reapplying the same receipt returns zero. A different terminal ID/value for a settled row or
receipt raises `SettlementConflict`; terminal settlement is never overwritten.

After a target receipt exists, every row-creation method rejects a new row for that condition. This
closes the terminal-before-row race: acknowledging a zero-row application cannot strand a later
pending row. SQLite transaction serialization makes receipt insertion and row creation mutually
ordered inside each target database.

All three target ledgers use WAL and `synchronous=FULL` for terminal receipts, row settlement, and
row creation. This ensures central FULL acknowledgement cannot outlive the earlier target commit
after power loss.

Projection is:

- `CLEAR` Forecast: `WON`, `LOST`, or `VOID`, plus exact numerator/denominator and deterministic
  `resolution_value`; strict fractions are `VOID` and remain excluded from binary calibration.
- `CLEAR` Maker/Shadow: `WON`, `LOST`, or `SETTLED`, plus exact numerator/denominator and deterministic
  `resolution_value`.
- `DISPUTED` or `MANUAL` Forecast: `DISPUTED_LOST`, value `NULL`.
- `DISPUTED` or `MANUAL` Maker/Shadow: `DISPUTED`, value `NULL`.

`UNKNOWN` assessments never create a terminal or outbox. Their rows remain pending and their
condition is visible through `ResolutionStore.assessment_for` as excluded.

Legacy row-level `record_resolution` and `record_settlement` remain available only for all-null
identity legacy rows used by existing unit fixtures. They reject every canonical row, whether pending
or terminal-applied, so they cannot bypass CTF authority. POL-17 composition must use the feed/outbox
path.

## 9. Polling operation

The public feed result and methods are:

```python
class PollDisposition(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    UNKNOWN = "UNKNOWN"
    ACCEPTED = "ACCEPTED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    UNAVAILABLE = "UNAVAILABLE"

@dataclass(frozen=True)
class PollResult:
    condition_id: str
    disposition: PollDisposition
    dispute: DisputeState | None
    terminal_id: str | None
    detail: str

class ResolutionFeed:
    def __init__(self, store: ResolutionStore,
                 providers: tuple[ResolutionProvider, ResolutionProvider]): ...
    def poll(self, subjects: tuple[ResolutionSubject, ...]) -> tuple[PollResult, ...]: ...
    def verify_terminal(self, terminal: TerminalResolution) -> None: ...
    def recover_pending(self) -> int: ...
```

`ResolutionFeed.poll(subjects)` performs one bounded pass:

1. validate subjects are unique by condition, then require both providers report chain `137`;
2. for each stored terminal, verify subject equality and call both providers' bounded
   `verify_terminal` at its original acceptance block;
   return `ALREADY_TERMINAL` without deriving a new terminal at the later head;
3. if any subjects remain, read each provider head, derive one common acceptance block, and require
   its provider-agreed hash;
4. observe each remaining subject independently through both providers;
5. persist unresolved or unknown assessments;
6. accept a clear/disputed/manual finalized terminal and create its three outbox rows atomically;
7. continue after a per-condition `ResolutionUnavailable` with `UNAVAILABLE`, preserving input order;
8. persistently halt and abort on `SettlementConflict` or authority/identity contradiction.

Construction validates the two non-empty/distinct provider IDs without network I/O. A chain/head/hash
failure before per-condition observation returns `UNAVAILABLE` for every input and writes nothing.
Invalid/duplicate input subjects raise `ValueError`; results otherwise preserve the input order.

There is no hidden thread, sleep, service start, or network call during object construction.

`ResolutionDispatcher(store, forecast_ledger, maker_ledger, shadow_ledger).drain(limit) -> int`
applies pending payloads to target ledgers in deterministic
outbox order. An ordinary target exception leaves the row pending and stops draining so retry
ordering is stable. A target `SettlementConflict` first persists the central integrity halt, leaves
the row pending, and then re-raises. A successful idempotent apply is acknowledged. The return value
is acknowledgements completed; `limit` must be a positive integer. A recovery-required or halted
store raises before reading the first target.

The exception hierarchy is exact: `ResolutionError` is the base; `ResolutionUnavailable` is a
retryable per-condition/provider failure; `RecoveryRequired` blocks delivery; `SettlementConflict`
is an authority/immutability contradiction; `ConditionAlreadyTerminal` rejects late target-row
creation; and `IntegrityHalted` reports an already halted store.

## 10. Security invariants

1. Gamma, labels, prices, and Hermes cannot choose an outcome.
2. Fewer than two agreeing providers cannot create a terminal.
3. Five confirmations are required at the exact accepted block.
4. Unknown, disputed, manual, unsupported, or incomplete paths never produce positive evidence.
5. Token value comes from stored slot identity and exact CTF integers.
6. Terminal bytes and target settlements are immutable and conflict detecting.
7. Crash between target commit and acknowledgement is safe by idempotent replay.
8. Legacy identity is quarantined, never guessed.
9. Any post-acceptance contradiction halts delivery and future acceptance.
10. No POL-15 operation signs or submits a blockchain transaction.

## 11. Acceptance criteria

- Pure tests cover every model invariant, fractional payout, path precedence, canonical hash vector,
  provider disagreement, five-confirmation boundary, and unavailable behavior.
- Store tests prove atomic terminal/outbox creation, duplicate idempotency, immutable conflict,
  restart persistence, halt persistence, and target-ack retry behavior.
- Ledger migration tests prove new identity fields, legacy quarantine, terminal idempotency, and
  conflict rejection for Forecast, Maker, and Shadow.
- Registry and ERS tests prove canonical identity is recorded before any future settlement.
- Provider tests prove pUSD CTF position derivation authenticates token order and a positive,
  condition-linked allowlisted adapter/question/resolution chain is required for non-UNKNOWN paths.
- Provider tests prove the frozen CTF/adapter preceding-empty and exact-block-nonempty code anchors,
  logarithmic preparation/resolution transition search, derived-interval pages of at most 10,000
  blocks, and zero-log terminal recovery.
- V1.0.1 normal resolution remains `UNKNOWN` without historical Optimistic Oracle disputer proof;
  adapter-event ambiguity can never be upgraded to `CLEAR`.
- Feed/dispatcher whole-slice tests prove unresolved, clear, disputed, unknown, crash/retry,
  fractional, multi-condition isolation, and recovery contradiction paths.
- Classified `DISPUTED`/`MANUAL` terminals fan out immutable non-economic statuses that remain
  excluded from PnL/calibration while contributing the existing dispute-tail counters.
- Target tests prove all-row atomicity, zero-row receipts, post-receipt creation rejection, idempotent
  replay, and WAL/FULL durability for all three ledgers.
- Reopened pending outboxes cannot deliver until every pending terminal is reverified in one complete
  recovery; provider unavailability or partial recovery leaves the barrier closed.
- Calibration, maker, and harness consumer tests prove fractional forecast rows remain excluded while
  fractional maker/shadow rows retain exact rational authority and contribute the deterministic
  fixed-context economic mark without becoming unknown data.
- JSON-RPC boundary tests prove strict IDs, quantities, hashes, ABI words, errors, and incomplete log
  classification without making live network calls.
- Full suite, compile check, diff check, independent specification review, and adversarial mutation
  review pass before the slice is complete.

## 12. Out of scope

- Continuous runtime composition or deployment (POL-17).
- Shadow fill generation (POL-16).
- Promotion/coverage thresholds and live-money authorization.
- Neg-risk/combo-specific token derivation.
- Automatic adapter discovery or adoption.
- Periodic full-history rescans.
- Cross-database atomic commits or same-UID malicious database tamper resistance.
- Blockchain writes, redemptions, wallet keys, and signing.
