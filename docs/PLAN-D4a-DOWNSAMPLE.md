# PLAN — D4a downsampled market-memory persistence

> **For Hermes:** execute this plan serially on `pol-13-d4a-downsample`. Use strict RED→GREEN→REFACTOR. Never run parallel writers. Do not touch a production file until its named test has failed for the intended reason. After each sub-slice, run independent spec-compliance review, then adversarial mutation review in an isolated worktree.

**Goal:** keep every CLOB frame in the live in-memory `LocalBook`, but replace the 24/7 raw-frame firehose with one exact, versioned, 60-second midpoint batch while retaining the complete deduplicated trade tape.

**Architecture:** add a pure midpoint batch codec and a supervised `MidpointSnapshotter`, cut the production WS sink to `None`, and teach the existing co-move adapter to prefer midpoint batches while retaining raw-store fallback. Reuse the existing `EventStore`, one process-wide stamper, one `QueuedEventWriter`, and unchanged Data API poller. Prove the live database grows at no more than 0.5 GiB/day before enabling the service.

**Tech stack:** Python 3.13, `asyncio`, exact `Decimal`, SQLite/WAL, pytest, systemd deployment kit.

**Authoritative contract:** `docs/DESIGN-D4a-DOWNSAMPLE.md`.

---

## 0. Working rules and baseline

- Branch: `pol-13-d4a-downsample` from `main` at `2ab2411`.
- Baseline command:

```bash
./.venv/bin/pytest -o addopts="" -q
```

Expected baseline: `1145 passed`, exit 0, no skips/xfails.

- Targeted commands must also use `-o addopts=""` so pytest prints an explicit summary.
- One test behavior per RED. Confirm failure is caused by missing/incorrect behavior, not a typo.
- Commit only on GREEN. Omit `Co-Authored-By` trailers.
- Sacred surfaces stay byte-for-byte: `src/polybot/ers/validator.py`, the `propose_trade` chokepoint, `evaluate_intent`, caps, signer, safety controller/state machine.
- The ingestion service stays stopped and disabled through implementation and review.
- Do not merge, push, deploy, rename the live database, or start a service without the required later approval.

---

# Sub-slice D4a-ds1 — midpoint codec and one-shot sampler

## Task A1: Decode one valid version-1 batch with exact Decimals

**Objective:** establish the public midpoint batch schema and exact return type.

**Files:**
- Create: `tests/test_midpoint_snapshot.py`
- Create after RED: `src/polybot/ingestion/midpoint.py`

**Step 1 — RED:** create the test with one valid compact payload:

```python
import json
from decimal import Decimal

from polybot.ingestion.midpoint import (
    MIDPOINT_SCHEMA,
    MIDPOINT_SOURCE,
    MidpointQuote,
    decode_midpoint_batch,
)


def test_decode_midpoint_batch_returns_exact_quotes():
    content = json.dumps({
        "schema": 1,
        "books": {
            "B": {"bid": "0.30", "ask": "0.34", "mid": "0.32"},
            "A": {"bid": "0.60", "ask": "0.62", "mid": "0.61"},
        },
    })

    assert MIDPOINT_SOURCE == "clob-midpoint"
    assert MIDPOINT_SCHEMA == 1
    assert decode_midpoint_batch(content) == {
        "A": MidpointQuote(Decimal("0.60"), Decimal("0.62"), Decimal("0.61")),
        "B": MidpointQuote(Decimal("0.30"), Decimal("0.34"), Decimal("0.32")),
    }
```

**Step 2 — verify RED:** 

```bash
./.venv/bin/pytest -o addopts="" tests/test_midpoint_snapshot.py::test_decode_midpoint_batch_returns_exact_quotes -q
```

Expected: FAIL because `polybot.ingestion.midpoint` does not exist.

**Step 3 — GREEN:** add only:

```python
MIDPOINT_SOURCE = "clob-midpoint"
MIDPOINT_SCHEMA = 1

@dataclass(frozen=True)
class MidpointQuote:
    bid: Decimal
    ask: Decimal
    midpoint: Decimal


def decode_midpoint_batch(content):
    payload = json.loads(content)
    return {
        token: MidpointQuote(
            Decimal(quote["bid"]),
            Decimal(quote["ask"]),
            Decimal(quote["mid"]),
        )
        for token, quote in sorted(payload["books"].items())
    }
```

Do not add validation not yet driven by a RED.

**Step 4 — verify GREEN:** rerun the exact targeted command; expect `1 passed`.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: decode exact midpoint snapshot batches"
```

---

## Task A2: Fail loud on the top-level schema contract

**Objective:** reject ambiguous/unknown batch shapes rather than guessing.

**Files:**
- Modify: `tests/test_midpoint_snapshot.py`
- Modify after RED: `src/polybot/ingestion/midpoint.py`

**Step 1 — RED:** add separate named tests for:

```python
import pytest


def test_decode_rejects_unknown_schema():
    with pytest.raises(ValueError, match="schema"):
        decode_midpoint_batch('{"schema":2,"books":{}}')


def test_decode_rejects_missing_or_extra_top_level_keys():
    for content in ('{"books":{}}', '{"schema":1,"books":{},"extra":1}'):
        with pytest.raises(ValueError, match="schema|keys"):
            decode_midpoint_batch(content)


def test_decode_rejects_non_object_books():
    with pytest.raises(ValueError, match="books"):
        decode_midpoint_batch('{"schema":1,"books":[]}')
```

**Step 2 — verify RED:** run these three tests; confirm at least the unknown/extra schema cases fail because current decoder accepts them.

**Step 3 — GREEN:** validate decoded JSON type, exact top-level keys, schema value, and books type. Convert `json.JSONDecodeError`/type failures to a clear `ValueError` without swallowing unrelated programmer exceptions.

**Step 4 — verify GREEN:** targeted file; expect all current tests pass.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: fail loud on midpoint batch schema changes"
```

---

## Task A3: Fail loud on token and quote field shape

**Objective:** accept only non-empty token IDs and exactly three string price fields.

**Step 1 — RED:** add named tests:

```python
def test_decode_rejects_empty_token_id(): ...
def test_decode_rejects_missing_or_extra_quote_keys(): ...
def test_decode_rejects_numeric_json_prices_instead_of_strings(): ...
```

Use representative payloads; assert `ValueError` with `token` or `quote`/`string` in the message. The numeric test must use JSON number `0.60`, not string `"0.60"`.

**Step 2 — verify RED:** confirm the current decoder accepts at least the extra-key and numeric-value mutations or raises an unhelpful exception.

**Step 3 — GREEN:** require:

```python
isinstance(token, str) and token
set(quote) == {"bid", "ask", "mid"}
all(isinstance(quote[name], str) for name in ("bid", "ask", "mid"))
```

Raise `ValueError`; do not coerce.

**Step 4 — verify GREEN:** run `tests/test_midpoint_snapshot.py`; expect all pass.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: validate midpoint quote field shapes"
```

---

## Task A4: Fail loud on non-finite, invalid-domain, crossed, and forged prices

**Objective:** pin the exact mathematical contract used to warm correlation.

**Step 1 — RED:** add separate named tests:

```python
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_decode_rejects_non_finite_prices(bad): ...

@pytest.mark.parametrize("bid,ask", [
    ("-0.01", "0.50"),
    ("0.50", "1.01"),
    ("0.60", "0.60"),
    ("0.70", "0.60"),
])
def test_decode_rejects_out_of_domain_locked_or_crossed_books(bid, ask): ...


def test_decode_rejects_midpoint_not_equal_to_exact_bid_ask_average(): ...
```

**Step 2 — verify RED:** confirm current decoder accepts a representative NaN, crossing, or forged midpoint.

**Step 3 — GREEN:** parse `Decimal`, call `is_finite()` before comparisons, enforce `0 <= bid < ask <= 1`, and exact midpoint equality.

**Step 4 — verify GREEN:** targeted file; expect all pass.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: validate midpoint batch price invariants"
```

---

## Task A5: Snapshot two fresh books into one deterministic envelope

**Objective:** implement the one-stamp/one-row batch behavior.

**Files:** same as A1.

**Test helpers:**

```python
class FakeBook:
    def __init__(self, bid, ask, mid):
        self._bid, self._ask, self._mid = map(Decimal, (bid, ask, mid))
    def best_bid(self): return self._bid
    def best_ask(self): return self._ask
    def midpoint(self): return self._mid

class FakeStamper:
    def __init__(self): self.calls = 0
    def stamp(self):
        self.calls += 1
        return 123

class FakeWriter:
    def __init__(self): self.rows = []
    def append(self, row): self.rows.append(row)
```

**Step 1 — RED:** import `MidpointSnapshotter`, construct with token order `("B", "A")`, and assert:

- return value 2;
- stamper called exactly once;
- writer received exactly one row;
- source/tier/event ID/stamp/published_at match the design;
- `market_links == ("A", "B")`;
- `row.content` is the exact compact deterministic JSON string;
- decoding returns exact quotes.

**Step 2 — verify RED:** expected FAIL because `MidpointSnapshotter` is missing.

**Step 3 — GREEN:** implement constructor materialization and synchronous `snapshot_once()` only. Use one stamp before reads, sorted tokens, `json.dumps(..., sort_keys=True, separators=(",", ":"))`, and one `Envelope`.

**Step 4 — verify GREEN:** targeted test, then full `tests/test_midpoint_snapshot.py`.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: persist one deterministic midpoint batch"
```

---

## Task A6: Omit unusable books and still record an empty cadence batch

**Objective:** fail closed without losing evidence that the sampler ran.

**Step 1 — RED:** add:

```python
class NoneMidBook:
    def midpoint(self): return None


def test_snapshot_omits_missing_and_unusable_books(): ...

def test_snapshot_writes_valid_empty_batch_when_all_books_unusable(): ...
```

The first mixes a missing token, `NoneMidBook`, and one fresh book; assert only the fresh token appears. The second asserts one row with `{"books":{},"schema":1}`, empty links, and return 0.

**Step 2 — verify RED:** current implementation should raise on missing/unusable access or fail the inclusion result.

**Step 3 — GREEN:** skip `book is None` and `midpoint() is None`; do not call bid/ask for skipped books. Always append one batch.

**Step 4 — verify GREEN:** targeted tests + file.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: omit stale books from midpoint batches"
```

---

## Task A7: Validate sampler construction and propagate dependency failures

**Objective:** fail loud on invalid configuration and storage/book failures.

**Step 1 — RED:** add separate tests for duplicate token IDs, empty token IDs, empty universe, zero/negative/non-finite interval, a raising `book_for`, and a raising writer. Assert construction `ValueError` or dependency exception propagation as appropriate.

**Step 2 — verify RED:** confirm invalid construction currently succeeds.

**Step 3 — GREEN:** finite-positive interval and token validation only. Do not catch exceptions from `book_for`, accessors, encoding, stamper, or writer.

**Step 4 — verify GREEN:** midpoint test file.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: fail loud on invalid midpoint sampler state"
```

---

## D4a-ds1 review checkpoint

1. Run:

```bash
./.venv/bin/pytest -o addopts="" tests/test_midpoint_snapshot.py tests/test_persistence.py tests/test_replay.py -q
git diff main -- src/polybot/ingestion/midpoint.py tests/test_midpoint_snapshot.py
```

2. Independent spec review: verify A1–A4/A1–A4 design acceptance, exact schema, no overbuild.
3. Mutation review in an isolated worktree. Required mutations:
   - accept schema 2;
   - accept extra keys;
   - `Decimal(str(value))` numeric coercion;
   - remove `is_finite()`;
   - change `<` to `<=` for locked books;
   - trust stored midpoint;
   - call stamper per token;
   - append per token;
   - include `midpoint() is None`.
4. Every mutation must make a named test fail. Fix survivors through new RED→GREEN cycles, then re-review.

---

# Sub-slice D4a-ds2 — sampler cadence and configuration

## Task B1: Default and validate the 60-second config field

**Files:**
- Modify: `tests/test_runtime_config.py`
- Modify after RED: `src/polybot/runtime/config.py`

**Step 1 — RED:** extend `test_valid_config_defaults` with:

```python
assert c.snapshot_interval_seconds == 60.0
```

Add invalid parametrizations for zero, negative, `math.inf`, and `math.nan`.

**Step 2 — verify RED:** expected constructor error for unknown field/default assertion failure.

**Step 3 — GREEN:** add the dataclass field and include it in the existing interval-validation tuple.

**Step 4 — verify GREEN:**

```bash
./.venv/bin/pytest -o addopts="" tests/test_runtime_config.py -q
```

**Step 5 — commit:**

```bash
git add tests/test_runtime_config.py src/polybot/runtime/config.py
git commit -m "feat: configure midpoint snapshot cadence"
```

---

## Task B2: Overlay snapshot cadence from TOML and environment

**Step 1 — RED:** extend the TOML/env test so TOML sets 120 and `POLYBOT_INGEST_SNAPSHOT_INTERVAL_SECONDS=30` overrides it. Assert float `30.0`.

**Step 2 — verify RED:** expect the unknown TOML key or string value to fail.

**Step 3 — GREEN:** add the field name to `_FLOAT_FIELDS`; no loader special case.

**Step 4 — verify GREEN:** runtime config file.

**Step 5 — commit:**

```bash
git add tests/test_runtime_config.py src/polybot/runtime/config.py
git commit -m "feat: load snapshot cadence from deploy config"
```

---

## Task B3: Sleep before the first snapshot and repeat once per completed interval

**Files:**
- Modify: `tests/test_midpoint_snapshot.py`
- Modify after RED: `src/polybot/ingestion/midpoint.py`

**Step 1 — RED:** add an async scenario with an injected controlled sleep:

```python
def test_run_sleeps_before_each_snapshot_and_cancels_cleanly():
    calls = []
    gates = [asyncio.Event(), asyncio.Event()]
    async def controlled_sleep(interval):
        calls.append(("sleep", interval))
        await gates[len(calls) - 1].wait()
    ...
```

Start `run()` as a task. Assert no row before releasing the first gate; release once and assert exactly one row; cancel the task and assert cancellation with no second row. Keep the test bounded with `asyncio.wait_for`.

**Step 2 — verify RED:** expected FAIL because `run` is missing.

**Step 3 — GREEN:** implement only:

```python
async def run(self):
    while True:
        await self._sleep(self._interval_seconds)
        self.snapshot_once()
```

Do not catch cancellation or snapshot errors.

**Step 4 — verify GREEN:** targeted test + midpoint file.

**Step 5 — commit:**

```bash
git add tests/test_midpoint_snapshot.py src/polybot/ingestion/midpoint.py
git commit -m "feat: run midpoint snapshots on a fixed cadence"
```

---

## Task B4: Sampler errors terminate the supervised service

**Objective:** pin fail-loud behavior at the async boundary.

**Step 1 — RED:** inject an immediate sleep and a writer that raises `OSError("disk full")`; assert `await sampler.run()` raises that exact error on the first cycle.

**Step 2 — verify RED:** if B3 already propagates, this test may pass immediately. If it does, do not invent production code; record it as a characterization/mutation test and combine it with B3 before B3 commit. The final history must still show the behavior was tested before any error-catching code existed.

**Step 3 — GREEN:** no code should be needed unless B3 accidentally catches errors; remove such catching.

**Step 4 — verify GREEN:** midpoint test file.

**Step 5 — commit:** include with B3 if no production change; do not create an empty commit.

---

## D4a-ds2 review checkpoint

Run:

```bash
./.venv/bin/pytest -o addopts="" tests/test_midpoint_snapshot.py tests/test_runtime_config.py -q
```

Mutation review must kill: snapshot before first sleep, skip a cycle, swallow writer error, accept zero/NaN interval, fail to coerce env float.

---

# Sub-slice D4a-ds3 — production factory cutover

## Task C1: Factory wires WS + sampler + unchanged trade poller

**Files:**
- Modify: `tests/test_runtime_build.py`
- Modify after RED: `src/polybot/runtime/ingestion.py`

**Step 1 — RED:** update existing service-count expectations:

```python
# data API enabled: clob-ws + clob-midpoint + data-api
assert len(rt._services) == 3

# data API disabled: clob-ws + clob-midpoint
assert len(rt._services) == 2
```

Add a monkeypatched collector/snapshotter capture test that asserts:

- collector receives `sink=None`;
- sampler receives the discovered token IDs, `collector.book_for`, the same stamper, the same writer, and configured interval;
- no `PersistingSink` object is constructed;
- all factories remain `_supervised` wrapped.

**Step 2 — verify RED:** existing factory exposes only 2/1 services and passes a raw sink.

**Step 3 — GREEN:** import `MidpointSnapshotter`, remove the runtime `PersistingSink` import, pass `sink=None`, construct sampler, add its supervised service before optional Data API.

**Step 4 — verify GREEN:**

```bash
./.venv/bin/pytest -o addopts="" tests/test_runtime_build.py tests/test_runtime_ingestion.py -q
```

**Step 5 — commit:**

```bash
git add tests/test_runtime_build.py src/polybot/runtime/ingestion.py
git commit -m "fix: replace production raw frames with midpoint batches"
```

---

## Task C2: End-to-end factory capture stores snapshots and zero raw rows

**Files:**
- Modify: `tests/test_runtime_build.py`
- Production code only if the test exposes a genuine missing seam/bug.

**Step 1 — RED:** build a bounded fake WS transport that emits a valid `book` frame and then remains open. Use:

```python
IngestionConfig(
    db_path=str(tmp_path / "m.db"),
    universe_max_markets=1,
    data_api_enabled=False,
    snapshot_interval_seconds=0.01,
)
```

Run the real factory/runtime long enough for one snapshot, request a graceful stop, reopen `EventStore`, and assert:

```python
assert {row.source for row in rows} == {"clob-midpoint"}
assert len(rows) >= 1
assert decode_midpoint_batch(rows[0].content)[token].midpoint == Decimal("0.61")
```

Also assert no `clob-ws` source and writer close completed (reopen succeeds).

**Step 2 — verify RED:** before C1 implementation this persists raw rows or no midpoint row.

**Step 3 — GREEN:** C1 should satisfy it. If timing exposes a construction issue, make the smallest production fix consistent with the pinned contract; do not add test-only production flags.

**Step 4 — verify GREEN:** targeted integration test, then runtime/persistence/replay files.

**Step 5 — commit:** if no production change, include this test in C1's commit. Otherwise:

```bash
git add tests/test_runtime_build.py src/polybot/runtime/ingestion.py
git commit -m "test: prove production capture stores no raw frames"
```

---

## Task C3: Preserve shutdown durability with three supervised services

**Files:**
- Modify only if needed: `tests/test_runtime_ingestion.py`

Add a composition test with WS/sampler/trade fake services all started before stop, then assert every service is cancelled and writer closes once. Existing two-service tests remain. Do not modify `IngestionRuntime` unless the new test finds a real bug.

Run:

```bash
./.venv/bin/pytest -o addopts="" tests/test_runtime_ingestion.py -q
```

Commit test with no empty production churn:

```bash
git add tests/test_runtime_ingestion.py
git commit -m "test: pin three-service ingestion shutdown"
```

---

## D4a-ds3 review checkpoint

1. Targeted suite:

```bash
./.venv/bin/pytest -o addopts="" \
  tests/test_runtime_build.py tests/test_runtime_ingestion.py \
  tests/test_midpoint_snapshot.py tests/test_persistence.py tests/test_replay.py -q
```

2. Structural checks:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path('src/polybot/runtime/ingestion.py').read_text()
assert 'PersistingSink' not in text
assert 'sink=None' in text
PY
git diff main --name-only
```

3. Mutation review must kill: restore `PersistingSink(writer)`, omit sampler service, give sampler a different writer/stamper, allow normal sampler return, and remove writer close. Use an isolated worktree and restore every mutation.

---

# Sub-slice D4a-ds4 — co-move snapshot adapter

## Task D1: Build closing bars directly from midpoint batches

**Files:**
- Modify: `tests/test_ers_comove.py`
- Modify after RED: `src/polybot/ers/comove.py`

**Step 1 — RED:** add `_mid_env(observed_at, books)` using the exact schema. Write a test with two batches in bar 0 and one in bar 1; assert the later bar-0 midpoint wins and multiple tokens align.

```python
def test_build_bar_series_reads_midpoint_batches_and_uses_bar_close():
    ...
    assert bars["A"] == {0: Decimal("0.51"), 1: Decimal("0.71")}
    assert bars["B"] == {0: Decimal("0.31"), 1: Decimal("0.35")}
```

**Step 2 — verify RED:** current adapter ignores `clob-midpoint` and returns empty/raw-only bars.

**Step 3 — GREEN:** add a small private snapshot path using `decode_midpoint_batch`; auto-detect snapshots. Do not alter correlation math.

**Step 4 — verify GREEN:** targeted test and full `test_ers_comove.py`.

**Step 5 — commit:**

```bash
git add tests/test_ers_comove.py src/polybot/ers/comove.py
git commit -m "feat: build co-move bars from midpoint batches"
```

---

## Task D2: Respect cutoff and omission without forward-fill

**Step 1 — RED:** add two tests:

- `until=1500` excludes a batch at 2000;
- token B present in bar 0 but omitted in bar 1 yields no B bar-1 point.

**Step 2 — verify RED:** ensure one fails because current new path ignores cutoff or fills incorrectly (if D1 implementation already naturally passes, include these tests in D1 before its implementation/commit instead of pretending a RED).

**Step 3 — GREEN:** use the already bounded envelopes and assign only payload entries. Add no forward-fill map.

**Step 4 — verify GREEN:** comove file.

**Step 5 — commit:** commit only with corresponding production behavior/change; otherwise fold into D1.

---

## Task D3: Auto mode never merges raw and snapshot representations

**Step 1 — RED:** create a store with a raw frame at one price and a midpoint batch at another. Assert auto mode returns only snapshot-derived bars. Also force `source="clob-ws"` and assert the legacy raw result remains available.

**Step 2 — verify RED:** any implementation that merges both should expose the conflicting raw bar.

**Step 3 — GREEN:** if any midpoint row exists under the bounded query, choose only midpoint rows. Explicit non-midpoint source stays on the old replay path.

**Step 4 — verify GREEN:** targeted test.

**Step 5 — commit:**

```bash
git add tests/test_ers_comove.py src/polybot/ers/comove.py
git commit -m "feat: keep midpoint and raw bar sources exclusive"
```

---

## Task D4: Malformed midpoint history fails loud; legacy raw fallback remains exact

**Step 1 — RED:** add:

```python
def test_build_bar_series_fails_loud_on_malformed_midpoint_batch(): ...
def test_build_bar_series_auto_falls_back_to_legacy_raw_store(): ...
def test_build_bar_series_forced_midpoint_source_returns_empty_without_batches(): ...
```

The legacy test should reuse current expected values exactly.

**Step 2 — verify RED:** malformed snapshot may currently be skipped or raw fallback may not be explicit.

**Step 3 — GREEN:** never catch `decode_midpoint_batch` errors. Preserve the existing raw replay block as a helper with unchanged behavior.

**Step 4 — verify GREEN:**

```bash
./.venv/bin/pytest -o addopts="" tests/test_ers_comove.py tests/test_replay.py -q
```

**Step 5 — commit:**

```bash
git add tests/test_ers_comove.py src/polybot/ers/comove.py
git commit -m "feat: preserve fail-loud legacy bar replay"
```

---

## D4a-ds4 review checkpoint

Mutation review must kill:

- use raw rows when snapshots exist;
- merge both representations;
- forward-fill an omitted token;
- ignore `until`;
- use first rather than closing snapshot in a bar;
- catch/skip decoder errors;
- convert Decimal midpoint to float;
- alter `_returns`, `correlation`, `ClusterModel`, or cap consumers.

Verify untouched correlation/risk logic by diff and tests.

---

# Sub-slice D4a-ds5 — operational rate gate and documentation

## Task E1: Add snapshot cadence to deployment example

**Files:**
- Modify: `deploy/config.example.toml`
- Test: `tests/test_runtime_config.py`

**RED:** add a test loading the example TOML and assert `snapshot_interval_seconds == 60.0` and successful validation.

**GREEN:** add the field/comment; update `db_path` comment to say midpoint batches + full trade tape, not raw un-backfillable EventStore.

**Verify:** runtime config test file.

**Commit:**

```bash
git add deploy/config.example.toml tests/test_runtime_config.py
git commit -m "docs: configure downsampled ingestion persistence"
```

---

## Task E2: Add a testable storage-rate calculation

**Files:**
- Create: `scripts/downsample_endurance_check.py`
- Create: `tests/test_downsample_endurance.py`

The script must expose pure helpers before live orchestration:

```python
GIB = 1024 ** 3
SECONDS_PER_DAY = 86400


def projected_gib_per_day(total_bytes, elapsed_seconds):
    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be > 0")
    return total_bytes / elapsed_seconds * SECONDS_PER_DAY / GIB


def footprint(paths):
    return sum(path.stat().st_size for path in paths if path.exists())
```

**RED:** test exact arithmetic for a known byte/time pair, reject zero elapsed, and include DB+WAL while ignoring missing SHM.

**GREEN:** implement helpers only.

**Verify:** targeted new test.

**Commit:**

```bash
git add scripts/downsample_endurance_check.py tests/test_downsample_endurance.py
git commit -m "test: calculate downsampled store growth rate"
```

---

## Task E3: Implement the bounded public-data endurance runner

**Files:** same as E2.

**RED:** test a pure result validator against a temporary `EventStore` containing:

- one valid `clob-midpoint` row;
- one `data-api` row;
- no raw row -> PASS;
- then add a `clob-ws` row -> FAIL with a named reason;
- malformed midpoint -> FAIL;
- projected rate above 0.5 -> FAIL.

**GREEN:** implement result collection/validation, then `main()`:

- CLI flags: `--seconds` (default 1800), `--max-gib-per-day` (default 0.5), `--universe-max-markets` (default 200), optional `--keep-db`;
- create a temporary directory/database;
- build the real runtime with Data API enabled and 60-second snapshots;
- run for the bounded duration, request graceful stop, and await completion;
- calculate final DB+WAL+SHM footprint;
- query source counts and decode every midpoint row;
- print elapsed, bytes, source counts, usable quote count, projected GiB/day, and PASS/FAIL;
- exit nonzero on HALT, no midpoint, no trade, any raw row, malformed batch, no usable books, or rate above ceiling;
- public APIs only; no auth, keys, intents, orders, or service changes.

**Verify:** targeted test, then run a short non-release smoke such as `--seconds 70 --universe-max-markets 5`; it must persist at least one minute batch and exit 0. The 30-minute release gate is later Task G1.

**Commit:**

```bash
git add scripts/downsample_endurance_check.py tests/test_downsample_endurance.py
git commit -m "feat: add live downsample storage-rate gate"
```

---

## Task E4: Reconcile documentation only after code is green

**Files:**
- Modify: `docs/DESIGN-D4a-INGESTION-RUNTIME.md`
- Modify: `docs/HANDOFF.md`
- Modify: `deploy/README.md`
- Keep current: `docs/DESIGN-D4a-DOWNSAMPLE.md`
- Keep current: `docs/PLAN-D4a-DOWNSAMPLE.md`

Required corrections:

- D4a no longer captures raw CLOB history in production.
- One 60-second versioned midpoint batch + full deduplicated trade tape is the substrate.
- Synthetic events are no longer recomputable from production history and remain deferred pending a tuned live contract.
- State the 0.5 GiB/day release ceiling and real endurance evidence when available.
- Keep service state STOPPED/DISABLED until deployment approval.
- Replace any claim that downsample is still pending only after the code and live gate truly pass.
- Document old raw DB preservation and fresh corrected DB startup.
- Correct deploy instructions to the GitHub-authoritative checkout pattern; do not recreate `/root/git/polymarket-bot.git`.

Run `git diff --check`; review wording against actual results. Commit only truthful state.

---

# Final verification and review

## Task F1: Full local verification

Run, unpiped:

```bash
./.venv/bin/pytest -o addopts="" -q
```

Required: all tests pass, explicit count greater than 1145, zero skips/xfails, exit 0.

Then:

```bash
git diff --check
python3 -m compileall -q src scripts
python3 - <<'PY'
from pathlib import Path
text = Path('src/polybot/runtime/ingestion.py').read_text()
assert 'PersistingSink' not in text
for forbidden in ('ers.validator', 'ers.signer', 'IntentStore', 'propose_trade'):
    assert forbidden not in text
PY
git status --short
git diff main --name-only
```

Remove only generated `__pycache__` artifacts created by verification; never remove source/data.

---

## Task F2: Independent whole-slice spec-compliance review

Dispatch a fresh reviewer against the current branch. Require it to:

- read `AGENTS.md`, the DESIGN, and this PLAN;
- inspect every changed file and compare to §4/§5/§6;
- run targeted and full tests;
- verify additive/untouched-file and sacred-surface invariants;
- report only concrete overbuild, underbuild, correctness, safety, or test-evidence gaps.

Fix every confirmed blocker through a new RED→GREEN cycle. Then dispatch a fresh post-fix review that explicitly supersedes stale findings.

---

## Task F3: Adversarial whole-slice mutation battery

Use an isolated temporary git worktree at the reviewed commit, never the active branch. Apply one mutation at a time, run the named target test, record that it fails, then restore before the next mutation.

Required cross-cutting mutations:

1. Restore `sink=PersistingSink(writer)` in production.
2. Emit one snapshot row per token instead of one batch.
3. Include stale books by using bid/ask even when midpoint is `None`.
4. Accept unknown schema.
5. Coerce numeric JSON prices.
6. Trust a forged midpoint.
7. Merge raw and midpoint bars.
8. Forward-fill an omitted token.
9. Disable the `until` cutoff.
10. Swallow snapshot writer errors.
11. Remove the sampler from supervision.
12. Remove/skip writer close on halt.
13. Change Data API payload/projection/dedup behavior.
14. Touch or bypass a sacred risk/signing surface.

Every mutation needs a named killed test or structural assertion. Any survivor is a blocker: add a focused RED, implement minimal coverage/behavior, re-run the entire battery, and obtain fresh review.

---

# Live release gate and landing

## Task G1: Run the 30-minute real-venue endurance gate

Only after local tests and both reviews are green:

```bash
./.venv/bin/python scripts/downsample_endurance_check.py \
  --seconds 1800 \
  --universe-max-markets 200 \
  --max-gib-per-day 0.5
```

Required real output:

- exit 0;
- at least one `clob-midpoint` batch and usable quote;
- at least one `data-api` trade row;
- exactly zero `clob-ws` rows;
- all midpoint batches decode;
- projected total DB+WAL+SHM footprint `<=0.5 GiB/day`;
- no collector/writer HALT;
- graceful writer close.

If the ceiling fails, do not loosen it. Stop and return to owner brainstorming for an explicit compact trade schema.

Record the exact elapsed time, bytes, source counts, quote counts, and projection in the docs and final report.

---

## Task G2: Final branch cleanliness

```bash
git status --porcelain
```

Before merge, only intended committed files may exist; output must be empty. Search source for mutation markers and generated artifacts. Re-run the full suite after any cleanup.

---

## Task G3: Merge, push, and deployment gates

These are separate approvals/actions:

1. Present branch diff, commits, reviews, full test count, and live rate result to the owner.
2. Ask before merging if not already authorized. Merge to `main` with `--no-ff` and a message containing verification status.
3. Re-run the full suite on merged `main`.
4. Ask before pushing. After push, verify local `HEAD`, `origin/main`, and `git ls-remote` all match.
5. Post a POL-13 YouTrack progress comment only after the slice lands; do not claim state transition capability.
6. Ask separately before deployment/service changes.
7. Deployment must:
   - preserve the existing raw DB under an evidence filename;
   - record its size and SHA-256 checksum without printing secrets;
   - repair `/opt/polymarket-bot` to the GitHub-authoritative service checkout pattern, not recreate `/root/git/polymarket-bot.git`;
   - start with a fresh `market_memory.db`;
   - install/update while service remains stopped;
   - start/enable only on explicit approval;
   - verify unit status, heartbeat, source counts, zero raw rows, and observed growth.

---

## Files expected to change

**Create:**

- `docs/DESIGN-D4a-DOWNSAMPLE.md`
- `docs/PLAN-D4a-DOWNSAMPLE.md`
- `src/polybot/ingestion/midpoint.py`
- `tests/test_midpoint_snapshot.py`
- `scripts/downsample_endurance_check.py`
- `tests/test_downsample_endurance.py`

**Modify:**

- `src/polybot/runtime/config.py`
- `src/polybot/runtime/ingestion.py`
- `src/polybot/ers/comove.py` (adapter only)
- `tests/test_runtime_config.py`
- `tests/test_runtime_build.py`
- `tests/test_runtime_ingestion.py` only if needed to pin three-service shutdown
- `tests/test_ers_comove.py`
- `deploy/config.example.toml`
- `docs/DESIGN-D4a-INGESTION-RUNTIME.md`
- `docs/HANDOFF.md`
- `deploy/README.md`

**Must remain untouched:**

- `src/polybot/ers/validator.py`
- `src/polybot/ers/intent_store.py` / `propose_trade`
- `src/polybot/ers/service.py` / `evaluate_intent`
- signed risk caps and every signer/order/wallet surface
- Data API poller and EventStore unless a genuine blocker is separately escalated and owner-approved

---

## Definition of complete

This plan is complete only when the branch is fully green and reviewed, the real 30-minute storage gate passes, documentation states only verified facts, and no production/deployment action is claimed without evidence. A code-complete branch is not permission to merge, push, or start the service.
