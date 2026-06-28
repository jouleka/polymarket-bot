# S6 / POL-8 — Hermes Integration + Signal Fusion + Citation Truth-Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the full propose→dispose pipeline in shadow — a propose-only facade Hermes can only enqueue through, the ERS independently re-deriving truth-gate / fusion / anchor-clamp / calibration `k` / detector veto / cluster cap / breaker, sizing via the **unchanged** validator, end-to-end on the `PaperSigner`.

**Architecture:** ~80% integration of already-tested S1/S3/S5/S7 units + 3 net-new pure modules (`FusionEngine`, `CitationTruthGate`, `ProposeOnlyFacade`) + a `publisher_group` source-independence extension + a `ComponentLog` sidecar + a reviewed Hermes config artifact. `validator.evaluate_intent` is **NOT modified** — S6 wires *around* it via `process_pending(pipeline=...)`. **Corroboration (≥2 independent `publisher_group`s) is the single key** that unlocks Hermes's weight (`w_news` 0→0.20) and widens the Anchor band. Live-Hermes MCP transport, adaptive fusion, the `MarketRegistry`, resolution-feedback, and edge-hurdle H are deferred.

**Tech Stack:** Python 3.11+ (src-layout, `pythonpath=["src"]`), `Decimal` money math, SQLite stores (WAL), stdlib-only (runtime deps: `httpx`, `websockets`), `pytest` via `./.venv/bin/pytest`, no `pytest-asyncio` (bare `asyncio.run`). Spec: [`DESIGN-S6-HERMES.md`](DESIGN-S6-HERMES.md).

---

## Task ordering & dependencies

Build in order. Tasks 1–7 are independent pure units (1 must precede 4). Tasks 8–9 (integration + e2e) depend on ALL of 1–7. Task 10 is independent.

1. `publisher_group` source-independence extension — `ingestion/news.py` + `allowlist.py`
2. `FusionEngine` (weighted log-odds fold) — `fusion/engine.py`
3. `ComponentLog` sidecar — `fusion/component_log.py`
4. `CitationTruthGate` — `truthgate/gate.py` *(needs Task 1)*
5. `ProposeOnlyFacade` — `ers/facade.py`
6. `DetectorOrchestrator` — `detectors/orchestrator.py`
7. `StubMarketMeta` — `ers/market_meta.py`
8. `process_pending` S6 wiring (`HermesPipeline`) — `ers/service.py` *(integration; needs 1–7)*
9. End-to-end shadow test — `tests/test_ers_hermes_e2e.py` *(acceptance gate; uses the real units 1–7)*
10. Hermes `config.yaml` artifact — `deploy/hermes/config.yaml`

## TDD discipline note

Each unit is built RED→GREEN→commit, one concern per test. **Caveat (Task 2 + a few others):** some safety-case tests are *satisfied-by-construction* once the core code lands (their "RED" is a pass-by-construction lock rather than a true failing import) — these are flagged in-step. The genuinely-new-code cycles have a true RED. If you want a strict failing-RED for every case, split the impl into incremental stubs; the repo's frozen-dataclass `_verify` pattern otherwise doesn't use intermediate states.

## Opus review checkpoints (team standard)

After **Task 8** (integration) and again after **Task 9** (e2e), dispatch a `superpowers:code-reviewer` subagent with **`model: opus`** explicitly pinned; triage rigorously; re-review after any safety-critical fix (two passes, per the team convention). Probe especially the open risks below.

## Open risks for the Opus review to probe (DESIGN §10 + emerged in planning)

- **Truth-gate same-source check is a single-snapshot proxy:** `verify()` gets one book and no baseline mid, so "p-shift + thin-book mid-move from one fresh source" is operationalized as *thin top-of-book depth (`< thin_book_depth_usd`) + wide spread (`≥ thin_book_move`) + one source within `freshness_window_ns`*. A true before/after mid-diff needs a baseline-mid seam threaded through `process_pending`. Probe whether the proxy can miss an injection whose book isn't thin/wide.
- **Fusion `p_base = mid` (inert) in S6:** no market-level base-rate feed yet → fusion's `p_base` contributes zero delta; the prior enters via the Anchor Gate's `PriorEngine`. `w_base` (0.30) is configured for the deferred feed.
- **`w_news = 0.20`** corroboration-gated + anchor-clamped; probe whether 0.20 + the clip bound can over-move a thin market within the band.
- **`midpoint()`** is both the fusion prior and the anchor reference; probe near-degenerate mids (guarded by `midpoint() is None → REJECT book_stale`).
- **Forecast-recording predicate** (record before `evaluate_intent` so SKIPs still log) — over-counting vs calibration-grades-estimates.
- **Stub `category="unknown"` must keep `k=0`** (paper-only) — confirm nothing makes `k>0` before the real resolver + resolution feedback exist.

---

## Repo conventions & fixture cookbook

I have all the ground truth needed. Here is the complete brief.

---

# polymarket-bot test-conventions brief (shared ground truth for S6 plan agents)

## (a) Test layout, pytest command, import style

**Layout.** FLAT `tests/` dir (no subpackages). One test file per source module, named `test_<area>_<module>.py`:
- ERS → `tests/test_ers_validator.py`, `test_ers_intent_store.py`, `test_ers_service.py`, `test_ers_caps.py`, `test_ers_comove.py`, `test_ers_breaker.py`
- calibration → `tests/test_calibration_{gate,ledger,config,prior,anchor,tracker,scoring}.py`
- detectors → `tests/test_detectors_{classify,composite,pnl,sybil,config,signals,toxicity,luck,policy}.py`
- ingestion → `tests/test_news.py`, `test_orderbook.py`, `test_market_stream.py`, `test_market_socket.py`, `test_sharding.py`, `test_gamma_normalizer.py`, `test_synthetic.py`, `test_envelope.py`, `test_sanitizer.py`, `test_data_api.py`, `test_polygon.py`, `test_ratelimit.py`, `test_retry.py`, `test_transport.py`
- storage → `tests/test_market_memory.py`, `test_event_writer.py`, `test_replay.py`, `test_persistence.py`
- core → `tests/test_clock.py`

**New S6 test files should follow the same convention** (e.g. `tests/test_ers_service.py` already exists — add new cases there or a new `test_<area>_<module>.py`). `tests/__init__.py` exists (tests is a package).

**pyproject.toml (verbatim pytest config):**
```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "-q"
```
Package import root is `src/` (src-layout); top package is `polybot`. `requires-python = ">=3.11"`. Deps: `httpx>=0.28`, `websockets>=16` only — **no pytest-asyncio**.

**Exact pytest invocation (from repo root, WSL):**
```
./.venv/bin/pytest            # all 377+ tests
./.venv/bin/pytest tests/test_ers_service.py
./.venv/bin/pytest tests/test_ers_service.py::test_name
```
The venv is built with `uv venv --python 3.13`. `pythonpath=["src"]` means tests import `from polybot...` directly — no install step, no `sys.path` hacks.

**Import / style conventions (uniform across the codebase):**
- `from decimal import Decimal` at top of every money/probability test; all money + probabilities are `Decimal`, constructed from **strings** (`Decimal("0.55")`, `Decimal(str(p))`), never floats. Detector *statistical* sub-scores are plain `float` (luck/anchor/composite use float internally).
- Import the concrete symbols, not the module: `from polybot.ers.validator import ClusterView, Decision, OpenPosition, Portfolio, TradeIntent, evaluate_intent`.
- **Async tests use bare `asyncio.run(...)`** inside a sync `def test_...` (no `@pytest.mark.asyncio`). See `test_news.py`: `n = asyncio.run(poller.poll_source("fed-press"))`.
- SQLite-backed objects: use the `tmp_path` fixture and `str(tmp_path / "x.db")`; wrap in `with ... as store:` (all stores are context managers). Some ingestion tests use `tempfile.mktemp(suffix=".db")` instead — `tmp_path` is preferred for new code.
- Module-level constant fixtures in UPPER_SNAKE (e.g. `_PASSING`, `CFG = DetectorConfig()`, `_PROPOSAL = dict(...)`, `_FED = Source(...)`), plus small private `_helper()` factory functions (`_book`, `_intent`, `_portfolio`, `_pos`, `_ledger`, `_build`, `_store`). No `conftest.py`; no shared fixtures file — each test file defines its own local builders.
- Assertion style: plain `assert`, often two clauses on one line: `assert d.verdict == "REJECT" and d.reason == "degenerate_price"`. Loop-with-message asserts for parametric cases: `assert d.verdict == "REJECT" and d.reason == "bad_probability", p`. No `unittest`, no custom assert helpers.
- `pytest.raises(ValueError, match="allowlist")` for fail-loud paths; `inspect.signature(...).parameters` to assert an absent kwarg (chokepoint invariant test).
- One concern per test; long docstring at top of each file explaining the safety property under test.

---

## (b) Fixture cookbook (copy-pasteable constructors)

```python
from decimal import Decimal

# --- core clock ---
from polybot.core.clock import MonotonicStamper
stamper = MonotonicStamper()                      # or MonotonicStamper(clock=lambda: 1) for determinism
ts = stamper.stamp()                              # strictly-increasing int (ns)

# --- order book (re-priced off the touch) ---
from polybot.ingestion.orderbook import LocalBook
def _book(ask, ask_size="1000", bid="0.01", bid_size="1000"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book
book = _book("0.50")                              # midpoint 0.255, best_ask 0.50, depth 1000
book.mark_stale()                                 # -> is_stale() True, midpoint() None

# --- Market-Memory store + Envelope ---
from polybot.storage.market_memory import EventStore
from polybot.ingestion.envelope import make_envelope
with EventStore(str(tmp_path / "ev.db")) as store:        # ctx-manager; check_same_thread=True default
    store.append(make_envelope(stamper, source="fed-press", source_tier="PRIMARY",
                               event_id="g1", content="text",
                               published_at=None, entities=(), market_links=("token1",)))
    rows = store.all()                                    # list[Envelope], observed_at order
    rows = store.replay_until(cutoff_observed_at)         # point-in-time slice
# Envelope fields: source, source_tier, event_id, observed_at, content,
#                  published_at=None, entities=(), market_links=(), trust="UNTRUSTED"

# --- News Source + poller (async via asyncio.run) ---
from polybot.ingestion.news import Source, NewsPoller, PRIMARY, DISCOVERY
src = Source("fed-press", "https://primary.example/fed.xml", PRIMARY)   # (name, url, tier, kind="rss")
async def _fetch(url): return RSS_TEXT
poller = NewsPoller(_fetch, stamper, store, allowlist=[src])
import asyncio; n = asyncio.run(poller.poll_source("fed-press"))

# --- ERS signed caps (defaults are self-consistent; any kwarg override re-verifies) ---
from polybot.ers.caps import RiskCaps
caps = RiskCaps()                                 # nav=300, per_trade=12, total_open_risk=60, ...
caps = RiskCaps(min_n=...)  # NO — RiskCaps takes the money fields below, not calib fields

# --- ERS validator objects ---
from polybot.ers.validator import (TradeIntent, OpenPosition, Portfolio, Decision,
                                    ClusterView, evaluate_intent)
intent = TradeIntent(token_id="t1", condition_id="m1", event_id="e1",
                     resolution_source="s1", cluster_id="c1", p=Decimal("0.9"),
                     max_price=Decimal("0.60"), size_usd_suggestion=Decimal("100"),
                     matrix_cold=True)
pos = OpenPosition(condition_id="mX", event_id="eX", resolution_source="sX",
                   cluster_id="cX", worst_case_risk=Decimal("12"), matrix_cold=False,
                   token_id="tX", entry_price=Decimal("0.50"), frozen=False)
portfolio = Portfolio(nav=Decimal("300"), positions=(pos,))
d = evaluate_intent(intent, book, portfolio, caps, calib_score=Decimal(1),
                    cluster=ClusterView(warm=False, rho=None))
assert d.verdict in ("ACCEPT", "REJECT", "SKIP") and d.reason and d.stake_usd and d.price_exec

# --- ERS chokepoint store ---
from polybot.ers.intent_store import IntentStore
with IntentStore(str(tmp_path / "i.db"), stamper) as store:
    store.propose_trade("intent-1", token_id="t1", condition_id="0xabc", event_id="e1",
                        side="BUY", target_price="0.55", max_price="0.60",
                        size_usd_suggestion="10", p="0.7", p_confidence="0.6",
                        resolution_summary="...", thesis="...",
                        citations=("https://primary/1",))          # numeric fields = strings
    for pi in store.pending(): ...                                 # list[PendingIntent], FIFO by rowid
    store.record_decision("intent-1", Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly"))
    store.get("intent-1"); store.audit_log()

# --- ERS service loop ---
from polybot.ers.service import PaperSigner, process_pending
signer = PaperSigner()                            # .placed, .flattened lists
def book_for(token_id): return _book("0.50")      # injected book lookup; return None -> REJECT no_book
portfolio = process_pending(store, book_for=book_for, portfolio=Portfolio(nav=Decimal("300")),
                            caps=RiskCaps(), signer=signer, calib_score=Decimal(1),
                            cluster_model=None, breaker=None)

# --- ERS comove model + bar series + breaker ---
from polybot.ers.comove import ClusterModel, build_bar_series, correlation
model = ClusterModel({"tA": {0: Decimal("0.5"), 1: Decimal("0.6"), ...},
                      "tB": {0: Decimal("0.4"), 1: Decimal("0.5"), ...}}, min_observations=30)
view = model.view(["tA", "tB"])                   # -> ClusterView(warm, rho)
bars = build_bar_series(event_store, bar_ns=60_000_000_000, until=None, source="clob-ws")
from polybot.ers.breaker import DrawdownBreaker, BreakerState, NONE, FREEZE_ADDS, FLATTEN
breaker = DrawdownBreaker(RiskCaps(), clock=lambda: 0)        # clock returns SECONDS
state = breaker.evaluate(portfolio.positions, book_for)      # -> BreakerState(action, drawdown, triggers)

# --- calibration ---
from polybot.calibration.config import CalibrationConfig
from polybot.calibration.gate import CalibrationGate
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.prior import PriorEngine
cfg = CalibrationConfig()                          # defaults self-consistent; CalibrationConfig(min_n=20) for tests
with ForecastLedger(str(tmp_path / "f.db"), stamper) as ledger:
    ledger.record_forecast("f1", category="politics", condition_id="c",
                           p=Decimal("0.7"), market_mid=Decimal("0.5"))
    ledger.record_resolution("f1", "WON")          # WON|LOST|DISPUTED_LOST|VOID
    gate = CalibrationGate(ledger, PriorEngine(), cfg)
    k = gate.k_for("politics")                      # Decimal 0 or 1
    r = gate.clamp_p(Decimal("0.99"), Decimal("0.5"), question_text="Will the incumbent win?",
                     seconds_to_resolution=10**9, corroborated=False)   # -> AnchorResult

# --- detectors ---
from polybot.detectors.config import DetectorConfig
from polybot.detectors.composite import composite, CompositeScore, LOW, MED, HIGH, CRITICAL
from polybot.detectors.classify import classify, SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE
from polybot.detectors.policy import decide, DetectorDecision, FOLLOW, AVOID, FLAG_ONLY, FOLLOW_ENABLED
from polybot.detectors.toxicity import toxicity, Toxicity
DCFG = DetectorConfig()
s = composite({"D1": 0.85, "D2": 0.0}, DCFG)                 # -> CompositeScore(value, band)
tox = toxicity(Decimal("90"), Decimal("10"), baseline_mean=Decimal("0.2"),
               baseline_std=Decimal("0.1"), config=DCFG)
dec = decide(composite_band=HIGH, classification=INSIDER_LIKE, pull_quotes=True)
```

---

## (c) Verbatim signatures and field-lists

### `polybot/ers/validator.py`
```python
@dataclass(frozen=True)
class TradeIntent:
    token_id: str
    condition_id: str
    event_id: str
    resolution_source: str
    cluster_id: str
    p: Decimal
    max_price: Decimal
    size_usd_suggestion: Decimal
    matrix_cold: bool = True

@dataclass(frozen=True)
class OpenPosition:
    condition_id: str
    event_id: str
    resolution_source: str
    cluster_id: str
    worst_case_risk: Decimal
    matrix_cold: bool = True
    token_id: str = ""
    entry_price: Decimal = Decimal(0)
    frozen: bool = False

@dataclass(frozen=True)
class Portfolio:
    nav: Decimal
    positions: tuple = ()
    # methods: total_open_risk(); market_risk(condition_id); event_risk(event_id);
    #          source_risk(resolution_source); cluster_risk(cluster_id); matrix_cold_count()

@dataclass(frozen=True)
class Decision:
    verdict: str                  # "ACCEPT" | "REJECT" | "SKIP"
    stake_usd: Decimal | None
    price_exec: Decimal | None
    reason: str
# Constructed POSITIONALLY everywhere: Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly")
#   REJECT/SKIP: Decision("REJECT", None, Decimal("0.55"), "book_stale") / Decision("SKIP", None, price, "no_edge")

@dataclass(frozen=True)
class ClusterView:
    warm: bool
    rho: Decimal | None = None
# fail-closed default used by evaluate_intent: ClusterView(warm=False, rho=None)

def evaluate_intent(intent, book, portfolio, caps, *, calib_score=Decimal(1), cluster=_COLD_CLUSTER):
    # _COLD_CLUSTER = ClusterView(warm=False, rho=None)
```
Reason codes emitted: `book_stale, degenerate_price, price_above_limit, bad_probability, bad_calibration, no_edge, max_concurrent, matrix_cold_concurrent, bad_cluster, per_trade_cap, per_market_cap, per_event_cap, per_source_cap, total_open_cap, size_suggestion, liquidity_cap, per_cluster_cap, kelly, below_min_floor`.

### `polybot/ers/intent_store.py`
```python
@dataclass(frozen=True)
class PendingIntent:
    intent_id: str
    status: str
    token_id: str
    condition_id: str
    event_id: str
    side: str
    target_price: Decimal
    max_price: Decimal
    size_usd_suggestion: Decimal
    p: Decimal
    p_confidence: Decimal
    resolution_summary: str
    thesis: str
    citations: tuple
    created_at: int
    decided_at: int | None = None
    decision_verdict: str | None = None
    decision_stake_usd: Decimal | None = None
    decision_price_exec: Decimal | None = None
    decision_reason: str | None = None

class IntentStore:
    def __init__(self, path, stamper): ...                 # SQLite path + a MonotonicStamper; ctx-manager
    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()): ...  # -> bool (True if newly inserted)
    def record_decision(self, intent_id, decision): ...    # decision is a validator.Decision
    def pending(self): ...                                 # -> list[PendingIntent], status=PROPOSED, FIFO by rowid
    def get(self, intent_id): ...                          # -> PendingIntent | None
    def audit_log(self): ...   # -> list[dict] keys: intent_id, at, verdict, stake_usd, price_exec, reason
```
Status map: `{"ACCEPT":"ACCEPTED", "REJECT":"REJECTED", "SKIP":"SKIPPED"}`. `propose_trade` has **no `status` param** (the chokepoint).

### `polybot/ers/service.py`
```python
class PaperSigner:
    def __init__(self): self.placed = []; self.flattened = []
    def place(self, intent, decision):     # appends {intent_id, token_id, stake_usd, price_exec}
    def flatten(self, positions):          # appends tuple(p.token_id for p in positions)

def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None):
    # runs breaker.evaluate(portfolio.positions, book_for) FIRST; FLATTEN->signer.flatten + block;
    # FREEZE_ADDS->block; per intent: _cluster_view -> _to_trade_intent -> book_for(token_id)
    # (None -> REJECT "no_book") -> evaluate_intent -> record_decision -> on ACCEPT: signer.place + _fold.
    # try/except wraps each intent -> REJECT "internal_error". Returns updated Portfolio.

def _cluster_view(cluster_model, intent, portfolio):
    if cluster_model is None:
        return _COLD                         # ClusterView(warm=False, rho=None)
    cluster_id = intent.event_id             # cluster_id is the event_id PLACEHOLDER
    tokens = [intent.token_id]
    tokens += [p.token_id for p in portfolio.positions if p.cluster_id == cluster_id]
    return cluster_model.view(tokens)

def _to_trade_intent(intent, *, matrix_cold):
    return TradeIntent(
        token_id=intent.token_id, condition_id=intent.condition_id, event_id=intent.event_id,
        resolution_source=intent.condition_id, cluster_id=intent.event_id,
        p=intent.p, max_price=intent.max_price, size_usd_suggestion=intent.size_usd_suggestion,
        matrix_cold=matrix_cold,
    )

def _fold(portfolio, trade_intent, decision):
    pos = OpenPosition(
        condition_id=trade_intent.condition_id, event_id=trade_intent.event_id,
        resolution_source=trade_intent.resolution_source, cluster_id=trade_intent.cluster_id,
        worst_case_risk=decision.stake_usd, matrix_cold=trade_intent.matrix_cold,
        token_id=trade_intent.token_id, entry_price=decision.price_exec, frozen=False,
    )
    return Portfolio(nav=portfolio.nav, positions=portfolio.positions + (pos,))
```
Note: `resolution_source` and `cluster_id` both alias `intent.condition_id` / `intent.event_id` respectively (slice-2/3 placeholders). `block_reason` codes: `l7_flatten`, `l7_freeze`.

### `polybot/ers/caps.py` — `RiskCaps` (all fields have defaults; `RiskCaps()` is valid)
```python
@dataclass(frozen=True)
class RiskCaps:
    nav: Decimal = Decimal("300")
    total_open_risk: Decimal = Decimal("60")
    reserve_floor: Decimal = Decimal("240")          # MUST equal nav - total_open_risk
    per_trade: Decimal = Decimal("12")
    per_market: Decimal = Decimal("18")
    per_event_union: Decimal = Decimal("24")
    per_negrisk_event: Decimal = Decimal("18")
    per_source_open: Decimal = Decimal("30")
    per_source_locked_effective: Decimal = Decimal("18")
    max_locked_to_resolution: Decimal = Decimal("36")
    max_concurrent: int = 4
    matrix_cold_concurrent: int = 3
    daily_pending_ceiling: Decimal = Decimal("24")
    kelly_fraction: Decimal = Decimal("0.25")
    min_position_floor: Decimal = Decimal("5")
    liquidity_depth_frac: Decimal = Decimal("0.10")
    liquidity_impact_cents: Decimal = Decimal("1")
    l7_freeze_floor: Decimal = Decimal("18")
    l7_flatten_floor: Decimal = Decimal("30")
    l7_velocity_delta: Decimal = Decimal("18")
    l7_velocity_window_seconds: int = 900
    # methods: cluster_cap(rho) -> Decimal; content_hash() -> sha256 hex
```
Construction in tests is **always `RiskCaps()`** (defaults). `__post_init__` runs `_verify()` and raises `ValueError` on any inconsistent override (breaker ordering, zero-slack concurrency, reserve identity, 20%-NAV ceiling, kelly in (0,0.5], floor>=$5, L7 ordering). A "signed envelope with a bad value" test = `pytest.raises(ValueError, match=...)` with one field overridden.

### `polybot/ers/comove.py`
```python
class ClusterModel:
    def __init__(self, bars, *, min_observations=30): ...   # bars = {token_id: {bar_index: Decimal midpoint}}
    def view(self, token_ids): ...                          # -> ClusterView; cold if <2 tokens or any pair <min_obs

def correlation(returns_a, returns_b): ...   # Pearson, Decimal-only (float raises), fail-closed +1
def build_bar_series(store, *, bar_ns, until=None, source="clob-ws"): ...  # -> {token: {bar_index: Decimal}}
```

### `polybot/ers/breaker.py`
```python
NONE = "NONE"; FREEZE_ADDS = "FREEZE_ADDS"; FLATTEN = "FLATTEN"

@dataclass(frozen=True)
class BreakerState:
    action: str         # NONE | FREEZE_ADDS | FLATTEN
    drawdown: Decimal
    triggers: tuple     # subset of: velocity, position_loss, stale_mark, freeze_floor, flatten_floor

class DrawdownBreaker:
    def __init__(self, caps, *, clock): ...     # clock() -> monotonic SECONDS
    def evaluate(self, positions, book_for): ... # -> BreakerState
```

### `polybot/calibration/gate.py`
```python
class CalibrationGate:
    def __init__(self, ledger, prior_engine, config): ...
    def k_for(self, category): ...               # -> Decimal (0 or 1)
    def report_for(self, category): ...          # -> CalibrationReport
    def clamp_p(self, p, market_mid, *, question_text, seconds_to_resolution, corroborated): ...  # -> AnchorResult
```

### `polybot/calibration/anchor.py`
```python
@dataclass(frozen=True)
class AnchorResult:
    p_clamped: Decimal
    shrunk: bool
    reason: str   # within_band | clamped_low | clamped_high | anchor_conflict

def anchor_gate(p, market_mid, prior, *, seconds_to_resolution, corroborated, config): ...
# raises ValueError on non-finite p/market_mid/prior
```

### `polybot/calibration/ledger.py`
```python
VALID_STATUSES = ("WON", "LOST", "DISPUTED_LOST", "VOID")

@dataclass(frozen=True)
class ForecastRecord:
    forecast_id: str
    category: str
    condition_id: str
    p: Decimal
    market_mid: Decimal
    created_at: int
    resolution_status: str | None = None
    resolved_at: int | None = None

class ForecastLedger:
    def __init__(self, path, stamper): ...        # SQLite path + stamper; ctx-manager
    def record_forecast(self, forecast_id, *, category, condition_id, p, market_mid): ...  # -> bool; raises on non-finite/out-of-[0,1]
    def record_resolution(self, forecast_id, status): ...   # raises ValueError (bad status) / KeyError (unknown id)
    def resolved(self, category=None): ...        # -> list[ForecastRecord]
    def get(self, forecast_id); def all(self)
```

### `polybot/calibration/config.py` — `CalibrationConfig` (defaults valid; `CalibrationConfig()` works)
```python
@dataclass(frozen=True)
class CalibrationConfig:
    min_n: int = 150
    n_bins: int = 10
    reliability_max: Decimal = Decimal("0.03")
    brier_skill_min: Decimal = Decimal("0")
    longshot_lambda: Decimal = Decimal("0.9")
    max_shift_uncorroborated: Decimal = Decimal("1.0")
    max_shift_corroborated: Decimal = Decimal("2.5")
    prior_decay_window_seconds: int = 86400
    epsilon: Decimal = Decimal("0.001")
    # __post_init__ -> _verify() raises ValueError on nonsense. Tests pass overrides like CalibrationConfig(min_n=20).
```

### `polybot/calibration/prior.py`
```python
DEFAULT_REFERENCE_CLASSES = {"incumbent_reelection": Decimal("0.90"),
                             "scheduled_fed_hold": Decimal("0.97"),
                             "favorite_by_large_spread": Decimal("0.85")}
DEFAULT_KEYWORDS = {"incumbent": "incumbent_reelection", "re-elect": "incumbent_reelection",
                    "fed hold": "scheduled_fed_hold", "rate unchanged": "scheduled_fed_hold",
                    "favorite": "favorite_by_large_spread"}

class PriorEngine:
    def __init__(self, reference_classes=None, keyword_map=None, longshot_lambda=Decimal("0.9")): ...
    def base_rate(self, reference_class): ...   # -> shrunk Decimal | None
    def classify(self, text): ...               # -> reference_class str | None
    def prior_for(self, text): ...              # -> shrunk Decimal | None
```
`PriorEngine()` (no args) is the standard test construction.

### `polybot/calibration/tracker.py` (used internally by gate; for `test_calibration_tracker.py`)
```python
@dataclass(frozen=True)
class CalibrationReport:
    category, n_scored, n_disputed, n_void, bot_brier, market_brier, brier_skill,
    reliability, resolution, uncertainty, go, k   # go: bool, k: Decimal
class CalibrationTracker:
    def __init__(self, ledger, config): ...
    def k_for(self, category); def report_for(self, category)
```

### `polybot/detectors/config.py` — `DetectorConfig` (defaults valid)
```python
@dataclass(frozen=True)
class DetectorConfig:
    min_resolved: int = 50
    win_significance: Decimal = Decimal("0.001")
    edge_ci_confidence: Decimal = Decimal("0.99")
    max_event_dominance: Decimal = Decimal("0.5")
    mm_min_trades: int = 100
    mm_balance_min: Decimal = Decimal("0.4")
    toxicity_ratio_min: Decimal = Decimal("0.75")
    toxicity_z_min: Decimal = Decimal("2.0")
    band_low_max: Decimal = Decimal("2.5")
    band_med_max: Decimal = Decimal("5.0")
    band_high_max: Decimal = Decimal("7.5")
    critical_subscore: Decimal = Decimal("0.8")
    # __post_init__ -> _verify() raises ValueError on nonsense.
```

### `polybot/detectors/composite.py`
```python
LOW = "LOW"; MED = "MED"; HIGH = "HIGH"; CRITICAL = "CRITICAL"
@dataclass(frozen=True)
class CompositeScore:
    value: float   # 0..10
    band: str
def composite(subscores, config, weights=None): ...   # subscores: dict[str,float]; clamps to [0,1]; NaN->0
```

### `polybot/detectors/classify.py`
```python
SHARP="SHARP"; LUCKY="LUCKY"; MARKET_MAKER="MARKET_MAKER"; INSIDER_LIKE="INSIDER_LIKE"; NOISE="NOISE"
def classify(*, edge_passes, raw_mean_edge, trade_count, buy_volume, sell_volume,
             insider_band, config): ...   # -> one of the 5 string constants
```

### `polybot/detectors/policy.py`  ← **FOLLOW_ENABLED flag lives here**
```python
FOLLOW = "FOLLOW"; AVOID = "AVOID"; FLAG_ONLY = "FLAG_ONLY"
FOLLOW_ENABLED = False        # module constant, pinned False (the only FOLLOW branch is dead code)

@dataclass(frozen=True)
class DetectorDecision:
    action: str          # AVOID | FLAG_ONLY (never FOLLOW while FOLLOW_ENABLED is False)
    pull_quotes: bool
    reasons: tuple
def decide(*, composite_band, classification, pull_quotes): ...   # -> DetectorDecision
```

### `polybot/detectors/toxicity.py`
```python
@dataclass(frozen=True)
class Toxicity:
    ratio: Decimal
    z: float
    toxic: bool
    subscore: float
    pull_quotes: bool
def toxicity(buy_size, sell_size, *, baseline_mean, baseline_std, config): ...
# raises ValueError if buy_size < 0 or sell_size < 0
```

### `polybot/detectors/pnl.py` and `luck.py` (supporting detector fixtures)
```python
# pnl.py
@dataclass(frozen=True)
class CashFlow:
    kind: str          # BUY|SELL|SPLIT|MERGE|REDEEM|REWARD
    condition_id: str
    usd: Decimal
def realized_pnl(cash_flows, market_value=None); def pnl_by_condition(cash_flows, market_value=None)

# luck.py
@dataclass(frozen=True)
class ResolvedBet:
    entry_price: Decimal
    outcome: int  # 1|0
@dataclass(frozen=True)
class WalletEdge:
    n: int; mean_edge: float; win_z: float; edge_ci_low: float; max_share: float; passes: bool
def assess(bets, config): ...   # -> WalletEdge

# signals.py (D2-D6 + clamp01)
def clamp01(x)  # NaN -> 0.0
def d2_conviction(size, wallet_value, entry_price, recency)
def d3_abnormal_move(move_strength, *, catalyst_present)
def d4_coordinated_entry(cluster_entries, total_entries)
def d5_lead_time(*, trade_ts, public_ts, horizon)
def d6_smart_money(*, edge_weight, conviction)
```

### `polybot/ingestion/news.py` — `Source`
```python
class Source:                                    # NOT a dataclass — a plain class
    def __init__(self, name, url, tier, kind="rss"):  # raises ValueError if tier not in (PRIMARY, DISCOVERY)
        self.name, self.url, self.tier, self.kind = ...
PRIMARY = "PRIMARY"; DISCOVERY = "DISCOVERY"
# Also exported: parse_feed(xml_text), NewsPoller(fetch, stamper, store, allowlist, *, sanitizer=sanitize),
#   Calendar(events=()), CalendarScheduler(calendar, on_due, *, horizon, clock, sleep=asyncio.sleep, poll_interval=30.0)
# NewsPoller.poll_source(name)/poll_all() are async (drive with asyncio.run).
```

### `polybot/ingestion/allowlist.py` — `DEFAULT_ALLOWLIST` (verbatim entries)
```python
DEFAULT_ALLOWLIST = (
    Source("fed-press",       "https://www.federalreserve.gov/feeds/press_all.xml",      PRIMARY),
    Source("fed-monetary",    "https://www.federalreserve.gov/feeds/press_monetary.xml", PRIMARY),
    Source("sec-press",       "https://www.sec.gov/news/pressreleases.rss",              PRIMARY),
    Source("cftc-press",      "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",                PRIMARY),
    Source("bea-news",        "https://apps.bea.gov/rss/rss.xml",                        PRIMARY),
    Source("google-news-top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",  DISCOVERY),
)
```

### `polybot/ingestion/orderbook.py` — `LocalBook`
```python
class LocalBook:
    def __init__(self): ...                          # starts stale (no snapshot baseline)
    def apply_book(self, message): ...               # {"bids":[{"price","size"}], "asks":[...]}; clears stale
    def mark_stale(self); def is_stale(self)
    def apply_price_change(self, changes); def verify_top_of_book(self, best_bid, best_ask)
    def best_bid(self); def best_ask(self)           # Decimal | None
    def midpoint(self)                               # Decimal | None (None if stale/empty/crossed)
    def top_of_book(self)                            # (bid_price, bid_size, ask_price, ask_size), None on empty side
    def size_at(self, side, price)                   # Decimal (0 if absent); side in buy/bid/sell/ask
```

### `polybot/storage/market_memory.py` — `EventStore`
```python
class EventStore:
    def __init__(self, path, *, check_same_thread=True): ...   # ctx-manager; WAL+NORMAL
    def append(self, envelope): ...                  # envelope = core.models.Envelope
    def all(self): ...                               # -> list[Envelope], ORDER BY observed_at, rowid
    def replay_until(self, observed_at_cutoff): ...  # -> list[Envelope], observed_at <= cutoff
    def close(self)
# UNIQUE(source, event_id) -> INSERT OR IGNORE dedups.
```

### `polybot/core/clock.py` — `MonotonicStamper`
```python
class MonotonicStamper:
    def __init__(self, clock=None): ...   # clock defaults to time.monotonic_ns; inject lambda for determinism
    def stamp(self): ...                  # strictly-increasing int; thread-locked
```

### `polybot/core/models.py` — `Envelope` (the canonical envelope; built via `make_envelope`)
```python
@dataclass(frozen=True)
class Envelope:
    source: str
    source_tier: str
    event_id: str
    observed_at: int
    content: str
    published_at: int | None = None
    entities: tuple[str, ...] = ()
    market_links: tuple[str, ...] = ()
    trust: str = "UNTRUSTED"

# polybot/ingestion/envelope.py
def make_envelope(stamper, *, source, source_tier, event_id, content,
                  published_at=None, entities=(), market_links=()): ...  # stamps observed_at, trust defaults UNTRUSTED
```

---

**Key gotchas for plan agents:**
- All money/probability values: `Decimal` from **strings**; never float. Detector statistical scores: plain float.
- `Decision` is constructed **positionally** `(verdict, stake_usd, price_exec, reason)`.
- Async ingestion tests: `asyncio.run(...)` in a sync test, no pytest-asyncio.
- SQLite stores (`EventStore`, `IntentStore`, `ForecastLedger`) are context managers — use `with ... as x:` and `str(tmp_path / "x.db")`.
- `RiskCaps()`, `CalibrationConfig()`, `DetectorConfig()`, `PriorEngine()` all construct with valid defaults; overriding a field re-runs `_verify()` which raises `ValueError` — use `pytest.raises(ValueError, match=...)` to test the guards.
- `process_pending` injects `book_for` (a `token_id -> LocalBook | None` callable), `cluster_model` (None = fail-closed cold), and `breaker` (None = skip L7). `_to_trade_intent` aliases `resolution_source = condition_id` and `cluster_id = event_id` (slice placeholders) — S6 wiring must account for these aliases.
- No `conftest.py` / shared fixtures: define local `_helper()` builders per test file (the established pattern).

---

### Task 1: publisher_group source-independence extension

Adds a `publisher_group: str` to `Source` so the S6 truth-gate can measure *source independence* (two citations are independent iff they have distinct `publisher_group`s). When `publisher_group` is left empty (`""`) it is auto-derived from the registrable domain of the source `url` in `__post_init__`-style init logic, so both `federalreserve.gov` feeds (`fed-press`, `fed-monetary`) collapse to ONE group and are correctly treated as NOT independent. `tldextract` is **not** a dependency (`pyproject.toml` deps are only `httpx`, `websockets`), so the derivation uses the standard library plus a small known-multi-label-suffix set — no new dependency.

`Source` is a plain class (NOT a dataclass), constructed positionally as `Source(name, url, tier, kind="rss")`. The new param is keyword-only with a default of `""` so every existing positional construction (`Source("fed-press", "...", PRIMARY)`, the entire `DEFAULT_ALLOWLIST`, `_FED` in `tests/test_news.py`) keeps working unchanged.

**Files:**
- Modify: `src/polybot/ingestion/news.py`  (the `Source` class, lines 33-42; add a `_registrable_domain` module helper near the other module helpers, e.g. after `_local` at line 45-46)
- Modify: `src/polybot/ingestion/allowlist.py`  (the `DEFAULT_ALLOWLIST` tuple, lines 28-38 — give the two `federalreserve.gov` feeds an explicit shared group so the regression invariant is pinned even if the derivation logic later changes)
- Test: `tests/test_news.py`  (add cases to the existing file — convention is one test file per source module, and `test_news.py` already owns `Source`)

---

- [ ] **Step 1: Write the failing test — domain derivation collapses two same-host feeds**

Add to `tests/test_news.py` (uses the already-imported `Source`, `PRIMARY` at the top of that file):

```python
def test_publisher_group_derives_registrable_domain_from_url():
    """An empty publisher_group is auto-derived from the URL's registrable domain,
    so two feeds on the SAME host share one group (independence collapses)."""
    a = Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", PRIMARY)
    b = Source("fed-monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml", PRIMARY)
    assert a.publisher_group == "federalreserve.gov"
    assert b.publisher_group == "federalreserve.gov"
    assert a.publisher_group == b.publisher_group
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_publisher_group_derives_registrable_domain_from_url -v`
  - Expected: FAIL with `AttributeError: 'Source' object has no attribute 'publisher_group'`

- [ ] **Step 3: Write minimal implementation**

In `src/polybot/ingestion/news.py`, add the registrable-domain helper after `_local` (it already lives near line 45). Then extend `Source.__init__` to accept and derive `publisher_group`.

Add the helper (place it right after the `_local` function, before `parse_feed`):

```python
# A small set of multi-label public suffixes (no tldextract dependency: pyproject
# pins only httpx + websockets). Covers the common ccTLD second levels so a UK/AU/etc.
# host resolves to its registrable domain rather than the bare suffix. Single-label
# TLDs (.gov, .com, .org, ...) fall through to the simple "last two labels" rule, which
# is exactly what collapses both federalreserve.gov feeds into one publisher_group.
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au", "gov.au",
    "co.jp", "or.jp", "go.jp",
    "co.nz", "govt.nz",
    "com.br", "gov.br",
    "co.in", "gov.in",
    "com.cn", "gov.cn",
})


def _registrable_domain(url):
    """Best-effort registrable domain (eTLD+1) of a URL, lowercased, no port/userinfo.

    Dependency-free: handles common multi-label ccTLD suffixes explicitly, otherwise
    takes the last two labels. Returns "" if no host can be parsed (the caller treats
    an empty publisher_group as 'derive failed' is impossible here -- it is the input
    sentinel -- so a missing host yields "", which the truth-gate counts as its own
    singleton group)."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in _MULTI_LABEL_SUFFIXES:
        return last_three
    return last_two
```

Replace the `Source` class `__init__` (currently lines 36-42):

```python
class Source:
    """An allowlisted news feed: a stable name, a URL, and a trust tier.

    ``publisher_group`` is the source-INDEPENDENCE key the S6 truth-gate uses: two
    citations are independent iff their publisher_groups differ. Left empty it is
    auto-derived from the registrable domain of ``url`` -- so two feeds on the same
    host (e.g. both federalreserve.gov feeds) collapse to ONE group and are correctly
    NOT counted as two independent corroborating sources. Pass an explicit
    ``publisher_group`` to bind feeds across hosts that share an owner."""

    def __init__(self, name, url, tier, kind="rss", *, publisher_group=""):
        if tier not in (PRIMARY, DISCOVERY):
            raise ValueError(f"unknown news tier: {tier!r}")
        self.name = name
        self.url = url
        self.tier = tier
        self.kind = kind
        self.publisher_group = publisher_group or _registrable_domain(url)
```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_publisher_group_derives_registrable_domain_from_url -v`
  - Expected: PASS

- [ ] **Step 5: Commit**
  - `git add src/polybot/ingestion/news.py tests/test_news.py && git commit -m "feat(ingestion): derive publisher_group from registrable domain (S6/POL-8)"`

---

- [ ] **Step 1: Write the failing test — explicit publisher_group overrides derivation**

Add to `tests/test_news.py`:

```python
def test_publisher_group_explicit_value_overrides_derivation():
    """An explicit non-empty publisher_group is kept verbatim (binds feeds across
    different hosts that share an owner) and is NOT overwritten by URL derivation."""
    s = Source("wire-a", "https://feeds.somewire.example/a.xml", PRIMARY,
               publisher_group="somewire-group")
    assert s.publisher_group == "somewire-group"
    # Cross-host sources can be pinned to one owner group:
    t = Source("wire-b", "https://news.othercdn.example/b.xml", PRIMARY,
               publisher_group="somewire-group")
    assert s.publisher_group == t.publisher_group
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_publisher_group_explicit_value_overrides_derivation -v`
  - Expected: PASS already if Step 3 of the previous cycle is in place (the `publisher_group or _registrable_domain(url)` short-circuit already honours an explicit value). If you are running these cycles strictly RED-first against a clean tree, this asserts the explicit-override branch; it FAILS with `AttributeError` only on a tree where the previous cycle's implementation is absent. On the current tree it should PASS — keep it as a regression pin for the override branch.

- [ ] **Step 3: Write minimal implementation**

No new code required — the `publisher_group or _registrable_domain(url)` expression from the prior cycle already implements the override (a non-empty explicit value is truthy and short-circuits the derivation). This step exists to make the override branch an explicit, named, asserted contract.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_publisher_group_explicit_value_overrides_derivation -v`
  - Expected: PASS

- [ ] **Step 5: Commit**
  - `git add tests/test_news.py && git commit -m "test(ingestion): pin explicit publisher_group override branch (S6/POL-8)"`

---

- [ ] **Step 1: Write the failing test — REGRESSION INVARIANT: fed-press and fed-monetary share a group**

This is the load-bearing safety invariant from the pinned contract. Add to `tests/test_news.py` (import `DEFAULT_ALLOWLIST` at the top of the new test, or add it to the module imports):

```python
def test_default_allowlist_fed_feeds_share_publisher_group():
    """REGRESSION INVARIANT (S6 truth-gate): fed-press and fed-monetary are BOTH
    federalreserve.gov, so they MUST resolve to the same publisher_group and therefore
    NEVER count as two independent corroborating primaries. Two citations to these two
    feeds = corroborated:False."""
    from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST

    by_name = {s.name: s for s in DEFAULT_ALLOWLIST}
    fed_press = by_name["fed-press"]
    fed_monetary = by_name["fed-monetary"]
    assert fed_press.publisher_group == fed_monetary.publisher_group
    assert fed_press.publisher_group == "federalreserve.gov"
    # And distinct-owner primaries stay independent (sanity counter-example):
    assert by_name["sec-press"].publisher_group != fed_press.publisher_group
    assert by_name["sec-press"].publisher_group == "sec.gov"
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_default_allowlist_fed_feeds_share_publisher_group -v`
  - Expected: On the post-Step-3-cycle-1 tree this PASSES via derivation alone (both URLs are `www.federalreserve.gov` -> `federalreserve.gov`). To make the invariant explicit and immune to derivation drift, proceed to Step 3 and pin it in the allowlist; before that edit, the explicit-group assertion path is still satisfied by derivation, so the FAIL you are guarding against is a future regression (someone changes a feed URL or the derivation heuristic). Treat this test as the regression tripwire.

- [ ] **Step 3: Write minimal implementation — pin the shared group explicitly in the allowlist**

Edit `src/polybot/ingestion/allowlist.py` so the two `federalreserve.gov` feeds carry an explicit, shared `publisher_group`. This makes the invariant independent of the derivation heuristic (defense in depth: even if a feed URL moves to a subdomain or a CDN host, the two stay bound). Replace the two `Source("fed-...", ...)` lines (currently lines 30-31):

```python
DEFAULT_ALLOWLIST = (
    # --- PRIMARY: US financial regulators (relevant to crypto / finance markets) ---
    # Both Fed feeds are the SAME publisher (federalreserve.gov): pin a shared
    # publisher_group so the S6 truth-gate NEVER counts them as two independent
    # corroborating primaries, even if a feed URL later moves to a subdomain/CDN.
    Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", PRIMARY,
           publisher_group="federalreserve.gov"),
    Source("fed-monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml", PRIMARY,
           publisher_group="federalreserve.gov"),  # FOMC statements
    Source("sec-press", "https://www.sec.gov/news/pressreleases.rss", PRIMARY),
    Source("cftc-press", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", PRIMARY),
    # --- PRIMARY: macro econ releases (GDP / personal income / PCE) ---
    Source("bea-news", "https://apps.bea.gov/rss/rss.xml", PRIMARY),
    # --- DISCOVERY: aggregator -- NEVER triggers a trade (legal/ToS is the operator's call) ---
    Source("google-news-top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", DISCOVERY),
)
```

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_default_allowlist_fed_feeds_share_publisher_group -v`
  - Expected: PASS (both assert `federalreserve.gov`; `sec-press` derives to `sec.gov` and differs)

- [ ] **Step 5: Commit**
  - `git add src/polybot/ingestion/allowlist.py tests/test_news.py && git commit -m "feat(ingestion): pin shared publisher_group for fed-press/fed-monetary (S6/POL-8)"`

---

- [ ] **Step 1: Write the failing test — every DEFAULT_ALLOWLIST entry still constructs and exposes a non-empty group**

Add to `tests/test_news.py`:

```python
def test_default_allowlist_all_entries_construct_with_a_group():
    """Backward-compat + completeness: every existing allowlist entry still constructs
    (positional Source(...) signature unchanged) and exposes a non-empty publisher_group
    (explicit or derived). bea-news (apps.bea.gov) derives to bea.gov."""
    from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST

    assert len(DEFAULT_ALLOWLIST) == 6
    for s in DEFAULT_ALLOWLIST:
        assert s.publisher_group, f"empty publisher_group for {s.name}"
    by_name = {s.name: s for s in DEFAULT_ALLOWLIST}
    assert by_name["bea-news"].publisher_group == "bea.gov"          # apps.bea.gov -> bea.gov
    assert by_name["cftc-press"].publisher_group == "cftc.gov"
    assert by_name["google-news-top"].publisher_group == "google.com"
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_default_allowlist_all_entries_construct_with_a_group -v`
  - Expected: On a tree without the prior cycles this FAILS with `AttributeError: 'Source' object has no attribute 'publisher_group'`. With the prior cycles applied it PASSES — it is the backward-compat regression pin (subdomain hosts `apps.bea.gov` / `news.google.com` must reduce to the registrable domain).

- [ ] **Step 3: Write minimal implementation**

No new code — covered by `_registrable_domain` from cycle 1 (it strips subdomains by taking the last two labels for single-label TLDs). This step pins the subdomain-reduction behavior for `apps.bea.gov` and `news.google.com`.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_news.py::test_default_allowlist_all_entries_construct_with_a_group -v`
  - Expected: PASS

- [ ] **Step 5: Commit**
  - `git add tests/test_news.py && git commit -m "test(ingestion): pin publisher_group derivation for all allowlist entries (S6/POL-8)"`

---

- [ ] **Step 1: Write the failing test — full regression: existing news suite + the 377 baseline stay green**

No new test code — this is the guardrail run that proves the additive change broke nothing (the `Source` signature change is keyword-only with a default, so `_FED = Source("fed-press", "https://primary.example/fed.xml", PRIMARY)` and every poller test must still pass).

- [ ] **Step 2: Run the existing suite to confirm no regression**
  - Run: `./.venv/bin/pytest tests/test_news.py -v`
  - Run: `./.venv/bin/pytest`   (full suite — expect the existing 377+ to remain green)
  - Expected: PASS, no failures, no new errors. (`_FED` and all positional `Source(...)` constructions are unaffected because `publisher_group` is keyword-only with a default of `""`.)

- [ ] **Step 3: (No implementation)** — if any existing test fails here, the regression is in the `Source.__init__` signature; verify `publisher_group` is keyword-only (`*, publisher_group=""`) and that no positional caller is shifted. Do not change call sites.

- [ ] **Step 4: Re-run to confirm green**
  - Run: `./.venv/bin/pytest`
  - Expected: PASS (full suite green, count >= prior 377 + the 4 new tests above)

- [ ] **Step 5: Commit** (only if a fixup was needed in Step 3; otherwise skip — the prior commits already capture the work)
  - `git add -A && git commit -m "test(ingestion): confirm publisher_group change is non-breaking across suite (S6/POL-8)"`

---

### Task 2: FusionEngine (weighted log-odds fold)

The §4.1 weighted-log-odds signal fold with the market mid as prior. Hermes's `p` enters only as `p_news`, hard-capped at `w_news <= 0.25` and gated on corroboration; the Bot folds it with `p_base` / `p_micro` / `p_flow` against `logit(mid)` and re-quantizes to a `Decimal` posterior. `recalibrate()` is a typed identity stub behind a seam (the deferred adaptive slice replaces it). Money/probability values are `Decimal` from strings; the only `float` lives inside the logit/sigmoid fold, re-quantized to a 6dp `Decimal` at the boundary — the exact pattern already used in `calibration/anchor.py` and `ers/comove.py`.

**Files:**
- Create: `src/polybot/fusion/__init__.py` (new package marker — `src/polybot/fusion/` does not exist yet)
- Create: `src/polybot/fusion/engine.py`
- Test: `tests/test_fusion_engine.py`

> Convention notes (from the repo scout): flat `tests/` dir, one file per module named `test_<area>_<module>.py`; `from decimal import Decimal` at the top; all probabilities are `Decimal` built from **strings**; import the concrete symbols (`from polybot.fusion.engine import ...`); plain `assert`; `pytest.raises(ValueError, match=...)` for the fail-loud construction guards. `pyproject` sets `pythonpath=["src"]`, so imports are `from polybot...` with no install step. The repo runs `./.venv/bin/pytest <path>::<test> -v`.

---

#### Cycle A — `FusionConfig` fails loud on `w_news > 0.25`

- [ ] **Step A1: Write the failing test**

```python
# tests/test_fusion_engine.py
"""S6 / POL-8 -- FusionEngine: the weighted log-odds fold (market mid as prior).

Safety properties under test:
  * FusionConfig is consistency-checked at construction and FAILS LOUD (ValueError) on a
    spec-cap violation (w_news > 0.25), a negative weight, or a non-positive clip bound.
  * corroboration is the single key that lets w_news contribute (w_news_effective flips
    0.20 <-> 0.0); an uncorroborated proposal reduces to mid nudged toward the base-rate prior.
  * a confident-wrong p_news cannot run away: its log-odds delta is clipped to +/- clip_logodds.
  * all-mid inputs leave the posterior at the mid (no spurious nudge).
  * a degenerate mid (<=0 or >=1) raises FusionError; an out-of-(0,1) signal contributes 0 delta
    (fail-closed, not a crash).
  * recalibrate() is a typed identity stub (the deferred adaptive slice replaces it).
  * components carries the raw Decimal inputs for the ComponentLog.
All probabilities are Decimal from strings; only the internal logit/sigmoid fold is float.
"""

from decimal import Decimal

import pytest

from polybot.fusion.engine import (
    FusionConfig,
    FusionError,
    FusionResult,
    fuse,
    recalibrate,
)


# Bootstrap config used across the plan + the e2e test (see DESIGN-S6 §0 fork 1b).
def _cfg(**overrides):
    base = dict(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)
    base.update(overrides)
    return FusionConfig(**base)


def test_config_rejects_w_news_above_cap():
    # The spec cap: Hermes's signal can never earn more than 0.25 weight.
    with pytest.raises(ValueError, match="w_news"):
        FusionConfig(w_news=0.26, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)
```

- [ ] **Step A2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_w_news_above_cap -v`
  - Expected: FAIL — `ModuleNotFoundError: No module named 'polybot.fusion'` (the package and module do not exist yet).

- [ ] **Step A3: Write minimal implementation** (create the package marker + the module with just enough to make the guard real)

```python
# src/polybot/fusion/__init__.py
"""Signal fusion (S6 / POL-8): the weighted log-odds fold + the per-signal component log."""
```

```python
# src/polybot/fusion/engine.py
"""FusionEngine (S6 / POL-8) -- the §4.1 weighted log-odds fold with the market mid as prior.

Hermes's posterior enters ONLY as ``p_news``, hard-capped at ``w_news <= 0.25`` and gated on
corroboration (>=2 independent allowlisted primaries, verified by the truth-gate). The Bot folds
it with ``p_base`` (base-rate prior), ``p_micro`` and ``p_flow`` (0-weight in v1, logged) against
``logit(mid)``:

    L = logit(mid) + w_news_eff*clip(logit(p_news)-logit(mid))
                   + w_base   *clip(logit(p_base)-logit(mid))
                   + w_micro  *clip(logit(p_micro)-logit(mid))
                   + w_flow   *clip(logit(p_flow)-logit(mid))
    w_news_eff = w_news if corroborated else 0.0
    p_final    = recalibrate(sigmoid(L))

Each per-signal delta is clipped to +/- ``clip_logodds`` so a confident-wrong signal cannot run
away. ``recalibrate`` is a typed IDENTITY stub behind a seam (the deferred adaptive isotonic
recalibrator replaces it). Fail-closed: any ``p_i`` not strictly in (0,1) contributes a 0 delta
(no nudge); a degenerate ``mid`` (<=0 or >=1) raises ``FusionError`` (the caller already guards
``midpoint() is None`` upstream).

Probabilities are Decimal at the boundary; the logit/sigmoid math is float internally (the one
log/exp boundary), re-quantized to a 6dp Decimal -- the same pattern as calibration/anchor.py and
ers/comove.py.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

_QUANT = Decimal("0.000001")  # 6dp, matching anchor.py / comove.py
_EPS = 1e-9                   # internal logit clamp so sigmoid/logit never see 0 or 1


class FusionError(Exception):
    """Raised when fusion cannot proceed (a degenerate mid the caller failed to guard)."""


@dataclass(frozen=True)
class FusionConfig:
    """Fixed bootstrap weights; consistency-checked at construction, fails LOUD.

    HARD invariants (DESIGN-S6 §4.1):
      * 0.0 <= w_news <= 0.25      -- the spec cap on Hermes's signal
      * w_base, w_micro, w_flow >= 0.0
      * clip_logodds > 0.0          -- a non-positive clamp would erase every signal delta
    """

    w_news: float
    w_base: float
    w_micro: float
    w_flow: float
    clip_logodds: float

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if not (0.0 <= self.w_news <= 0.25):
            raise ValueError(f"w_news must be in [0.0, 0.25] (the spec cap), got {self.w_news}")
        for name in ("w_base", "w_micro", "w_flow"):
            value = getattr(self, name)
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0.0, got {value}")
        if not (self.clip_logodds > 0.0):
            raise ValueError(f"clip_logodds must be > 0.0, got {self.clip_logodds}")
```

- [ ] **Step A4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_w_news_above_cap -v`
  - Expected: PASS.

- [ ] **Step A5: Commit**

```bash
git add src/polybot/fusion/__init__.py src/polybot/fusion/engine.py tests/test_fusion_engine.py
git commit -m "feat(fusion): FusionConfig w_news<=0.25 spec-cap guard (S6/POL-8)"
```

---

#### Cycle B — `FusionConfig` fails loud on a negative weight

- [ ] **Step B1: Write the failing test** (append to `tests/test_fusion_engine.py`)

```python
def test_config_rejects_negative_weight():
    # A negative w_base would invert the prior pull -- nonsense; must fail loud.
    for field in ("w_base", "w_micro", "w_flow"):
        with pytest.raises(ValueError, match=field):
            _cfg(**{field: -0.01})
```

- [ ] **Step B2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_negative_weight -v`
  - Expected: PASS already (the `_verify()` negative-weight loop written in A3 covers it). If it instead errors on import or fails, fix `_verify()` before continuing. This RED is satisfied-by-construction from Cycle A; treat a clean PASS as the GREEN and proceed.

- [ ] **Step B3: Implementation** — already present from Step A3 (`_verify()` loops `w_base`/`w_micro`/`w_flow >= 0.0`). No change.

- [ ] **Step B4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_negative_weight -v`
  - Expected: PASS.

- [ ] **Step B5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): FusionConfig rejects negative w_base/w_micro/w_flow (S6/POL-8)"
```

---

#### Cycle C — `FusionConfig` fails loud on `clip_logodds <= 0`

- [ ] **Step C1: Write the failing test** (append)

```python
def test_config_rejects_nonpositive_clip():
    with pytest.raises(ValueError, match="clip_logodds"):
        _cfg(clip_logodds=0.0)
    with pytest.raises(ValueError, match="clip_logodds"):
        _cfg(clip_logodds=-1.0)
```

- [ ] **Step C2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_nonpositive_clip -v`
  - Expected: PASS already (the `clip_logodds > 0.0` guard from A3 covers it). Same satisfied-by-construction note as Cycle B — confirm the clean PASS, then proceed.

- [ ] **Step C3: Implementation** — already present from Step A3. No change.

- [ ] **Step C4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_config_rejects_nonpositive_clip -v`
  - Expected: PASS.

- [ ] **Step C5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): FusionConfig rejects non-positive clip_logodds (S6/POL-8)"
```

---

#### Cycle D — `recalibrate` is the identity stub

- [ ] **Step D1: Write the failing test** (append)

```python
def test_recalibrate_is_identity_stub():
    # The deferred adaptive slice replaces this; v1 is a typed Decimal-in/Decimal-out no-op.
    for x in ("0.01", "0.5", "0.73", "0.999"):
        assert recalibrate(Decimal(x)) == Decimal(x), x
    assert isinstance(recalibrate(Decimal("0.5")), Decimal)
```

- [ ] **Step D2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_recalibrate_is_identity_stub -v`
  - Expected: FAIL — `ImportError: cannot import name 'recalibrate'` (not defined yet; the import at the top of the test file fails for the whole module).

- [ ] **Step D3: Write minimal implementation** (append to `src/polybot/fusion/engine.py`, after `FusionConfig`)

```python
def recalibrate(x: Decimal) -> Decimal:
    """IDENTITY stub behind a seam. The deferred adaptive slice (isotonic recalibrator, needs a
    warm ForecastLedger) replaces this; v1 returns p_final unchanged. Documented as identity so a
    future swap is a one-function change and the fold stays untouched."""
    return x
```

- [ ] **Step D4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_recalibrate_is_identity_stub -v`
  - Expected: PASS.

- [ ] **Step D5: Commit**

```bash
git add src/polybot/fusion/engine.py tests/test_fusion_engine.py
git commit -m "feat(fusion): recalibrate() identity stub behind seam (S6/POL-8)"
```

---

#### Cycle E — `fuse` with all-mid inputs leaves the posterior at the mid

- [ ] **Step E1: Write the failing test** (append)

```python
def test_fuse_all_mid_inputs_returns_mid():
    # Every signal == mid -> every delta is 0 -> L == logit(mid) -> p_final ~= mid.
    mid = Decimal("0.40")
    r = fuse(mid, p_news=mid, p_base=mid, p_micro=mid, p_flow=mid,
             corroborated=True, config=_cfg())
    assert isinstance(r, FusionResult)
    assert isinstance(r.p_final, Decimal)
    # 6dp re-quantization round-trip: identical to the mid within one quantum.
    assert abs(r.p_final - mid) <= Decimal("0.000001"), r.p_final
```

- [ ] **Step E2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_fuse_all_mid_inputs_returns_mid -v`
  - Expected: FAIL — `ImportError: cannot import name 'fuse'` / `FusionResult` (neither defined yet).

- [ ] **Step E3: Write minimal implementation** (append to `src/polybot/fusion/engine.py`)

```python
@dataclass(frozen=True)
class FusionResult:
    p_final: Decimal
    components: Mapping[str, Decimal]   # raw {p_news,p_base,p_micro,p_flow} for the ComponentLog
    w_news_effective: float


def _logit(x: float) -> float:
    x = min(max(x, _EPS), 1.0 - _EPS)
    return math.log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _clip(delta: float, bound: float) -> float:
    return min(max(delta, -bound), bound)


def _to_decimal(x: float) -> Decimal:
    return Decimal(str(x)).quantize(_QUANT)


def _in_unit(p: Decimal) -> bool:
    """A signal probability is usable iff it is finite and strictly inside (0, 1)."""
    return p.is_finite() and Decimal(0) < p < Decimal(1)


def fuse(mid: Decimal, *, p_news: Decimal, p_base: Decimal, p_micro: Decimal,
         p_flow: Decimal, corroborated: bool, config: FusionConfig) -> FusionResult:
    """Fold the four signals around ``logit(mid)`` and return the recalibrated posterior.

    Fail-closed: a degenerate ``mid`` (not finite, or not strictly in (0,1)) raises
    ``FusionError`` (the caller guards ``midpoint() is None`` upstream). Any individual signal
    ``p_i`` outside (0,1) contributes a 0 delta -- it cannot crash or nudge. ``w_news`` is applied
    only when ``corroborated`` (the corroboration key); otherwise its effective weight is 0.
    """
    if not (mid.is_finite() and Decimal(0) < mid < Decimal(1)):
        raise FusionError(f"degenerate mid: {mid}")

    mid_logit = _logit(float(mid))
    bound = config.clip_logodds
    w_news_eff = config.w_news if corroborated else 0.0

    L = mid_logit
    for weight, p in (
        (w_news_eff, p_news),
        (config.w_base, p_base),
        (config.w_micro, p_micro),
        (config.w_flow, p_flow),
    ):
        if weight == 0.0 or not _in_unit(p):
            continue  # fail-closed: no weight or a degenerate signal -> 0 delta (no nudge)
        delta = _clip(_logit(float(p)) - mid_logit, bound)
        L += weight * delta

    p_final = recalibrate(_to_decimal(_sigmoid(L)))
    components = {
        "p_news": p_news,
        "p_base": p_base,
        "p_micro": p_micro,
        "p_flow": p_flow,
    }
    return FusionResult(p_final=p_final, components=components, w_news_effective=w_news_eff)
```

- [ ] **Step E4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_fuse_all_mid_inputs_returns_mid -v`
  - Expected: PASS.

- [ ] **Step E5: Commit**

```bash
git add src/polybot/fusion/engine.py tests/test_fusion_engine.py
git commit -m "feat(fusion): fuse() weighted log-odds fold + FusionResult (S6/POL-8)"
```

---

#### Cycle F — corroboration flips `w_news_effective` (0.20 vs 0.0)

- [ ] **Step F1: Write the failing test** (append)

```python
def test_corroboration_flips_w_news_effective():
    mid = Decimal("0.50")
    p_news = Decimal("0.80")  # bullish Hermes signal
    common = dict(p_news=p_news, p_base=mid, p_micro=mid, p_flow=mid, config=_cfg())

    corr = fuse(mid, corroborated=True, **common)
    uncorr = fuse(mid, corroborated=False, **common)

    # The corroboration key: w_news contributes only when corroborated.
    assert corr.w_news_effective == 0.20
    assert uncorr.w_news_effective == 0.0

    # Uncorroborated -> p_news earns 0 weight -> with p_base=p_micro=p_flow=mid the posterior
    # collapses to the mid (informational-only, exactly the DESIGN-S6 §0 fork-1b contract).
    assert abs(uncorr.p_final - mid) <= Decimal("0.000001"), uncorr.p_final
    # Corroborated -> the bullish signal pulls the posterior strictly above the mid.
    assert corr.p_final > mid, corr.p_final
```

- [ ] **Step F2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_corroboration_flips_w_news_effective -v`
  - Expected: PASS already if Cycle E's `fuse` is correct (`w_news_eff` gate + the unit-collapse). This is the load-bearing corroboration assertion — run it explicitly to confirm GREEN before committing. If it FAILs, the bug is in the `w_news_eff` branch of `fuse`; fix `fuse`, do not weaken the test.

- [ ] **Step F3: Implementation** — covered by Cycle E's `fuse`. No change unless F2 surfaced a bug.

- [ ] **Step F4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_corroboration_flips_w_news_effective -v`
  - Expected: PASS.

- [ ] **Step F5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): corroboration flips w_news_effective 0.20<->0.0 (S6/POL-8)"
```

---

#### Cycle G — a huge `p_news` delta is clipped (bounded move)

- [ ] **Step G1: Write the failing test** (append)

```python
def test_huge_p_news_delta_is_clipped():
    # A confident-wrong p_news near 1 cannot run away: its log-odds delta is clamped to
    # +/- clip_logodds, so the contribution is bounded regardless of how extreme p_news is.
    mid = Decimal("0.50")
    cfg = _cfg(clip_logodds=2.0)
    common = dict(p_base=mid, p_micro=mid, p_flow=mid, corroborated=True, config=cfg)

    extreme = fuse(mid, p_news=Decimal("0.999999"), **common)
    very_extreme = fuse(mid, p_news=Decimal("0.99999999"), **common)

    # Both clip to the SAME bounded L = logit(0.5)=0 + 0.20*clip(2.0) = 0.40 -> sigmoid(0.40).
    import math as _m
    expected = Decimal(str(1.0 / (1.0 + _m.exp(-0.20 * 2.0)))).quantize(Decimal("0.000001"))
    assert extreme.p_final == expected, extreme.p_final
    assert very_extreme.p_final == expected, very_extreme.p_final
    # Bounded: nowhere near p_news -- the clamp held.
    assert extreme.p_final < Decimal("0.60"), extreme.p_final
```

- [ ] **Step G2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_huge_p_news_delta_is_clipped -v`
  - Expected: PASS if Cycle E's `_clip` is wired correctly. This is a safety-critical assertion (prompt-injected catalyst cannot over-move a thin market); run it explicitly. If FAIL, the `_clip` call in `fuse` is wrong — fix `fuse`.

- [ ] **Step G3: Implementation** — covered by Cycle E's `_clip`. No change unless G2 surfaced a bug.

- [ ] **Step G4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_huge_p_news_delta_is_clipped -v`
  - Expected: PASS.

- [ ] **Step G5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): clip bounds a confident-wrong p_news delta (S6/POL-8)"
```

---

#### Cycle H — `p_base` pulls the posterior toward the prior

- [ ] **Step H1: Write the failing test** (append)

```python
def test_p_base_pulls_toward_prior():
    # With p_news held at the mid (no news pull), a base-rate prior below the mid drags the
    # posterior strictly below the mid -- and a prior above drags it above. The prior is the
    # only mover here, so direction is unambiguous.
    mid = Decimal("0.50")
    common = dict(p_micro=mid, p_flow=mid, corroborated=True, config=_cfg())

    low = fuse(mid, p_news=mid, p_base=Decimal("0.20"), **common)
    high = fuse(mid, p_news=mid, p_base=Decimal("0.80"), **common)

    assert low.p_final < mid, low.p_final
    assert high.p_final > mid, high.p_final
    # Symmetric around the mid in log-odds (p_base symmetric, equal weight).
    assert abs((mid - low.p_final) - (high.p_final - mid)) <= Decimal("0.000002")
```

- [ ] **Step H2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_p_base_pulls_toward_prior -v`
  - Expected: PASS if Cycle E's `w_base` term is wired. Run explicitly to confirm the prior-pull direction and the log-odds symmetry. If FAIL, the `config.w_base` term in `fuse` is wrong.

- [ ] **Step H3: Implementation** — covered by Cycle E's `w_base` term. No change unless H2 surfaced a bug.

- [ ] **Step H4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_p_base_pulls_toward_prior -v`
  - Expected: PASS.

- [ ] **Step H5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): p_base pulls posterior toward the prior (S6/POL-8)"
```

---

#### Cycle I — a degenerate `mid` raises `FusionError`

- [ ] **Step I1: Write the failing test** (append)

```python
def test_degenerate_mid_raises_fusion_error():
    cfg = _cfg()
    common = dict(p_news=Decimal("0.6"), p_base=Decimal("0.5"),
                  p_micro=Decimal("0.5"), p_flow=Decimal("0.5"),
                  corroborated=True, config=cfg)
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.1"), Decimal("1.5")):
        with pytest.raises(FusionError, match="degenerate mid"):
            fuse(bad, **common)
```

- [ ] **Step I2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_degenerate_mid_raises_fusion_error -v`
  - Expected: PASS if Cycle E's degenerate-mid guard is present (it is). Run explicitly — this is the fail-closed boundary the upstream `midpoint() is None -> REJECT book_stale` relies on. If FAIL, the guard in `fuse` is missing/wrong.

- [ ] **Step I3: Implementation** — covered by Cycle E's `FusionError` guard. No change unless I2 surfaced a bug.

- [ ] **Step I4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_degenerate_mid_raises_fusion_error -v`
  - Expected: PASS.

- [ ] **Step I5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): degenerate mid raises FusionError (S6/POL-8)"
```

---

#### Cycle J — an out-of-(0,1) signal contributes 0 delta (fail-closed, no crash)

- [ ] **Step J1: Write the failing test** (append)

```python
def test_out_of_unit_signal_contributes_zero_delta():
    # A degenerate p_news (0, 1, or out of range) must NOT crash and must NOT nudge -- it is
    # dropped to a 0 delta. With every other signal at the mid, the posterior stays at the mid.
    mid = Decimal("0.50")
    common = dict(p_base=mid, p_micro=mid, p_flow=mid, corroborated=True, config=_cfg())
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.2"), Decimal("1.4")):
        r = fuse(mid, p_news=bad, **common)
        assert abs(r.p_final - mid) <= Decimal("0.000001"), (bad, r.p_final)
        # The raw (even degenerate) value is still recorded for the ComponentLog audit.
        assert r.components["p_news"] == bad, bad
```

- [ ] **Step J2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_out_of_unit_signal_contributes_zero_delta -v`
  - Expected: PASS if Cycle E's `_in_unit` skip is wired. Run explicitly — confirms a degenerate signal is dropped (not crashed) yet still recorded raw for audit. If FAIL, the `_in_unit(p)` guard in the `fuse` loop is wrong.

- [ ] **Step J3: Implementation** — covered by Cycle E's `_in_unit` check. No change unless J2 surfaced a bug.

- [ ] **Step J4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_out_of_unit_signal_contributes_zero_delta -v`
  - Expected: PASS.

- [ ] **Step J5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): out-of-(0,1) signal contributes 0 delta, no crash (S6/POL-8)"
```

---

#### Cycle K — `components` dict carries the raw inputs

- [ ] **Step K1: Write the failing test** (append)

```python
def test_components_returns_raw_decimal_inputs():
    # The ComponentLog (§4.6) needs the raw per-signal Decimals to preserve the un-backfillable
    # substrate the deferred per-signal calibration grades. fuse() must surface exactly the four.
    mid = Decimal("0.50")
    r = fuse(mid, p_news=Decimal("0.7"), p_base=Decimal("0.4"),
             p_micro=Decimal("0.55"), p_flow=Decimal("0.45"),
             corroborated=True, config=_cfg())
    assert set(r.components) == {"p_news", "p_base", "p_micro", "p_flow"}
    assert r.components["p_news"] == Decimal("0.7")
    assert r.components["p_base"] == Decimal("0.4")
    assert r.components["p_micro"] == Decimal("0.55")
    assert r.components["p_flow"] == Decimal("0.45")
    for v in r.components.values():
        assert isinstance(v, Decimal)
```

- [ ] **Step K2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_components_returns_raw_decimal_inputs -v`
  - Expected: PASS if Cycle E's `components` dict is wired. Run explicitly to lock the exact key set and the raw-Decimal contract the ComponentLog depends on. If FAIL, the `components` literal in `fuse` is wrong.

- [ ] **Step K3: Implementation** — covered by Cycle E's `components` dict. No change unless K2 surfaced a bug.

- [ ] **Step K4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py::test_components_returns_raw_decimal_inputs -v`
  - Expected: PASS.

- [ ] **Step K5: Commit**

```bash
git add tests/test_fusion_engine.py
git commit -m "test(fusion): components surfaces raw Decimal inputs for ComponentLog (S6/POL-8)"
```

---

#### Cycle L — full-suite regression (no break to the 377 existing tests)

- [ ] **Step L1: Run the whole new file plus the full suite**
  - Run: `./.venv/bin/pytest tests/test_fusion_engine.py -v`
  - Expected: PASS — all 11 tests green.
  - Run: `./.venv/bin/pytest`
  - Expected: PASS — the 377 existing tests still pass (this task is purely additive: a new package + a new test file, no existing module touched). Final count is 377 + 11 = 388.

- [ ] **Step L2: Commit** (only if anything was adjusted in L1; otherwise skip — the per-cycle commits already cover it)

```bash
git add -A
git commit -m "test(fusion): full FusionEngine suite green, 377 baseline intact (S6/POL-8)"
```

---

### Task 3: ComponentLog sidecar

The per-signal breakdown sidecar (DESIGN §4.6). It records the four raw fusion inputs (`p_news, p_base, p_micro, p_flow`) plus `w_news_effective`, `corroborated`, and the contemporaneous `mid`, keyed by `forecast_id` (= `intent_id`). This preserves the un-backfillable substrate the deferred per-signal calibration slice needs **without** touching POL-7's tested `ForecastLedger` (isolation is the whole point). It is append-only, idempotent on `forecast_id`, shares the ONE process-wide `MonotonicStamper`, and fails LOUD on non-finite / out-of-[0,1] probabilities — exactly like `ForecastLedger.record_forecast` (which it deliberately mirrors but does not import or subclass).

**Why a separate SQLite table and not a column on `forecasts`:** POL-7's `ForecastLedger` is tested and frozen; S6 must keep the 377 tests green. `ComponentLog` is a standalone context-managed store sharing only the stamper, per the pinned contract ("sidecar; does NOT touch calibration/ledger.py").

**Files:**
- Create: `src/polybot/fusion/component_log.py`
- Test: `tests/test_fusion_component_log.py`

> Convention notes for the executing agent: flat `tests/` dir; one test file per source module named `test_<area>_<module>.py`; `from decimal import Decimal` at top, all probabilities built from **strings**; SQLite stores use the `tmp_path` fixture wrapped in `with ... as x:`; plain `assert`; no `conftest.py` — define local `_helper()` builders. The repo runs `./.venv/bin/pytest <path>::<test> -v`; `pyproject` sets `pythonpath=["src"]` so import as `from polybot...`. The venv lives at `.venv/bin/pytest` (built `uv venv --python 3.13`). Run all commands from the repo root in WSL: `/home/jurgenubuntu/projects/polymarket-bot`.

Note: this task assumes Task 1 has already created the `src/polybot/fusion/` package directory and `src/polybot/fusion/__init__.py` (it creates `engine.py` there). If you are executing Task 3 before Task 1, the very first sub-step's directory/`__init__.py` creation handles a fresh package; otherwise it is a harmless no-op (`__init__.py` already present). `component_log.py` does NOT import `engine.py`, so the two are independent.

---

- [ ] **Step 0: Ensure the `fusion` package exists**

  The store module lives in `src/polybot/fusion/`. If Task 1 has not run yet, create the package marker. This is idempotent — skip the write if `__init__.py` already exists.

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  mkdir -p src/polybot/fusion
  [ -f src/polybot/fusion/__init__.py ] || printf '"""Signal fusion (S6 / POL-8): log-odds fold engine + per-signal component log."""\n' > src/polybot/fusion/__init__.py
  ```

---

#### Cycle A — `record` inserts a row and returns True

- [ ] **Step A1: Write the failing test**

  ```python
  # tests/test_fusion_component_log.py
  """ComponentLog sidecar (S6 / POL-8, DESIGN §4.6).

  Append-only, idempotent per-signal breakdown keyed by forecast_id (= intent_id).
  Preserves the un-backfillable substrate the deferred per-signal calibration needs
  WITHOUT modifying POL-7's ForecastLedger. Shares the one MonotonicStamper.

  Safety properties under test:
    * record() returns True on first insert, False on a duplicate forecast_id (idempotent).
    * record() fails LOUD (ValueError) on a non-finite or out-of-[0,1] probability --
      the substrate cannot be backfilled, so garbage must never enter it.
    * all() returns the recorded rows carrying the stamp + every stored field
      (p_news/p_base/p_micro/p_flow as Decimal, w_news_effective float, corroborated bool, mid Decimal).
  """
  from decimal import Decimal

  from polybot.core.clock import MonotonicStamper
  from polybot.fusion.component_log import ComponentLog


  def _log(tmp_path):
      # MonotonicStamper with an injected deterministic clock for a predictable stamp.
      stamper = MonotonicStamper(clock=lambda: 1000)
      return ComponentLog(str(tmp_path / "components.db"), stamper=stamper)


  def test_record_returns_true_on_first_insert(tmp_path):
      with _log(tmp_path) as log:
          inserted = log.record(
              "intent-1",
              p_news=Decimal("0.70"), p_base=Decimal("0.55"),
              p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
              w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
          )
      assert inserted is True
  ```

- [ ] **Step A2: Run test to verify it fails**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_record_returns_true_on_first_insert -v`

  Expected: FAIL — `ModuleNotFoundError: No module named 'polybot.fusion.component_log'` (the module does not exist yet).

- [ ] **Step A3: Write minimal implementation**

  ```python
  # src/polybot/fusion/component_log.py
  """Per-signal component log (S6 / POL-8, DESIGN §4.6).

  Append-only, point-in-time SQLite sidecar of the per-signal fusion breakdown, keyed by
  ``forecast_id`` (= ``intent_id``): the four raw inputs ``{p_news, p_base, p_micro, p_flow}``
  plus ``w_news_effective``, ``corroborated`` and the contemporaneous market ``mid``.

  This is the substrate the DEFERRED adaptive per-signal calibration slice (EMA w_i, auto-zero,
  isotonic recalibrator) needs to grade each signal on S6-era data. Like the Market-Memory
  EventStore and the ForecastLedger it CANNOT be backfilled, so it is written from day one.

  ISOLATION: this is a SIDECAR. It deliberately does NOT import, subclass, or touch POL-7's tested
  ``calibration/ledger.py`` ForecastLedger -- it mirrors that store's idempotent-INSERT + fail-loud
  validation patterns in its own table so the 377 existing tests stay green. It shares ONLY the one
  process-wide MonotonicStamper (the global total-order contract, core/clock.py).
  """

  import sqlite3
  from dataclasses import dataclass
  from decimal import Decimal

  _COLUMNS = ("forecast_id, p_news, p_base, p_micro, p_flow, "
              "w_news_effective, corroborated, mid, recorded_at")


  @dataclass(frozen=True)
  class ComponentRecord:
      forecast_id: str
      p_news: Decimal
      p_base: Decimal
      p_micro: Decimal
      p_flow: Decimal
      w_news_effective: float
      corroborated: bool
      mid: Decimal
      recorded_at: int


  class ComponentLog:
      def __init__(self, path, *, stamper):
          self._stamper = stamper
          self._conn = sqlite3.connect(path)
          self._conn.execute("PRAGMA journal_mode=WAL")
          self._conn.execute("PRAGMA synchronous=NORMAL")
          self._conn.execute(
              """
              CREATE TABLE IF NOT EXISTS components (
                  forecast_id       TEXT PRIMARY KEY,
                  p_news            TEXT    NOT NULL,
                  p_base            TEXT    NOT NULL,
                  p_micro           TEXT    NOT NULL,
                  p_flow            TEXT    NOT NULL,
                  w_news_effective  REAL    NOT NULL,
                  corroborated      INTEGER NOT NULL,
                  mid               TEXT    NOT NULL,
                  recorded_at       INTEGER NOT NULL
              )
              """
          )
          self._conn.commit()

      def record(self, forecast_id, *, p_news, p_base, p_micro, p_flow,
                 w_news_effective, corroborated, mid):
          """Append the per-signal breakdown for ``forecast_id`` (idempotent on it). Returns True
          if newly inserted, False on a duplicate. Numeric probabilities stored as exact strings.

          Fails LOUD on a non-finite or out-of-[0,1] probability/price: the calibration substrate
          cannot be backfilled, so a NaN/Inf component must never enter it."""
          for name, value in (("p_news", p_news), ("p_base", p_base),
                              ("p_micro", p_micro), ("p_flow", p_flow), ("mid", mid)):
              if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                  raise ValueError(f"{name} must be a finite probability in [0, 1], got {value}")
          cur = self._conn.execute(
              "INSERT OR IGNORE INTO components "
              "(forecast_id, p_news, p_base, p_micro, p_flow, "
              " w_news_effective, corroborated, mid, recorded_at) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (forecast_id, str(p_news), str(p_base), str(p_micro), str(p_flow),
               float(w_news_effective), 1 if corroborated else 0, str(mid),
               self._stamper.stamp()),
          )
          self._conn.commit()
          return cur.rowcount > 0

      def all(self):
          return self._query(f"SELECT {_COLUMNS} FROM components ORDER BY rowid")

      def close(self):
          self._conn.close()

      def __enter__(self):
          return self

      def __exit__(self, *exc):
          self.close()
          return False

      def _query(self, sql, params=()):
          return tuple(self._row(r) for r in self._conn.execute(sql, params).fetchall())

      @staticmethod
      def _row(r):
          return ComponentRecord(
              forecast_id=r[0], p_news=Decimal(r[1]), p_base=Decimal(r[2]),
              p_micro=Decimal(r[3]), p_flow=Decimal(r[4]),
              w_news_effective=float(r[5]), corroborated=bool(r[6]),
              mid=Decimal(r[7]), recorded_at=r[8],
          )
  ```

- [ ] **Step A4: Run test to verify it passes**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_record_returns_true_on_first_insert -v`

  Expected: PASS.

- [ ] **Step A5: Commit**

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add src/polybot/fusion/__init__.py src/polybot/fusion/component_log.py tests/test_fusion_component_log.py
  git commit -m "feat(fusion): add ComponentLog sidecar with idempotent record (S6/POL-8)"
  ```

---

#### Cycle B — duplicate `forecast_id` returns False (idempotent)

- [ ] **Step B1: Write the failing test**

  ```python
  # tests/test_fusion_component_log.py  (append)
  def test_record_duplicate_forecast_id_returns_false(tmp_path):
      with _log(tmp_path) as log:
          first = log.record(
              "intent-dup",
              p_news=Decimal("0.70"), p_base=Decimal("0.55"),
              p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
              w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
          )
          # Same forecast_id, DIFFERENT payload: the second call must be a no-op INSERT OR IGNORE.
          second = log.record(
              "intent-dup",
              p_news=Decimal("0.10"), p_base=Decimal("0.10"),
              p_micro=Decimal("0.10"), p_flow=Decimal("0.10"),
              w_news_effective=0.0, corroborated=False, mid=Decimal("0.10"),
          )
          rows = log.all()
      assert first is True and second is False
      # Idempotent: exactly one row survives, and it is the ORIGINAL payload (not overwritten).
      assert len(rows) == 1
      assert rows[0].p_news == Decimal("0.70") and rows[0].corroborated is True
  ```

- [ ] **Step B2: Run test to verify it fails (or passes-by-construction — confirm explicitly)**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_record_duplicate_forecast_id_returns_false -v`

  Expected: PASS — the `INSERT OR IGNORE` + `cur.rowcount > 0` already implements idempotency, and `all()` round-trips the original payload. This cycle is a guard test that locks the idempotent / no-overwrite contract against future regressions. If it FAILS, the implementation diverged from the pinned contract (`INSERT OR IGNORE -> False on dup`) — fix `record` before proceeding.

- [ ] **Step B3: Implementation already present**

  No code change needed — the Cycle A implementation satisfies this contract. (Do not add an `UPDATE` path: the pinned contract is append-only / INSERT OR IGNORE, so a duplicate must NOT overwrite.)

- [ ] **Step B4: Re-run to confirm**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py -v`

  Expected: PASS (both tests).

- [ ] **Step B5: Commit**

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add tests/test_fusion_component_log.py
  git commit -m "test(fusion): lock ComponentLog idempotent no-overwrite contract (S6/POL-8)"
  ```

---

#### Cycle C — non-finite probability is rejected with ValueError

- [ ] **Step C1: Write the failing test**

  ```python
  # tests/test_fusion_component_log.py  (append)
  import pytest


  def test_record_rejects_non_finite_prob(tmp_path):
      # A NaN p_news must never enter the substrate (it cannot be backfilled).
      with _log(tmp_path) as log:
          with pytest.raises(ValueError, match="p_news"):
              log.record(
                  "intent-nan",
                  p_news=Decimal("NaN"), p_base=Decimal("0.55"),
                  p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
                  w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
              )
          # Nothing was written.
          assert log.all() == ()
  ```

- [ ] **Step C2: Run test to verify it passes (guard) — confirm the message and the no-write**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_record_rejects_non_finite_prob -v`

  Expected: PASS — `Decimal("NaN").is_finite()` is False, so the validation loop raises `ValueError` mentioning `p_news` BEFORE any INSERT. This guards the fail-loud substrate-integrity property. If it FAILS, the `.is_finite()` check is missing or mis-ordered — fix before proceeding.

- [ ] **Step C3: Implementation already present**

  No code change needed — the validation loop in Cycle A covers it.

- [ ] **Step C4: (covered by C2)** — skip; C2 already exercised the implementation.

- [ ] **Step C5: Commit**

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add tests/test_fusion_component_log.py
  git commit -m "test(fusion): ComponentLog rejects non-finite component prob (S6/POL-8)"
  ```

---

#### Cycle D — out-of-[0,1] probability is rejected with ValueError

- [ ] **Step D1: Write the failing test**

  ```python
  # tests/test_fusion_component_log.py  (append)
  def test_record_rejects_out_of_range_prob(tmp_path):
      # mid > 1 is rejected; the field name appears in the error (loud + locatable).
      with _log(tmp_path) as log:
          with pytest.raises(ValueError, match="mid"):
              log.record(
                  "intent-oob",
                  p_news=Decimal("0.70"), p_base=Decimal("0.55"),
                  p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
                  w_news_effective=0.20, corroborated=True, mid=Decimal("1.5"),
              )
          assert log.all() == ()

      # And a negative p_flow is rejected too.
      with _log(tmp_path) as log:
          with pytest.raises(ValueError, match="p_flow"):
              log.record(
                  "intent-neg",
                  p_news=Decimal("0.70"), p_base=Decimal("0.55"),
                  p_micro=Decimal("0.50"), p_flow=Decimal("-0.01"),
                  w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
              )
  ```

- [ ] **Step D2: Run test to verify it passes (guard)**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_record_rejects_out_of_range_prob -v`

  Expected: PASS — `1.5` fails `<= Decimal(1)` and `-0.01` fails `Decimal(0) <=`, each raising `ValueError` naming the offending field. Locks the range-bound substrate-integrity property.

- [ ] **Step D3: Implementation already present**

  No code change needed.

- [ ] **Step D4: (covered by D2)** — skip.

- [ ] **Step D5: Commit**

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add tests/test_fusion_component_log.py
  git commit -m "test(fusion): ComponentLog rejects out-of-range component prob (S6/POL-8)"
  ```

---

#### Cycle E — `all()` round-trips every stored field with the stamp

- [ ] **Step E1: Write the failing test**

  ```python
  # tests/test_fusion_component_log.py  (append)
  def test_all_round_trips_every_field_with_stamp(tmp_path):
      # Deterministic clock -> known monotonic stamp (1000 for the first stamp() call).
      stamper = MonotonicStamper(clock=lambda: 1000)
      with ComponentLog(str(tmp_path / "c.db"), stamper=stamper) as log:
          log.record(
              "intent-rt",
              p_news=Decimal("0.71"), p_base=Decimal("0.53"),
              p_micro=Decimal("0.49"), p_flow=Decimal("0.61"),
              w_news_effective=0.20, corroborated=False, mid=Decimal("0.52"),
          )
          rows = log.all()

      assert len(rows) == 1
      rec = rows[0]
      assert rec.forecast_id == "intent-rt"
      # Probabilities preserved EXACTLY as Decimal (string round-trip, no float drift).
      assert rec.p_news == Decimal("0.71") and isinstance(rec.p_news, Decimal)
      assert rec.p_base == Decimal("0.53")
      assert rec.p_micro == Decimal("0.49")
      assert rec.p_flow == Decimal("0.61")
      assert rec.mid == Decimal("0.52") and isinstance(rec.mid, Decimal)
      # w_news_effective is a float; corroborated round-trips as a bool (not 0/1 int).
      assert rec.w_news_effective == 0.20 and isinstance(rec.w_news_effective, float)
      assert rec.corroborated is False
      # Carries the stamper's monotonic stamp.
      assert rec.recorded_at == 1000
  ```

- [ ] **Step E2: Run test to verify it passes (guard)**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py::test_all_round_trips_every_field_with_stamp -v`

  Expected: PASS — `_row` reconstructs `Decimal` from the stored strings, `float()`/`bool()` for the scalar columns, and `recorded_at` equals the deterministic stamp `1000` (the `MonotonicStamper(clock=lambda: 1000)` returns `1000` on its first `stamp()` since `1000 > 0`). Confirms the contract's "stores w_news_effective+corroborated+mid" and "all() returns recorded rows with the stamp".

- [ ] **Step E3: Implementation already present**

  No code change needed — `_row` and the column list in Cycle A cover the full round-trip.

- [ ] **Step E4: (covered by E2)** — skip.

- [ ] **Step E5: Commit**

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add tests/test_fusion_component_log.py
  git commit -m "test(fusion): ComponentLog all() round-trips all fields + stamp (S6/POL-8)"
  ```

---

#### Cycle F — full-file green + 377-suite regression

- [ ] **Step F1: Run the whole new test file**

  Run: `./.venv/bin/pytest tests/test_fusion_component_log.py -v`

  Expected: PASS — all five tests (`test_record_returns_true_on_first_insert`, `test_record_duplicate_forecast_id_returns_false`, `test_record_rejects_non_finite_prob`, `test_record_rejects_out_of_range_prob`, `test_all_round_trips_every_field_with_stamp`).

- [ ] **Step F2: Run the full suite — confirm no regression**

  Run: `./.venv/bin/pytest`

  Expected: PASS — the prior 377 tests still pass plus the 5 new ones (382). The sidecar is purely additive and imports nothing from `calibration/ledger.py`, so existing tests are untouched.

- [ ] **Step F3: Commit (if any test files were touched in F1/F2 — otherwise skip)**

  No code change expected here; this cycle is verification only. If the full suite surfaced an import-time collision (e.g. Task 1's `__init__.py` not present), resolve it and:

  ```bash
  cd /home/jurgenubuntu/projects/polymarket-bot
  git add -A
  git commit -m "test(fusion): verify ComponentLog suite + full regression green (S6/POL-8)"
  ```

---

### Task 4: CitationTruthGate

The ERS-side, post-INSERT verification of a Hermes proposal's citations. **Pure** over `(citations, EventStore envelopes, live LocalBook)` — citation strings are *matched* against the already-sanitized `EventStore`, **never fetched or executed**. It answers two questions the loop needs: (a) is the news evidence corroborated by `>=2` *independent* allowlisted PRIMARY sources (the single key that unlocks `w_news` in fusion and widens the anchor band), and (b) is this the indirect-prompt-injection signature — one fresh source moving `p` while a thin book lets that same source push the mid (`same_source_collusion`).

Independence is `distinct publisher_group` (Task 1's `Source.publisher_group`), so the confirmed `fed-press`/`fed-monetary` same-domain bypass is closed. `DISCOVERY`-tier and non-allowlisted citations never count and never trigger anything.

**Depends on:** Task 1 (`Source.publisher_group` + `DEFAULT_ALLOWLIST` group assignments). Task 1 must be GREEN before Task 4's `fed-press`/`fed-monetary` regression test can pass.

**Operational definitions pinned for this task** (the design §10 asks the plan to nail these down — see Notes for the one prose refinement):
- **Allowlisted** = the envelope's `source` name is a `Source.name` present in the passed `allowlist` collection.
- **Citation → Envelope match** = a citation string equals an envelope's `event_id` (guid) **or** appears in its `entities` tuple (the neutralized provenance link `NewsPoller` stores). No fetch.
- **Fresh** = `now_ns - envelope.observed_at <= config.freshness_window_ns` (the shared `MonotonicStamper` ns clock).
- **Thin book** = the smaller of the two `top_of_book` resting sizes (bid_size, ask_size), priced in USD as `size * price`, is `< config.thin_book_depth_usd`.
- **Mid move on a thin book** = the live book's bid/ask spread `(best_ask - best_bid) >= config.thin_book_move`. (A single pure book snapshot has no prior mid to diff against; a wide spread on a thin top-of-book is the recompute-free, look-ahead-free proxy that the mid was pushed. See Notes.)
- **`same_source_collusion`** fires iff: there is exactly **one** distinct fresh allowlisted-primary `publisher_group` among the matched p-moving citations **AND** the book is thin **AND** shows the mid-move signature. Corroborated evidence (`>=2` distinct fresh groups) can never trip it.

**Files:**
- Create: `src/polybot/truthgate/__init__.py`
- Create: `src/polybot/truthgate/gate.py`
- Test: `tests/test_truthgate_gate.py`

---

- [ ] **Step 1: Write the failing test — `TruthGateConfig` fails loud on non-positive fields**

```python
"""Tests for the citation truth-gate (S6 / POL-8).

The truth-gate is the ERS-side re-derivation of a Hermes proposal's evidence: it
matches citations against the already-sanitized EventStore (NEVER fetches/executes
them), keeps only allowlisted PRIMARY envelopes, collapses them by publisher_group,
and answers (a) corroborated = >=2 INDEPENDENT primaries, (b) the indirect-prompt-
injection signature: one fresh source moving p while a thin book lets it push the
mid -> same_source_collusion. DISCOVERY tier and non-allowlisted citations never
count and never trigger. Every value that is money/depth is a Decimal from a string.
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.news import DISCOVERY, PRIMARY, Source
from polybot.ingestion.orderbook import LocalBook
from polybot.storage.market_memory import EventStore
from polybot.truthgate.gate import (
    REASON_SAME_SOURCE,
    REASON_TRUTH_GATE_REFUSE,
    TruthGateConfig,
    TruthVerdict,
    verify,
)


def test_config_rejects_non_positive_fields():
    # all three fields must be strictly > 0 (fail loud, not a silent default)
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=0,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("0"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0"))
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_config_rejects_non_positive_fields -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'polybot.truthgate'` (the package/module does not exist yet).

- [ ] **Step 3: Write minimal implementation** — create the package marker and the config dataclass.

`src/polybot/truthgate/__init__.py`:
```python
```

(intentionally empty — matches the repo's other subpackage `__init__.py` files, e.g. `ers/__init__.py`.)

`src/polybot/truthgate/gate.py`:
```python
"""Citation truth-gate (S6 / POL-8).

ERS-side, post-INSERT verification of a Hermes proposal's citations. PURE over
(citations, EventStore envelopes, a live LocalBook). Citation strings are MATCHED
against the already-sanitized EventStore -- never fetched, never executed (untrusted-
data discipline). Two outputs the loop consumes:

  * corroborated = (>= 2 distinct, fresh, allowlisted PRIMARY publisher_groups).
    This is the single key that lets w_news go nonzero in fusion AND widens the
    anchor band. DISCOVERY tier and non-allowlisted citations never count.

  * refusal: zero allowlisted primaries -> REASON_TRUTH_GATE_REFUSE (news-only with
    no corroboration is refuse-and-alert). The indirect-prompt-injection signature
    -- one fresh source moving p while a thin book lets that same source push the
    mid -> REASON_SAME_SOURCE. An uncorroborated-but-present proposal is NOT refused;
    it just yields corroborated=False (informational-only, w_news=0 downstream).
"""

from dataclasses import dataclass
from decimal import Decimal


REASON_TRUTH_GATE_REFUSE = "truth_gate_refuse"
REASON_SAME_SOURCE = "same_source_collusion"


@dataclass(frozen=True)
class TruthGateConfig:
    freshness_window_ns: int      # collusion "fresh" window, on the shared ns clock
    thin_book_depth_usd: Decimal  # top-of-book USD depth below which the book is "thin"
    thin_book_move: Decimal       # bid/ask spread that reads as a pushed mid on a thin book

    def __post_init__(self):
        if not self.freshness_window_ns > 0:
            raise ValueError("freshness_window_ns must be > 0")
        if not self.thin_book_depth_usd > 0:
            raise ValueError("thin_book_depth_usd must be > 0")
        if not self.thin_book_move > 0:
            raise ValueError("thin_book_move must be > 0")
```

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_config_rejects_non_positive_fields -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/polybot/truthgate/__init__.py src/polybot/truthgate/gate.py tests/test_truthgate_gate.py
git commit -m "feat(truthgate): add TruthGateConfig with fail-loud field validation (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — two distinct publisher_groups corroborate**

Append local builders + the first `verify` test to `tests/test_truthgate_gate.py`:

```python
# --- local builders (the repo's per-file pattern; no conftest) ---
_CFG = TruthGateConfig(freshness_window_ns=10_000,
                       thin_book_depth_usd=Decimal("50"),
                       thin_book_move=Decimal("0.05"))

# Two independent primaries (distinct publisher_group), one discovery aggregator.
_FED = Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml",
              PRIMARY, publisher_group="federalreserve.gov")
_SEC = Source("sec-press", "https://www.sec.gov/news/pressreleases.rss",
              PRIMARY, publisher_group="sec.gov")
_GNEWS = Source("google-news-top", "https://news.google.com/rss", DISCOVERY,
                publisher_group="google.com")
_ALLOWLIST = (_FED, _SEC, _GNEWS)


def _book(ask="0.50", ask_size="1000", bid="0.49", bid_size="1000"):
    """Healthy, deep, tight book by default (NOT the collusion signature)."""
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book


def _seed(store, stamper, source, event_id, *, link):
    store.append(make_envelope(stamper, source=source.name, source_tier=source.tier,
                               event_id=event_id, content="text",
                               published_at=None, entities=(link,), market_links=()))


def test_two_distinct_groups_corroborated(tmp_path):
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _SEC, "sec1", link="https://www.sec.gov/1")
        now = stamper.stamp()
        v = verify(("fed1", "sec1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert isinstance(v, TruthVerdict)
    assert v.refused is False and v.reason is None
    assert v.corroborated is True
    assert set(v.primary_groups) == {"federalreserve.gov", "sec.gov"}
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_two_distinct_groups_corroborated -v`
  Expected: FAIL — `ImportError: cannot import name 'TruthVerdict'` / `'verify'` from `polybot.truthgate.gate` (not defined yet).

- [ ] **Step 3: Write minimal implementation** — add `TruthVerdict`, the matching/resolution helpers, and `verify` with the corroboration core. Append to `src/polybot/truthgate/gate.py`:

```python
@dataclass(frozen=True)
class TruthVerdict:
    refused: bool
    reason: str | None
    corroborated: bool
    primary_groups: tuple[str, ...]


def _group_for(allowlist):
    """name -> (tier, publisher_group) for every Source in the allowlist."""
    return {s.name: (s.tier, s.publisher_group) for s in allowlist}


def _matched_primaries(citations, *, event_store, by_name):
    """Resolve citation strings to envelopes (match on event_id OR a provenance link
    in entities), keep ONLY allowlisted PRIMARY envelopes. Citations are matched,
    never fetched. Returns the list of (envelope, publisher_group) kept."""
    wanted = set(citations)
    kept = []
    for env in event_store.all():
        if env.event_id in wanted or wanted.intersection(env.entities):
            meta = by_name.get(env.source)
            if meta is None:
                continue                      # not allowlisted -> dropped
            tier, group = meta
            if tier != PRIMARY:
                continue                      # DISCOVERY never counts / triggers
            kept.append((env, group))
    return kept


def verify(citations, *, event_store, book, allowlist, now_ns, config):
    by_name = _group_for(allowlist)
    matched = _matched_primaries(citations, event_store=event_store, by_name=by_name)

    if not matched:
        # news-only with no allowlisted primary corroboration -> refuse-and-alert.
        return TruthVerdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE,
                            corroborated=False, primary_groups=())

    groups = tuple(sorted({group for _env, group in matched}))
    corroborated = len(groups) >= 2
    return TruthVerdict(refused=False, reason=None,
                        corroborated=corroborated, primary_groups=groups)
```

Note for the executing agent: `PRIMARY` is already imported indirectly via the type comparison — add `from polybot.ingestion.news import PRIMARY` to the imports at the top of `gate.py`.

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_two_distinct_groups_corroborated -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/polybot/truthgate/gate.py tests/test_truthgate_gate.py
git commit -m "feat(truthgate): verify() corroborates >=2 distinct primary publisher_groups (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — fed-press + fed-monetary share a group => NOT corroborated (the confirmed-defect regression)**

```python
def test_same_publisher_group_not_corroborated_regression(tmp_path):
    # fed-press and fed-monetary are both federalreserve.gov: the confirmed same-domain
    # bypass. Two FEEDS, ONE publisher_group -> NOT independent -> NOT corroborated.
    fed_press = Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml",
                       PRIMARY, publisher_group="federalreserve.gov")
    fed_monetary = Source("fed-monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml",
                          PRIMARY, publisher_group="federalreserve.gov")
    allowlist = (fed_press, fed_monetary)
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, fed_press, "fp1", link="https://www.federalreserve.gov/p1")
        _seed(store, stamper, fed_monetary, "fm1", link="https://www.federalreserve.gov/m1")
        now = stamper.stamp()
        v = verify(("fp1", "fm1"), event_store=store, book=_book(),
                   allowlist=allowlist, now_ns=now, config=_CFG)

    assert v.refused is False                       # present, just not independent
    assert v.corroborated is False                  # the regression assertion
    assert v.primary_groups == ("federalreserve.gov",)   # collapsed to one group
```

- [ ] **Step 2: Run test to verify it fails (or passes-by-construction — confirm it's GREEN)**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_same_publisher_group_not_corroborated_regression -v`
  Expected: PASS if Task 1's `publisher_group` is wired and the corroboration core collapses by group. If it FAILS, the cause is Task 1 not yet assigning `federalreserve.gov` to both feeds (or `Source` not accepting `publisher_group`) — fix Task 1 first; do not weaken this test. (This is the safety regression the whole field exists for, so it gets its own committed test even though no new `gate.py` code is required.)

- [ ] **Step 3: Write minimal implementation** — none required; the group-collapse in `verify` already handles it. (If the test fails, the fix belongs in Task 1, not here.)

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_same_publisher_group_not_corroborated_regression -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add tests/test_truthgate_gate.py
git commit -m "test(truthgate): regression - fed-press+fed-monetary share a group, not corroborated (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — DISCOVERY tier is ignored (never counts, never triggers)**

```python
def test_discovery_tier_ignored(tmp_path):
    # One real primary + one discovery aggregator citation. Discovery must not count
    # toward corroboration NOR toward refusal: the single primary stands alone ->
    # present, uncorroborated, not refused.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _GNEWS, "gn1", link="https://news.google.com/x")
        now = stamper.stamp()
        v = verify(("fed1", "gn1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.corroborated is False                       # gn1 (DISCOVERY) does not count
    assert v.primary_groups == ("federalreserve.gov",)   # only the primary survives
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_discovery_tier_ignored -v`
  Expected: PASS (the `tier != PRIMARY -> continue` filter in `_matched_primaries` already drops DISCOVERY). If it FAILS, the filter regressed — restore it. (Committed regardless: DISCOVERY-never-triggers is a named invariant; it gets an explicit test.)

- [ ] **Step 3: Write minimal implementation** — none required; `_matched_primaries` already drops non-PRIMARY tiers.

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_discovery_tier_ignored -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add tests/test_truthgate_gate.py
git commit -m "test(truthgate): DISCOVERY-tier citations never count or trigger (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — zero allowlisted primaries => refused truth_gate_refuse**

```python
def test_no_allowlisted_primary_refused(tmp_path):
    # A citation that resolves to a NON-allowlisted source, plus a citation that
    # resolves to nothing. No allowlisted primary survives -> refuse-and-alert.
    rogue = Source("rogue-blog", "https://rogue.example/feed", PRIMARY,
                   publisher_group="rogue.example")   # NOT in _ALLOWLIST
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, rogue, "rogue1", link="https://rogue.example/1")
        now = stamper.stamp()
        v = verify(("rogue1", "does-not-exist"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is True
    assert v.reason == REASON_TRUTH_GATE_REFUSE
    assert v.corroborated is False
    assert v.primary_groups == ()
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_no_allowlisted_primary_refused -v`
  Expected: PASS (the `if not matched -> REASON_TRUTH_GATE_REFUSE` branch handles it; the rogue source is dropped by `by_name.get` returning `None`, the missing citation matches no envelope). If it FAILS, the allowlist membership check regressed.

- [ ] **Step 3: Write minimal implementation** — none required; the empty-`matched` branch already refuses.

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_no_allowlisted_primary_refused -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add tests/test_truthgate_gate.py
git commit -m "test(truthgate): zero allowlisted primaries -> refused truth_gate_refuse (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — empty citations => not refused? No: zero primaries => refused. And uncorroborated-but-present => not refused, corroborated False**

```python
def test_empty_citations_refused_truth_gate(tmp_path):
    # Empty citations resolve to zero allowlisted primaries -> refuse-and-alert.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        now = stamper.stamp()
        v = verify((), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
    assert v.refused is True and v.reason == REASON_TRUTH_GATE_REFUSE
    assert v.corroborated is False and v.primary_groups == ()


def test_single_primary_present_but_uncorroborated(tmp_path):
    # Exactly ONE allowlisted primary group, healthy deep book (no collusion signature)
    # -> NOT refused, corroborated=False (informational-only, w_news=0 downstream).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()
        v = verify(("fed1",), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
    assert v.refused is False and v.reason is None
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py::test_empty_citations_refused_truth_gate tests/test_truthgate_gate.py::test_single_primary_present_but_uncorroborated -v`
  Expected: PASS for both (empty -> empty `matched` -> refuse; single primary -> `len(groups)==1` -> not refused, corroborated False). If `test_empty_citations_refused_truth_gate` FAILS, an empty-input short-circuit was added that diverges from the "zero primaries -> refuse" contract — remove it.

- [ ] **Step 3: Write minimal implementation** — none required.

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -k "empty_citations or uncorroborated" -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add tests/test_truthgate_gate.py
git commit -m "test(truthgate): empty=refuse, single primary=present-uncorroborated (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — one fresh source + thin-book mid move => refused same_source_collusion**

```python
def _thin_pushed_book():
    """The collusion signature: a THIN top-of-book (tiny resting size) whose wide
    bid/ask spread reads as a mid that was pushed. depth USD = 10 * 0.55 = 5.5 < 50;
    spread = 0.55 - 0.45 = 0.10 >= thin_book_move 0.05."""
    book = LocalBook()
    book.apply_book({"bids": [{"price": "0.45", "size": "10"}],
                     "asks": [{"price": "0.55", "size": "10"}]})
    return book


def test_same_source_plus_thin_book_move_refused(tmp_path):
    # ONE fresh primary source moving p + a thin book it can push -> indirect-prompt-
    # injection signature -> refused same_source_collusion. The forecast is NOT logged
    # upstream for this reason (handled in the loop), and the signer is never reached.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()                         # fed1 is fresh within the window
        v = verify(("fed1",), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is True
    assert v.reason == REASON_SAME_SOURCE
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)


def test_corroborated_evidence_never_collusion(tmp_path):
    # Two INDEPENDENT fresh primaries on the SAME thin pushed book: corroboration
    # defeats the collusion signature (it takes >=2 distinct groups to push together,
    # which is exactly what corroboration verifies against). Not refused.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _SEC, "sec1", link="https://www.sec.gov/1")
        now = stamper.stamp()
        v = verify(("fed1", "sec1"), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False and v.reason is None
    assert v.corroborated is True


def test_single_source_but_stale_not_collusion(tmp_path):
    # ONE primary + thin pushed book, but the source is STALE (outside the freshness
    # window) -> the "fresh injection + pre-position" timing signature is absent ->
    # NOT collusion; just present-uncorroborated.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        observed = stamper.stamp()
        now = observed + _CFG.freshness_window_ns + 1   # fed1 is now stale
        v = verify(("fed1",), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -k "same_source or corroborated_evidence_never or stale_not_collusion" -v`
  Expected: FAIL — `test_same_source_plus_thin_book_move_refused` returns `refused=False` (collusion detection not implemented; current `verify` only does the corroboration core). The other two should already pass.

- [ ] **Step 3: Write minimal implementation** — add the thin-book / fresh-single-source collusion check to `verify`. Replace the body of `verify` in `src/polybot/truthgate/gate.py` with:

```python
def _is_thin_pushed(book, config):
    """Pure book-snapshot test for the 'a single source pushed a thin mid' signature:
    the smaller top-of-book USD depth is below thin_book_depth_usd AND the bid/ask
    spread is at least thin_book_move. Returns False on an empty side / no midpoint
    (a degenerate book is handled upstream by REJECT book_stale, not here)."""
    bid, bid_size, ask, ask_size = book.top_of_book()
    if bid is None or ask is None or bid_size is None or ask_size is None:
        return False
    bid_usd = bid * bid_size
    ask_usd = ask * ask_size
    depth_usd = min(bid_usd, ask_usd)
    spread = ask - bid
    return depth_usd < config.thin_book_depth_usd and spread >= config.thin_book_move


def verify(citations, *, event_store, book, allowlist, now_ns, config):
    by_name = _group_for(allowlist)
    matched = _matched_primaries(citations, event_store=event_store, by_name=by_name)

    if not matched:
        return TruthVerdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE,
                            corroborated=False, primary_groups=())

    groups = tuple(sorted({group for _env, group in matched}))
    corroborated = len(groups) >= 2

    # Same-source / indirect-prompt-injection refusal: the p-moving citations trace to
    # exactly ONE fresh source AND the book is thin enough that that one source could
    # have pushed the mid. Corroboration (>=2 distinct groups) defeats this by design.
    if not corroborated:
        fresh_groups = {group for env, group in matched
                        if now_ns - env.observed_at <= config.freshness_window_ns}
        if len(fresh_groups) == 1 and _is_thin_pushed(book, config):
            return TruthVerdict(refused=True, reason=REASON_SAME_SOURCE,
                                corroborated=False, primary_groups=groups)

    return TruthVerdict(refused=False, reason=None,
                        corroborated=corroborated, primary_groups=groups)
```

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -k "same_source or corroborated_evidence_never or stale_not_collusion" -v`
  Expected: PASS (all three).

- [ ] **Step 5: Commit**
```bash
git add src/polybot/truthgate/gate.py tests/test_truthgate_gate.py
git commit -m "feat(truthgate): refuse same_source_collusion on one fresh source + thin pushed book (S6/POL-8)"
```

---

- [ ] **Step 1: Write the failing test — non-allowlisted citation is dropped, and citations are never fetched/executed**

```python
def test_non_allowlisted_citation_dropped_but_primary_survives(tmp_path):
    # A rogue (non-allowlisted) citation alongside a real allowlisted primary: the
    # rogue is silently dropped, the primary still yields present-uncorroborated.
    rogue = Source("rogue-blog", "https://rogue.example/feed", PRIMARY,
                   publisher_group="rogue.example")
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, rogue, "rogue1", link="https://rogue.example/1")
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()
        v = verify(("rogue1", "fed1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.primary_groups == ("federalreserve.gov",)   # rogue dropped, not counted


def test_citations_are_matched_never_fetched(tmp_path):
    # Pass an http(s) citation string that is NOT in the store. The gate must NOT
    # attempt any network I/O to resolve it -- it simply fails to match. We prove the
    # gate is network-free by patching out the http clients it could conceivably use
    # and asserting they are never called; the unresolved citation yields zero matches
    # (-> refuse), with no exception and no fetch.
    import httpx

    calls = []

    class _Boom:
        def __getattr__(self, _name):
            def _fail(*a, **k):
                calls.append(1)
                raise AssertionError("truth-gate must never fetch a citation")
            return _fail

    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        now = stamper.stamp()
        original_client = httpx.Client
        httpx.Client = _Boom              # any accidental fetch path explodes
        try:
            v = verify(("https://anything.example/never-fetched",),
                       event_store=store, book=_book(),
                       allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
        finally:
            httpx.Client = original_client

    assert calls == []                                   # no fetch attempted
    assert v.refused is True and v.reason == REASON_TRUTH_GATE_REFUSE
```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -k "non_allowlisted_citation_dropped or matched_never_fetched" -v`
  Expected: PASS for both — `_matched_primaries` already drops non-allowlisted sources and only reads `event_store.all()` (no network). The fetch-guard test passes because `verify` never references `httpx`. If `test_citations_are_matched_never_fetched` FAILS by raising the `AssertionError`, a fetch path was introduced — remove it; the gate is pure over the store.

- [ ] **Step 3: Write minimal implementation** — none required; the gate is already network-free and drops non-allowlisted sources.

- [ ] **Step 4: Run test to verify it passes**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -k "non_allowlisted_citation_dropped or matched_never_fetched" -v`
  Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add tests/test_truthgate_gate.py
git commit -m "test(truthgate): non-allowlisted citations dropped; citations matched never fetched (S6/POL-8)"
```

---

- [ ] **Step 1: Run the full truth-gate suite and the full repo suite to confirm no regressions**
  Run: `./.venv/bin/pytest tests/test_truthgate_gate.py -v` then `./.venv/bin/pytest`
  Expected: the truth-gate file is fully GREEN, and the whole suite stays green (Task 4 only ADDS `src/polybot/truthgate/`; it does not modify `service.py`, `validator.py`, or the existing 377 tests — those are wired in the integration task). The `publisher_group` regression depends on Task 1; if the full suite is run before Task 1 lands, only `test_same_publisher_group_not_corroborated_regression` and the `publisher_group=` constructions will fail with a `TypeError` on the unknown `Source(publisher_group=...)` kwarg — that is expected ordering, not a Task 4 defect.

- [ ] **Step 2: Commit (final, if any whitespace/import cleanup was needed)**
```bash
git add -A
git commit -m "chore(truthgate): finalize CitationTruthGate unit for S6/POL-8" || echo "nothing to commit"
```


---

### Task 5: ProposeOnlyFacade

The load-bearing safety boundary. `ProposeOnlyFacade` **composes** (never subclasses) an `IntentStore` so it inherits no `record_decision`/`pending` method, holds the store in a **name-mangled private attribute** (`self.__store` → `self._ProposeOnlyFacade__store`), and exposes EXACTLY: `propose_trade` (the one INSERT-only write), `get`/`audit_log` (read own proposals + immutable audit), and the 4 read tools `get_market`/`get_book`/`get_ledger`/`get_flags` (each delegating to an injected `*_reader` callable). The structural guarantee — "Hermes can at worst enqueue" — is made load-bearing in code by a `dir()`/`getattr`/`hasattr` sweep test that proves there is no `place`, `flatten`, `record_decision`, `pending`, or public `store` attribute, and no public path to the signer or to a status transition.

**Files:**
- Create: `src/polybot/ers/facade.py`
- Test: `tests/test_ers_facade.py`

Reference (verbatim, already in the repo — do NOT modify):
- `IntentStore.propose_trade(self, intent_id, *, token_id, condition_id, event_id, side, target_price, max_price, size_usd_suggestion, p, p_confidence, resolution_summary="", thesis="", citations=()) -> bool` (True if newly inserted, False on duplicate `intent_id`) — `src/polybot/ers/intent_store.py:101`.
- `IntentStore.get(self, intent_id) -> PendingIntent | None` — `:146`.
- `IntentStore.audit_log(self) -> list[dict]` (keys `intent_id, at, verdict, stake_usd, price_exec, reason`) — `:150`.
- `IntentStore.record_decision`/`pending` exist on `IntentStore` but MUST NOT be reachable through the facade (the chokepoint).
- `IntentStore(path, stamper)` is a context manager; build with `str(tmp_path / "i.db")` and a `MonotonicStamper`.

---

- [ ] **Step 1: Write the failing test — `propose_trade` delegates the INSERT-only call**

```python
# tests/test_ers_facade.py
"""ProposeOnlyFacade — the load-bearing S6/POL-8 safety boundary.

Hermes is handed ONLY this facade. The safety claim is structural and IN CODE:
the facade composes (never subclasses) an IntentStore, holds it in a
name-mangled private attribute, and exposes EXACTLY {propose_trade, get,
audit_log, get_market, get_book, get_ledger, get_flags}. It has no place /
flatten / record_decision / pending attribute and no public path to mutate
status or reach the signer. A confused-deputy Hermes can at worst enqueue a
PROPOSED row; the deterministic ERS (not Hermes) disposes.
"""
import inspect
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.ers.facade import ProposeOnlyFacade

_PROPOSAL = dict(
    token_id="t1", condition_id="0xabc", event_id="e1", side="BUY",
    target_price="0.55", max_price="0.60", size_usd_suggestion="10",
    p="0.7", p_confidence="0.6", resolution_summary="will X happen?",
    thesis="because Y", citations=("https://primary.example/1",),
)


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def test_propose_trade_delegates_insert(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        ok = facade.propose_trade("intent-1", **_PROPOSAL)
        assert ok is True
        # The row landed in the underlying store as PROPOSED (delegated INSERT).
        row = store.get("intent-1")
        assert row is not None and row.status == "PROPOSED"
        assert row.token_id == "t1" and row.side == "BUY"
        # Signature parity: the facade's propose_trade exposes the SAME kwargs
        # as the store (no extra `status` param — the chokepoint).
        params = inspect.signature(facade.propose_trade).parameters
        assert "status" not in params
        assert "citations" in params and "p" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_propose_trade_delegates_insert -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polybot.ers.facade'` (the module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# src/polybot/ers/facade.py
"""Propose-only facade (S6 / POL-8) — the load-bearing safety boundary.

Hermes (a frozen-model harness with NO keys, undeployed in S6) is handed ONLY
an instance of this facade. The entire safety model is structural and lives in
CODE, not in a prompt:

  * The facade COMPOSES an ``IntentStore`` (it does NOT subclass it), so it
    inherits no ``record_decision`` / ``pending`` method.
  * The store reference is held in a NAME-MANGLED private attribute
    (``self.__store`` -> ``_ProposeOnlyFacade__store``); there is no public
    ``store`` attribute to reach through.
  * The only write surface is ``propose_trade(...)``, which delegates the
    store's INSERT-only call. It has no ``status`` parameter, so a
    confused-deputy Hermes can at worst enqueue a ``PROPOSED`` row.
  * Read surface: ``get`` / ``audit_log`` (own proposals + the immutable audit)
    plus the 4 Hermes read tools (``get_market`` / ``get_book`` / ``get_ledger``
    / ``get_flags``), each delegating to an INJECTED read-only callable.

The deterministic ERS (NOT Hermes) is what polls ``pending()``, runs the
validator, and calls ``record_decision`` -- none of which the facade exposes.
A ``dir()``/attribute sweep test (``test_ers_facade.py``) makes the
"no place/flatten/record_decision/pending, no public store, no signer path"
guarantee load-bearing so careless future wiring cannot regress it.
"""


class ProposeOnlyFacade:
    def __init__(self, store, *, market_reader=None, book_reader=None,
                 ledger_reader=None, flags_reader=None):
        # Name-mangled private: no public attribute exposes the IntentStore, so
        # Hermes cannot reach record_decision / pending / the audit-mutation path.
        self.__store = store
        self.__market_reader = market_reader
        self.__book_reader = book_reader
        self.__ledger_reader = ledger_reader
        self.__flags_reader = flags_reader

    # --- the ONE write: INSERT-only, no `status` param (the chokepoint) ---
    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()):
        return self.__store.propose_trade(
            intent_id, token_id=token_id, condition_id=condition_id,
            event_id=event_id, side=side, target_price=target_price,
            max_price=max_price, size_usd_suggestion=size_usd_suggestion,
            p=p, p_confidence=p_confidence, resolution_summary=resolution_summary,
            thesis=thesis, citations=citations,
        )

    # --- reads of the facade's own proposal store (no mutation surface) ---
    def get(self, intent_id):
        return self.__store.get(intent_id)

    def audit_log(self):
        return self.__store.audit_log()

    # --- the 4 Hermes read tools: delegate to injected read-only callables ---
    def get_market(self, *args, **kwargs):
        return self.__market_reader(*args, **kwargs)

    def get_book(self, *args, **kwargs):
        return self.__book_reader(*args, **kwargs)

    def get_ledger(self, *args, **kwargs):
        return self.__ledger_reader(*args, **kwargs)

    def get_flags(self, *args, **kwargs):
        return self.__flags_reader(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_propose_trade_delegates_insert -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polybot/ers/facade.py tests/test_ers_facade.py
git commit -m "feat(ers): add ProposeOnlyFacade propose_trade delegation (S6/POL-8)"
```

---

- [ ] **Step 6: Write the failing test — `propose_trade` is idempotent (dup → False)**

```python
def test_propose_trade_idempotent_returns_false_on_dup(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        first = facade.propose_trade("intent-1", **_PROPOSAL)
        second = facade.propose_trade("intent-1", **_PROPOSAL)
        assert first is True and second is False
        # Still exactly one row; the dup INSERT was IGNOREd by the store.
        assert store.get("intent-1") is not None
```

- [ ] **Step 7: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_propose_trade_idempotent_returns_false_on_dup -v`
Expected: PASS — the facade delegates straight to `IntentStore.propose_trade`, which uses `INSERT OR IGNORE` and returns `cur.rowcount > 0`, so the duplicate yields `False`. (No new implementation; this asserts the delegated idempotency contract holds through the facade.)

- [ ] **Step 8: Commit**

```bash
git add tests/test_ers_facade.py
git commit -m "test(ers): assert ProposeOnlyFacade propose_trade idempotency (S6/POL-8)"
```

---

- [ ] **Step 9: Write the failing test — `get` and `audit_log` read through the facade**

```python
def test_get_and_audit_log_read_through(tmp_path):
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)
        assert facade.get("missing") is None        # nothing proposed yet
        facade.propose_trade("intent-1", **_PROPOSAL)

        row = facade.get("intent-1")
        assert row is not None and row.intent_id == "intent-1"
        assert row.status == "PROPOSED" and row.p == Decimal("0.7")

        # audit_log is read-only and empty until the ERS (not the facade)
        # records a decision; the facade exposes no way to write an audit row.
        assert facade.audit_log() == []
```

- [ ] **Step 10: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_get_and_audit_log_read_through -v`
Expected: PASS — `get`/`audit_log` delegate to the store; the audit table is empty because only the ERS-only `record_decision` (not exposed) appends to it.

- [ ] **Step 11: Commit**

```bash
git add tests/test_ers_facade.py
git commit -m "test(ers): assert ProposeOnlyFacade get/audit_log read-through (S6/POL-8)"
```

---

- [ ] **Step 12: Write the failing test — the 4 read tools delegate to their `*_reader` callables**

```python
def test_read_tools_delegate_to_readers(tmp_path):
    calls = {"market": [], "book": [], "ledger": [], "flags": []}

    def _market_reader(*a, **k):
        calls["market"].append((a, k)); return "MARKET"

    def _book_reader(*a, **k):
        calls["book"].append((a, k)); return "BOOK"

    def _ledger_reader(*a, **k):
        calls["ledger"].append((a, k)); return "LEDGER"

    def _flags_reader(*a, **k):
        calls["flags"].append((a, k)); return "FLAGS"

    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(
            store, market_reader=_market_reader, book_reader=_book_reader,
            ledger_reader=_ledger_reader, flags_reader=_flags_reader,
        )
        assert facade.get_market("0xabc") == "MARKET"
        assert facade.get_book("t1", depth=5) == "BOOK"
        assert facade.get_ledger(category="politics") == "LEDGER"
        assert facade.get_flags("t1") == "FLAGS"

        # Each reader was invoked exactly once with the forwarded args/kwargs.
        assert calls["market"] == [(("0xabc",), {})]
        assert calls["book"] == [(("t1",), {"depth": 5})]
        assert calls["ledger"] == [((), {"category": "politics"})]
        assert calls["flags"] == [(("t1",), {})]
```

- [ ] **Step 13: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_read_tools_delegate_to_readers -v`
Expected: PASS — each `get_*` forwards `*args, **kwargs` verbatim to the injected callable. (No new implementation; the four delegating methods already exist.)

- [ ] **Step 14: Commit**

```bash
git add tests/test_ers_facade.py
git commit -m "test(ers): assert ProposeOnlyFacade read tools delegate to readers (S6/POL-8)"
```

---

- [ ] **Step 15: Write the failing test — THE STRUCTURAL SWEEP (the heart of S6's safety claim)**

```python
def test_structural_sweep_no_signer_or_status_path(tmp_path):
    """Load-bearing safety guarantee: the facade exposes EXACTLY the allowed
    public names and NO dangerous attribute. This is the property that makes
    'Hermes can at worst enqueue' true in code, surviving careless future
    wiring."""
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)

        # (a) The public surface is EXACTLY the allowed set -- nothing more.
        allowed = {
            "propose_trade", "get", "audit_log",
            "get_market", "get_book", "get_ledger", "get_flags",
        }
        public = {name for name in dir(facade) if not name.startswith("_")}
        assert public == allowed, f"unexpected public surface: {public ^ allowed}"

        # (b) No dispose/mutate/signer attribute is reachable on the facade,
        #     by any access path (hasattr covers inherited + instance attrs).
        for forbidden in ("place", "flatten", "record_decision", "pending",
                          "signer", "store"):
            assert not hasattr(facade, forbidden), forbidden
            assert forbidden not in dir(facade), forbidden

        # (c) The facade did NOT subclass IntentStore (composition only), so it
        #     inherits none of the store's dispose methods.
        assert not isinstance(facade, IntentStore)
        assert IntentStore not in type(facade).__mro__

        # (d) The store ref exists ONLY under name-mangling -- there is no plain
        #     `store` / `_store` attribute Hermes could dot into.
        assert not hasattr(facade, "_store")
        assert getattr(facade, "_ProposeOnlyFacade__store", None) is store

        # (e) Even reaching the mangled store, propose_trade has no `status`
        #     param: there is no public path to transition a status or sign.
        assert "status" not in inspect.signature(facade.propose_trade).parameters
```

- [ ] **Step 16: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_structural_sweep_no_signer_or_status_path -v`
Expected: PASS — the implementation exposes only the 7 allowed public methods, composes (does not subclass) `IntentStore`, and stores the ref name-mangled. If this fails, the implementation has leaked a dangerous attribute and must be fixed before proceeding (this is the safety-critical test).

- [ ] **Step 17: Commit**

```bash
git add tests/test_ers_facade.py
git commit -m "test(ers): structural sweep proves ProposeOnlyFacade has no signer/status path (S6/POL-8)"
```

---

- [ ] **Step 18: Write the failing test — read tools fail loud when no reader was injected**

```python
def test_read_tools_fail_loud_without_reader(tmp_path):
    """A reader is None by default; calling that read tool must raise, not
    silently return None -- fail-closed over a misconfigured wiring."""
    import pytest
    with _store(tmp_path) as store:
        facade = ProposeOnlyFacade(store)        # no readers injected
        with pytest.raises(TypeError):           # None is not callable
            facade.get_market("0xabc")
        with pytest.raises(TypeError):
            facade.get_book("t1")
        with pytest.raises(TypeError):
            facade.get_ledger()
        with pytest.raises(TypeError):
            facade.get_flags("t1")
```

- [ ] **Step 19: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ers_facade.py::test_read_tools_fail_loud_without_reader -v`
Expected: PASS — each `get_*` calls `self.__<reader>(...)`; when the reader is `None`, the `None(...)` invocation raises `TypeError: 'NoneType' object is not callable`. (No new implementation; this pins the fail-closed behavior of an un-wired read tool.)

- [ ] **Step 20: Commit**

```bash
git add tests/test_ers_facade.py
git commit -m "test(ers): assert ProposeOnlyFacade read tools fail loud when unwired (S6/POL-8)"
```

---

- [ ] **Step 21: Run the full facade file + the full suite to confirm no regressions**

Run: `./.venv/bin/pytest tests/test_ers_facade.py -v`
Expected: PASS — all 7 facade tests green.

Run: `./.venv/bin/pytest`
Expected: PASS — the full suite (the 377 existing tests + the 7 new facade tests = 384) green; `facade.py` is purely additive and touches no existing module.

- [ ] **Step 22: Commit (final, if anything was tidied)**

```bash
git add -A
git commit -m "test(ers): full-suite green after ProposeOnlyFacade (S6/POL-8)" --allow-empty
```

---

### Task 6: DetectorOrchestrator

Composes the S7 pure detectors (`toxicity` → `d2..d6` → `composite` → `policy.decide`) into ONE defensive `DetectorVerdict` that the S6 `process_pending` loop consumes (step 2: `AVOID → REJECT detector_avoid`). At S6 the orchestrator is fed placeholder/zero sub-scores — live `/activity` + on-chain input parsing is POL-9-deferred — so the default verdict for an all-zero `DetectorInputs` is `FLAG_ONLY` (never `AVOID`). The orchestrator must CATCH `toxicity()`'s `ValueError`-on-negative-size (it must NOT propagate up and wedge the per-intent guard) and degrade to a safe verdict. `FOLLOW_ENABLED` stays `False`, so `action` is never `FOLLOW`. `p_flow` (the smart-money confirmation signal, 0-weight in fusion v1 but logged) is surfaced as a `Decimal`.

**Files:**
- Create: `src/polybot/detectors/orchestrator.py`
- Test: `tests/test_detectors_orchestrator.py`

> Pinned contract (authoritative; do NOT invent variants):
> ```python
> @dataclass(frozen=True)
> class DetectorInputs:
>     buy_size: Decimal = Decimal(0)
>     sell_size: Decimal = Decimal(0)
>     baseline_mean: Decimal = Decimal(0)
>     baseline_std: Decimal = Decimal(0)
>     d2: Decimal = Decimal(0)
>     d3: Decimal = Decimal(0)
>     d4: Decimal = Decimal(0)
>     d5: Decimal = Decimal(0)
>     d6: Decimal = Decimal(0)
>     classification: str = "NOISE"
>     catalyst_present: bool = False
>
> @dataclass(frozen=True)
> class DetectorVerdict:
>     action: str          # 'AVOID' | 'FLAG_ONLY'
>     pull_quotes: bool
>     p_flow: Decimal
>     reasons: tuple[str, ...]
>
> REASON_DETECTOR_AVOID = "detector_avoid"
>
> class DetectorOrchestrator:
>     def __init__(self, config): ...                 # the existing detectors.DetectorConfig
>     def evaluate(self, intent, *, inputs: DetectorInputs) -> DetectorVerdict: ...
> ```
> NOTE — the S6 DESIGN doc sketched a module-level `def evaluate(intent, *, inputs, config)`. The PINNED contract is authoritative: it is a `DetectorOrchestrator` class that holds `config` on the instance (`__init__(self, config)`) and exposes `evaluate(self, intent, *, inputs)`. Follow the pinned contract.
>
> Detector statistical sub-scores are plain `float` internally (`composite`, `clamp01`, `toxicity.subscore` are floats); `p_flow` and the `DetectorInputs` money/probability fields are `Decimal`. `p_flow` is derived from the D6 smart-money score (`d6_smart_money`) and re-wrapped as a `Decimal` via `Decimal(str(...))`.

---

- [ ] **Step 1: Write the failing test — zero inputs yield a safe FLAG_ONLY verdict (the S6 default)**

The orchestrator fed an all-default (zero) `DetectorInputs` must produce the quiet, non-blocking verdict: `FLAG_ONLY`, not `AVOID`, with no pull-quotes and `p_flow == Decimal(0)`.

```python
"""S6 / POL-8 — DetectorOrchestrator: composes the S7 pure detectors into one defensive verdict.

Safety properties under test:
  * zero/placeholder inputs (the S6 state; live D1-D6 wiring is POL-9-deferred) -> FLAG_ONLY, never AVOID;
  * a CRITICAL composite OR an INSIDER_LIKE classification -> AVOID with reason "detector_avoid";
  * toxicity()'s ValueError-on-negative-size is CAUGHT (not propagated) and yields a safe verdict;
  * FOLLOW stays off: action is never FOLLOW across the input space;
  * p_flow (the smart-money confirmation signal) is surfaced as a Decimal.
"""

from decimal import Decimal

from polybot.detectors.config import DetectorConfig
from polybot.detectors.orchestrator import (
    DetectorInputs,
    DetectorOrchestrator,
    DetectorVerdict,
    REASON_DETECTOR_AVOID,
)
from polybot.detectors.policy import AVOID, FLAG_ONLY, FOLLOW

CFG = DetectorConfig()


def _orch():
    return DetectorOrchestrator(CFG)


class _Intent:
    """Minimal stand-in for a PendingIntent; the orchestrator reads nothing off it at S6."""
    token_id = "t1"
    condition_id = "0xabc"
    event_id = "e1"


def test_zero_inputs_yield_flag_only_never_avoid():
    v = _orch().evaluate(_Intent(), inputs=DetectorInputs())
    assert isinstance(v, DetectorVerdict)
    assert v.action == FLAG_ONLY
    assert v.action != AVOID
    assert v.pull_quotes is False
    assert v.p_flow == Decimal(0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_zero_inputs_yield_flag_only_never_avoid -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'polybot.detectors.orchestrator'` (the module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
"""DetectorOrchestrator (S6 / POL-8): composes the S7 pure detectors into ONE defensive verdict.

The ERS loop (process_pending, step 2) consumes this: action == AVOID -> the intent is REJECTed with
reason REASON_DETECTOR_AVOID. The pipeline is toxicity -> d2..d6 -> composite() -> policy.decide().

DEFENSIVE invariants:
  * FOLLOW is hard-off (policy.FOLLOW_ENABLED is False), so the verdict action is only ever AVOID/FLAG_ONLY.
  * toxicity()'s ValueError-on-negative-size (data corruption from the POL-9-deferred /activity parser)
    is CAUGHT here, never propagated -- a corrupt input must degrade to a safe verdict, not wedge the
    per-intent guard. On that path D1 contributes 0 and pull_quotes stays False.
  * At S6 the inputs are placeholder/zeros (live /activity + on-chain parsing is POL-9-deferred); the
    orchestrator and the AVOID->REJECT wiring are real and tested.

p_flow is the D6 smart-money confirmation signal, surfaced as a Decimal (0 weight in fusion v1, logged).
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.detectors.composite import composite
from polybot.detectors.policy import decide
from polybot.detectors.signals import d6_smart_money
from polybot.detectors.toxicity import toxicity

REASON_DETECTOR_AVOID = "detector_avoid"


@dataclass(frozen=True)
class DetectorInputs:
    # S6 defaults = zeros: live /activity + on-chain inputs are POL-9-deferred.
    buy_size: Decimal = Decimal(0)
    sell_size: Decimal = Decimal(0)
    baseline_mean: Decimal = Decimal(0)
    baseline_std: Decimal = Decimal(0)
    d2: Decimal = Decimal(0)
    d3: Decimal = Decimal(0)
    d4: Decimal = Decimal(0)
    d5: Decimal = Decimal(0)
    d6: Decimal = Decimal(0)
    classification: str = "NOISE"
    catalyst_present: bool = False


@dataclass(frozen=True)
class DetectorVerdict:
    action: str          # 'AVOID' | 'FLAG_ONLY'  (never FOLLOW -- FOLLOW_ENABLED is False)
    pull_quotes: bool
    p_flow: Decimal      # the D6 smart-money confirmation signal (0 weight in fusion v1, logged)
    reasons: tuple


class DetectorOrchestrator:
    def __init__(self, config):
        self._config = config

    def evaluate(self, intent, *, inputs):
        # D1 toxicity. CATCH the ValueError-on-negative-size: a corrupt size must degrade to a safe
        # verdict (D1 -> 0, no pull-quotes), not blow up the per-intent guard.
        try:
            tox = toxicity(
                inputs.buy_size, inputs.sell_size,
                baseline_mean=inputs.baseline_mean, baseline_std=inputs.baseline_std,
                config=self._config,
            )
            d1 = tox.subscore
            pull_quotes = tox.pull_quotes
        except ValueError:
            d1 = 0.0
            pull_quotes = False

        # D2-D6 are already-normalized [0,1] floats at S6 (zeros until POL-9 wires the live inputs).
        d2 = float(inputs.d2)
        d3 = float(inputs.d3)
        d4 = float(inputs.d4)
        d5 = float(inputs.d5)
        d6 = float(inputs.d6)

        score = composite({"D1": d1, "D2": d2, "D3": d3, "D4": d4, "D5": d5, "D6": d6}, self._config)

        decision = decide(
            composite_band=score.band,
            classification=inputs.classification,
            pull_quotes=pull_quotes,
        )

        # p_flow = the D6 smart-money score, re-wrapped as a Decimal (0 weight in fusion v1, logged).
        p_flow = Decimal(str(d6_smart_money(edge_weight=d6, conviction=1.0)))

        return DetectorVerdict(
            action=decision.action,
            pull_quotes=decision.pull_quotes,
            p_flow=p_flow,
            reasons=decision.reasons,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_zero_inputs_yield_flag_only_never_avoid -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polybot/detectors/orchestrator.py tests/test_detectors_orchestrator.py
git commit -m "feat(detectors): DetectorOrchestrator scaffold + zero-input FLAG_ONLY default (S6/POL-8)"
```

---

- [ ] **Step 6: Write the failing test — a CRITICAL composite produces AVOID with the detector_avoid reason**

A single sub-score at/above `critical_subscore` (0.8) escalates the composite band to HIGH/CRITICAL; the policy then returns `AVOID`. The orchestrator must surface `action == AVOID` and the loop's reason constant.

```python
def test_critical_composite_avoids_with_detector_reason():
    # D2 = 0.95 >= critical_subscore (0.8) -> composite band escalates to >= HIGH -> policy AVOID.
    inputs = DetectorInputs(d2=Decimal("0.95"))
    v = _orch().evaluate(_Intent(), inputs=inputs)
    assert v.action == AVOID
    assert REASON_DETECTOR_AVOID == "detector_avoid"
    assert "informed_flow" in v.reasons
```

- [ ] **Step 7: Run test to verify it fails / passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_critical_composite_avoids_with_detector_reason -v`
Expected: PASS (the Step 3 implementation already routes the composite band through `policy.decide`; this RED→GREEN cycle pins the AVOID escalation as a regression guard). If it FAILS, the composite/policy wiring is wrong — fix the orchestrator, not the test.

- [ ] **Step 8: Commit**

```bash
git add tests/test_detectors_orchestrator.py
git commit -m "test(detectors): CRITICAL composite -> AVOID(detector_avoid) in orchestrator (S6/POL-8)"
```

---

- [ ] **Step 9: Write the failing test — an INSIDER_LIKE classification AVOIDs even at a quiet (LOW) band**

`policy.decide` returns `AVOID` for `INSIDER_LIKE` regardless of band. The orchestrator must propagate that even when every sub-score is zero (LOW band).

```python
from polybot.detectors.classify import INSIDER_LIKE


def test_insider_like_classification_avoids_even_at_low_band():
    # All sub-scores zero -> LOW band, but INSIDER_LIKE classification forces AVOID.
    inputs = DetectorInputs(classification=INSIDER_LIKE)
    v = _orch().evaluate(_Intent(), inputs=inputs)
    assert v.action == AVOID
    assert "insider_like" in v.reasons
```

- [ ] **Step 10: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_insider_like_classification_avoids_even_at_low_band -v`
Expected: PASS (`inputs.classification` flows straight into `policy.decide`; this cycle pins the insider-classification AVOID path).

- [ ] **Step 11: Commit**

```bash
git add tests/test_detectors_orchestrator.py
git commit -m "test(detectors): INSIDER_LIKE -> AVOID at LOW band in orchestrator (S6/POL-8)"
```

---

- [ ] **Step 12: Write the failing test — a negative size is CAUGHT, not propagated, and yields a safe verdict**

`toxicity()` raises `ValueError` on a negative size (data corruption). The orchestrator must catch it: no exception escapes, D1 contributes 0, `pull_quotes` is False, and (with all other sub-scores zero) the verdict is the safe `FLAG_ONLY`.

```python
def test_negative_size_is_caught_and_yields_a_safe_verdict():
    # A negative buy_size makes toxicity() raise ValueError; the orchestrator must swallow it and
    # degrade to a safe verdict rather than letting the exception wedge the per-intent guard.
    inputs = DetectorInputs(buy_size=Decimal("-10"), sell_size=Decimal("50"),
                            baseline_mean=Decimal("0.2"), baseline_std=Decimal("0.1"))
    v = _orch().evaluate(_Intent(), inputs=inputs)   # must NOT raise
    assert v.action == FLAG_ONLY
    assert v.pull_quotes is False
    assert v.p_flow == Decimal(0)
```

- [ ] **Step 13: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_negative_size_is_caught_and_yields_a_safe_verdict -v`
Expected: PASS (the `try/except ValueError` around `toxicity()` in Step 3 handles this). If it FAILS with a `ValueError` escaping the call, the catch is missing — add it.

- [ ] **Step 14: Commit**

```bash
git add tests/test_detectors_orchestrator.py
git commit -m "test(detectors): orchestrator catches toxicity ValueError-on-negative-size (S6/POL-8)"
```

---

- [ ] **Step 15: Write the failing test — FOLLOW stays off across the whole classification × sub-score space**

Sweep every classification and a representative sub-score range; assert the orchestrator NEVER returns `FOLLOW` (`FOLLOW_ENABLED` is pinned `False`).

```python
from polybot.detectors.classify import LUCKY, MARKET_MAKER, NOISE, SHARP


def test_follow_is_never_emitted_across_the_input_space():
    orch = _orch()
    for cls in (SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE):
        for d in ("0", "0.5", "0.95"):
            inputs = DetectorInputs(classification=cls, d2=Decimal(d), d6=Decimal(d))
            v = orch.evaluate(_Intent(), inputs=inputs)
            assert v.action in (AVOID, FLAG_ONLY), (cls, d)
            assert v.action != FOLLOW, (cls, d)
```

- [ ] **Step 16: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_follow_is_never_emitted_across_the_input_space -v`
Expected: PASS (`policy.decide` only emits `FOLLOW` behind the dead `FOLLOW_ENABLED` branch).

- [ ] **Step 17: Commit**

```bash
git add tests/test_detectors_orchestrator.py
git commit -m "test(detectors): FOLLOW never emitted by orchestrator (FOLLOW_ENABLED off) (S6/POL-8)"
```

---

- [ ] **Step 18: Write the failing test — p_flow surfaces the D6 smart-money score as a Decimal**

A non-zero `d6` must surface as a non-zero `Decimal` `p_flow` (the smart-money confirmation signal); a zero `d6` surfaces `Decimal(0)`.

```python
def test_p_flow_surfaces_d6_smart_money_as_decimal():
    v = _orch().evaluate(_Intent(), inputs=DetectorInputs(d6=Decimal("0.6")))
    # d6_smart_money(edge_weight=0.6, conviction=1.0) == 0.6, surfaced as a Decimal.
    assert isinstance(v.p_flow, Decimal)
    assert v.p_flow == Decimal("0.6")
    # And zero d6 -> zero p_flow.
    z = _orch().evaluate(_Intent(), inputs=DetectorInputs(d6=Decimal(0)))
    assert z.p_flow == Decimal(0)
```

- [ ] **Step 19: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_detectors_orchestrator.py::test_p_flow_surfaces_d6_smart_money_as_decimal -v`
Expected: PASS (`p_flow = Decimal(str(d6_smart_money(edge_weight=d6, conviction=1.0)))`). NOTE for the executor: `d6_smart_money` returns a plain float; with `edge_weight=0.6` it is exactly `0.6`, and `Decimal(str(0.6)) == Decimal("0.6")` — going through `str()` (not `Decimal(0.6)`) is what keeps the equality exact. Do NOT change to `Decimal(d6_smart_money(...))` (that re-introduces binary-float noise and breaks the assertion).

- [ ] **Step 20: Commit**

```bash
git add tests/test_detectors_orchestrator.py
git commit -m "test(detectors): orchestrator surfaces D6 smart-money as Decimal p_flow (S6/POL-8)"
```

---

- [ ] **Step 21: Run the full suite to confirm no regression**

Run: `./.venv/bin/pytest`
Expected: PASS — all 377+ existing tests still green, plus the 6 new orchestrator tests. The orchestrator is purely additive (a new module + a new test file); it touches no existing source, so the 377 baseline must remain green.

- [ ] **Step 22: Final commit (if any stragglers / formatting)**

```bash
git add -A
git commit -m "chore(detectors): finalize DetectorOrchestrator for S6/POL-8 pipeline wiring"
```


---

### Task 7: StubMarketMeta

Build the MVP `MarketMeta` stub that `HermesPipeline` injects so the calibration arm has a category, a question string, and a seconds-to-resolution value for every proposal — *without* a real `MarketRegistry` (Gamma metadata) feed, which is deferred to the calibration-warming slice (DESIGN §8). The stub is deliberately neutered toward safety:

- `category_for(intent) -> "unknown"` — a single category bucket. The `CalibrationGate.k_for("unknown")` has no resolved forecasts → `k = 0` → **paper-only by design**. Do NOT collapse the `k_for` / `prior_for` keyspaces (DESIGN decision #6).
- `question_text_for(intent) -> intent.resolution_summary` — the proposal's own free-text summary (the only NL the ERS has at MVP; feeds `PriorEngine.classify` inside `clamp_p`).
- `seconds_to_resolution_for(intent) -> 1_000_000_000` — a fixed sentinel **strictly past** `CalibrationConfig.prior_decay_window_seconds` (default `86_400`) so the prior anchor stays active (the prior is only dropped *within* the decay window of resolution). This matches the `seconds_to_resolution=10**9` value used in `CalibrationGate.clamp_p` throughout the test cookbook. The method documents the `MarketRegistry` seam in its docstring.

`StubMarketMeta` takes the `TradeIntent` / `PendingIntent`-shaped object only via attribute access (`.resolution_summary`); it does no I/O, holds no state, and never raises.

**Files:**
- Create: `src/polybot/ers/market_meta.py`
- Test: `tests/test_ers_market_meta.py`

---

- [ ] **Step 1: Write the failing test — `category_for` returns the single "unknown" bucket**

  Create `tests/test_ers_market_meta.py` with the file docstring and the first test.

  ```python
  """StubMarketMeta (S6 / POL-8) — the MVP MarketMeta seam.

  Safety property under test: the stub is intentionally degenerate so that, with no real
  MarketRegistry wired, every proposal lands in ONE "unknown" category bucket (=> the
  CalibrationGate has no resolved forecasts for it => k = 0 => paper-only), the calibration
  anchor reads its question text from the proposal's own resolution_summary, and the
  seconds-to-resolution is a fixed sentinel STRICTLY past the prior-decay window so the prior
  anchor stays active. The real condition_id -> category/question/seconds feed is deferred.
  """
  from decimal import Decimal

  from polybot.ers.intent_store import PendingIntent
  from polybot.ers.market_meta import StubMarketMeta


  def _intent(resolution_summary="Will the incumbent win the 2026 election?"):
      return PendingIntent(
          intent_id="i1", status="PROPOSED", token_id="t1", condition_id="0xabc",
          event_id="e1", side="BUY", target_price=Decimal("0.55"), max_price=Decimal("0.60"),
          size_usd_suggestion=Decimal("10"), p=Decimal("0.7"), p_confidence=Decimal("0.6"),
          resolution_summary=resolution_summary, thesis="thesis text",
          citations=("https://primary/1",), created_at=1,
      )


  def test_category_for_is_single_unknown_bucket():
      meta = StubMarketMeta()
      assert meta.category_for(_intent()) == "unknown"
  ```

- [ ] **Step 2: Run test to verify it fails**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py::test_category_for_is_single_unknown_bucket -v`

  Expected: FAIL with `ModuleNotFoundError: No module named 'polybot.ers.market_meta'` (the module does not exist yet).

- [ ] **Step 3: Write minimal implementation**

  Create `src/polybot/ers/market_meta.py`:

  ```python
  """MarketMeta stub (S6 / POL-8) — the MVP seam for category / question / seconds-to-resolution.

  At MVP there is NO real ``MarketRegistry`` (Gamma metadata) feed (deferred to the
  calibration-warming slice, DESIGN §8). ``HermesPipeline`` injects this stub so the calibration
  arm always has the three inputs ``CalibrationGate`` needs. It is deliberately neutered toward
  safety:

      * ``category_for`` -> a single ``"unknown"`` bucket. ``CalibrationGate.k_for("unknown")``
        has no resolved forecasts, so ``k = 0`` -> paper-only by design. (Do NOT collapse the
        ``k_for`` / ``prior_for`` keyspaces -- DESIGN decision #6.)
      * ``question_text_for`` -> the proposal's own ``resolution_summary`` (the only natural
        language the ERS has at MVP; feeds ``PriorEngine.classify`` inside ``clamp_p``).
      * ``seconds_to_resolution_for`` -> a fixed sentinel STRICTLY past
        ``CalibrationConfig.prior_decay_window_seconds`` (default 86_400) so the prior anchor
        stays active.

    The stub does no I/O, holds no state, and never raises.
    """

  # A fixed sentinel far past CalibrationConfig.prior_decay_window_seconds (default 86_400s = 24h),
  # matching the seconds_to_resolution=10**9 value used in CalibrationGate.clamp_p throughout the
  # tests. "Past the decay window" keeps the prior anchor active (the prior is only dropped WITHIN
  # the decay window of resolution). The real per-market value is the MarketRegistry seam below.
  SECONDS_TO_RESOLUTION_SENTINEL = 1_000_000_000

  # The single MVP category bucket. k_for("unknown") has no resolved history -> k = 0 -> paper-only.
  UNKNOWN_CATEGORY = "unknown"


  class StubMarketMeta:
      """MVP ``MarketMeta``. Replace with a real ``MarketRegistry`` (condition_id ->
      category/question/seconds, from Gamma metadata) in the calibration-warming slice; the three
      method signatures are the seam HermesPipeline depends on."""

      def category_for(self, intent) -> str:
          return UNKNOWN_CATEGORY

      def question_text_for(self, intent) -> str:
          return intent.resolution_summary

      def seconds_to_resolution_for(self, intent) -> int | None:
          # MarketRegistry seam: a real impl returns (resolution_ts - now) per condition_id from
          # Gamma metadata. The stub returns a fixed sentinel past the prior-decay window.
          return SECONDS_TO_RESOLUTION_SENTINEL
  ```

- [ ] **Step 4: Run test to verify it passes**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py::test_category_for_is_single_unknown_bucket -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add src/polybot/ers/market_meta.py tests/test_ers_market_meta.py
  git commit -m "feat(ers): add StubMarketMeta category bucket (S6/POL-8)"
  ```

---

- [ ] **Step 6: Write the failing test — `question_text_for` returns `intent.resolution_summary` verbatim**

  Add to `tests/test_ers_market_meta.py`:

  ```python
  def test_question_text_for_returns_resolution_summary_verbatim():
      meta = StubMarketMeta()
      summary = "Will the Fed hold rates unchanged at the March meeting?"
      assert meta.question_text_for(_intent(resolution_summary=summary)) == summary


  def test_question_text_for_passes_through_empty_summary():
      # resolution_summary defaults to "" upstream; the stub must not substitute or raise.
      meta = StubMarketMeta()
      assert meta.question_text_for(_intent(resolution_summary="")) == ""
  ```

- [ ] **Step 7: Run tests to verify they pass**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py::test_question_text_for_returns_resolution_summary_verbatim tests/test_ers_market_meta.py::test_question_text_for_passes_through_empty_summary -v`

  Expected: PASS (no implementation change — `question_text_for` already returns `intent.resolution_summary`; these tests pin the verbatim-passthrough contract and the empty-string edge case).

- [ ] **Step 8: Commit**

  ```bash
  git add tests/test_ers_market_meta.py
  git commit -m "test(ers): pin StubMarketMeta question_text passthrough (S6/POL-8)"
  ```

---

- [ ] **Step 9: Write the failing test — `seconds_to_resolution_for` returns the sentinel, strictly past the prior-decay window**

  Add to `tests/test_ers_market_meta.py`:

  ```python
  from polybot.calibration.config import CalibrationConfig
  from polybot.ers.market_meta import SECONDS_TO_RESOLUTION_SENTINEL


  def test_seconds_to_resolution_for_returns_sentinel():
      meta = StubMarketMeta()
      assert meta.seconds_to_resolution_for(_intent()) == SECONDS_TO_RESOLUTION_SENTINEL


  def test_sentinel_is_strictly_past_prior_decay_window():
      # The prior anchor is dropped only WITHIN the decay window of resolution; the sentinel must
      # sit strictly OUTSIDE it so the prior stays active (DESIGN §6). Guard against a future
      # CalibrationConfig default change silently swallowing the prior.
      cfg = CalibrationConfig()
      assert SECONDS_TO_RESOLUTION_SENTINEL > cfg.prior_decay_window_seconds


  def test_seconds_to_resolution_for_is_a_positive_int():
      meta = StubMarketMeta()
      secs = meta.seconds_to_resolution_for(_intent())
      assert isinstance(secs, int) and secs > 0
  ```

- [ ] **Step 10: Run tests to verify they pass**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py::test_seconds_to_resolution_for_returns_sentinel tests/test_ers_market_meta.py::test_sentinel_is_strictly_past_prior_decay_window tests/test_ers_market_meta.py::test_seconds_to_resolution_for_is_a_positive_int -v`

  Expected: PASS (`SECONDS_TO_RESOLUTION_SENTINEL = 1_000_000_000` is `> 86_400`).

- [ ] **Step 11: Commit**

  ```bash
  git add tests/test_ers_market_meta.py
  git commit -m "test(ers): assert StubMarketMeta sentinel outranks prior-decay window (S6/POL-8)"
  ```

---

- [ ] **Step 12: Write the failing test — stub is stateless / pure (same intent identity, repeatable answers, no I/O)**

  Add to `tests/test_ers_market_meta.py`:

  ```python
  def test_stub_is_stateless_and_repeatable():
      # No registry, no caching, no mutation: two calls on two fresh instances agree, and a
      # second call on the same instance is identical (the seam must be side-effect free).
      a, b = StubMarketMeta(), StubMarketMeta()
      i = _intent()
      assert a.category_for(i) == b.category_for(i) == "unknown"
      assert a.question_text_for(i) == a.question_text_for(i) == i.resolution_summary
      assert a.seconds_to_resolution_for(i) == b.seconds_to_resolution_for(i)
  ```

- [ ] **Step 13: Run test to verify it passes**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py::test_stub_is_stateless_and_repeatable -v`

  Expected: PASS.

- [ ] **Step 14: Run the whole new test file plus the full suite to confirm no regression**

  Run: `./.venv/bin/pytest tests/test_ers_market_meta.py -v`

  Expected: PASS (all 7 tests in the file green).

  Run: `./.venv/bin/pytest`

  Expected: PASS — the prior 377 tests stay green (this task only adds a new module + new test file; it touches no existing source).

- [ ] **Step 15: Commit**

  ```bash
  git add tests/test_ers_market_meta.py
  git commit -m "test(ers): assert StubMarketMeta is stateless and pure (S6/POL-8)"
  ```

---

## Task 8: `process_pending` S6 wiring (`HermesPipeline`)

This task MODIFIES `src/polybot/ers/service.py` so that, when an optional `pipeline=HermesPipeline(...)` is supplied, each pending intent runs the full S6 re-derivation chain from DESIGN §2/§3 (detector veto → citation truth-gate → fusion → anchor clamp → forecast/component logging → per-intent calibration `k` → unchanged validator → record → paper place+fold). When `pipeline is None` the loop is **byte-for-byte the slice-3 path** (the 377 existing tests stay green). The breaker block, the per-intent `try/except → internal_error`, the `record_decision`-after-every-Decision rule, and the ACCEPT `place`-then-`_fold` ordering are all preserved.

This task depends on the symbols created in Tasks 1–7 of this plan (all pinned in the brief):
`polybot.fusion.engine.{FusionConfig,FusionResult,FusionError,fuse}`, `polybot.fusion.component_log.ComponentLog`, `polybot.truthgate.gate.{TruthGateConfig,TruthVerdict,verify,REASON_TRUTH_GATE_REFUSE,REASON_SAME_SOURCE}`, `polybot.detectors.orchestrator.{DetectorInputs,DetectorVerdict,DetectorOrchestrator,REASON_DETECTOR_AVOID}`, `polybot.ers.market_meta.StubMarketMeta`, `polybot.ers.facade.ProposeOnlyFacade`. The unit tests below stub the cross-module collaborators with tiny local fakes (the established per-file builder pattern — no `conftest.py`), so Task 8 can be developed and run **independently of the order Tasks 1–7 land**; the Task 9 end-to-end test then exercises the real units together.

**Files:**
- Modify: `src/polybot/ers/service.py` (add `HermesPipeline`, the `pipeline=` kwarg, the pipeline branch; extend `_to_trade_intent` with `p_override`, `_cluster_view` with `cluster_id_of`)
- Test: `tests/test_ers_service.py` (append new cases; existing 11 cases are the `pipeline=None` regression coverage)

---

### Task 8a: `pipeline=None` is exactly the slice-3 path (regression guard)

- [ ] **Step 1: Write the failing test** — pin that adding the `pipeline` kwarg with its `None` default does not change any slice-3 behavior. (Append to `tests/test_ers_service.py`.)

```python
# --- S6: HermesPipeline wiring ---------------------------------------------------------------
# These reuse the module-level _book / _P / _store helpers already defined at the top of this file.

def test_pipeline_none_is_exactly_the_slice3_accept_path(tmp_path):
    # The S6 seam is purely additive: with pipeline omitted (None), process_pending behaves
    # identically to slice-3 -- the i1 ACCEPT, $12 per_trade stake, paper place, and fold all hold.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, pipeline=None)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_none_is_exactly_the_slice3_accept_path -v`
  - Expected: FAIL with `TypeError: process_pending() got an unexpected keyword argument 'pipeline'`.

- [ ] **Step 3: Write minimal implementation** — add the `HermesPipeline` dataclass and the `pipeline=None` kwarg, dispatching to the existing body when `pipeline is None`. Replace the **entire** body of `src/polybot/ers/service.py` with the version below. (The slice-3 logic is preserved verbatim inside `_process_intent_slice3`; the only new code paths are the dataclass, the kwarg, the `if pipeline is None` dispatch, and the `p_override`/`cluster_id_of` seams.)

```python
"""ERS service poll-loop (S3 / POL-5 slices 2 + 3; S6 / POL-8 HermesPipeline wiring).

Wires the chokepoint to the validator + the safety breaker, and -- when a HermesPipeline is
supplied -- to the full S6 re-derivation chain (defensive detectors, citation truth-gate, signal
fusion, anchor clamp, per-intent calibration k, forecast + per-signal component logging). The ERS
is the ONLY component that ever signs -- never Hermes. Hermes can at worst enqueue a PROPOSED row
through ProposeOnlyFacade; this loop independently re-derives price, size, caps, corroboration, and
the anchored posterior. The signer here is a paper stub; the real signer is S2/POL-4.

S6 contract (DESIGN-S6-HERMES.md §2/§3): pipeline=None -> behavior is EXACTLY slice-3 (the 377
existing tests stay green). pipeline supplied -> steps 1-11 of §2 engage and calib_score is IGNORED
in favor of the per-intent k = pipeline.calib_gate.k_for(category).
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.ers.breaker import FLATTEN, FREEZE_ADDS
from polybot.ers.validator import (
    ClusterView,
    Decision,
    OpenPosition,
    Portfolio,
    TradeIntent,
    evaluate_intent,
)
from polybot.fusion.engine import FusionError
from polybot.truthgate.gate import REASON_SAME_SOURCE, REASON_TRUTH_GATE_REFUSE
from polybot.detectors.orchestrator import DetectorInputs, REASON_DETECTOR_AVOID

_COLD = ClusterView(warm=False, rho=None)  # fail-closed default when no co-move model is wired

# New S6 Decision.reason codes (free-form strings; NO validator change -- DESIGN §6).
REASON_ANCHOR_ERROR = "anchor_error"


@dataclass(frozen=True)
class HermesPipeline:
    """The S6 re-derivation context (DESIGN §3). Optional, defaulting None in process_pending -- the
    same additive-seam pattern as cluster_model / breaker. When provided, the per-intent k from
    calib_gate.k_for(category) supersedes the batch calib_score (which is retained for back-compat)."""
    calib_gate: object            # CalibrationGate: k_for(category) -> Decimal{0,1}; clamp_p(...) -> AnchorResult
    fusion_config: object         # fusion.engine.FusionConfig
    truth_gate_config: object     # truthgate.gate.TruthGateConfig
    detectors: object             # detectors.orchestrator.DetectorOrchestrator
    forecast_ledger: object       # calibration.ledger.ForecastLedger
    component_log: object         # fusion.component_log.ComponentLog
    market_meta: object           # ers.market_meta.StubMarketMeta (the MarketRegistry seam)
    allowlist: object             # iterable of ingestion.news.Source (truth-gate independence surface)
    event_store: object           # storage.market_memory.EventStore (sanitized citations only)
    stamper: object               # the ONE shared core.clock.MonotonicStamper (now_ns for the gate)


def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None):
    """Process every PROPOSED intent in FIFO order; return the updated portfolio.

    Runs the L7 breaker FIRST (when wired): FLATTEN signals the exit + blocks adds (l7_flatten),
    FREEZE_ADDS blocks adds (l7_freeze). Each surviving intent is processed inside a per-intent
    try/except so one malformed intent can't wedge the FIFO queue. On ACCEPT the signer is called
    THEN the portfolio is folded before the next intent (the cross-intent caps contract). When
    pipeline is None this is exactly slice-3; when supplied, the S6 chain engages."""
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
                decision, trade_intent = _process_intent_slice3(
                    intent, book_for, portfolio, caps, calib_score, cluster_model)
            else:
                decision, trade_intent = _process_intent_pipeline(
                    intent, book_for, portfolio, caps, cluster_model, pipeline)
        except Exception:
            # One malformed intent must not wedge the FIFO queue head: fail it closed + audit,
            # and keep processing the rest.
            decision = Decision("REJECT", None, None, "internal_error")
            trade_intent = None
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision)
    return portfolio


def _process_intent_slice3(intent, book_for, portfolio, caps, calib_score, cluster_model):
    """The unchanged slice-3 per-intent path (pipeline=None). Returns (decision, trade_intent)."""
    cluster = _cluster_view(cluster_model, intent, portfolio)
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm)
    book = book_for(trade_intent.token_id)
    if book is None:
        # No live book to re-price against -> fail closed (never size off the proposal).
        return Decision("REJECT", None, None, "no_book"), trade_intent
    decision = evaluate_intent(trade_intent, book, portfolio, caps,
                               calib_score=calib_score, cluster=cluster)
    return decision, trade_intent


def _process_intent_pipeline(intent, book_for, portfolio, caps, cluster_model, pipeline):
    """The S6 per-intent chain (DESIGN §2 steps 1-11). Returns (decision, trade_intent).

    Order is load-bearing: cheap/structural refusals (no_book, detector_avoid, truth-gate) come
    BEFORE any genuine estimate, so a refused proposal records NO forecast (DESIGN §2). A clean
    estimate records a forecast + per-signal components BEFORE evaluate_intent, so a SKIP on k=0
    still logs the estimate -- calibration grades estimates, not execution."""
    from polybot.fusion.engine import fuse  # local import keeps the module import-light + cycle-free
    from polybot.truthgate.gate import verify as truth_verify

    cluster = _cluster_view(cluster_model, intent, portfolio)
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm)

    # 1. Single live book re-fetch, shared by truth-gate / fusion / anchor / evaluate_intent.
    book = book_for(trade_intent.token_id)
    if book is None:
        return Decision("REJECT", None, None, "no_book"), trade_intent

    # 2. Defensive detector pre-gate (FOLLOW off). AVOID -> REJECT before any sizing.
    verdict = pipeline.detectors.evaluate(intent, inputs=DetectorInputs())
    if verdict.action == "AVOID":
        return Decision("REJECT", None, None, REASON_DETECTOR_AVOID), trade_intent

    # 3. Citation truth-gate over the sanitized EventStore + the live book (never fetches a URL).
    truth = truth_verify(intent.citations, event_store=pipeline.event_store, book=book,
                         allowlist=pipeline.allowlist, now_ns=pipeline.stamper.stamp(),
                         config=pipeline.truth_gate_config)
    if truth.refused:
        return Decision("REJECT", None, None, truth.reason), trade_intent

    # 4. Fusion prior + anchor reference is the live mid; degenerate -> book_stale.
    mid = book.midpoint()
    if mid is None:
        return Decision("REJECT", None, None, "book_stale"), trade_intent

    # 5. Weighted log-odds fusion. Hermes's p enters ONLY as p_news, w_news live iff corroborated.
    #    p_base/p_micro/p_flow are ERS-derived; at MVP p_base = mid (no base-rate model wired here
    #    beyond the anchor's prior), p_micro/p_flow carry zero weight (logged, not weighted).
    fusion_result = fuse(mid, p_news=intent.p, p_base=mid, p_micro=mid,
                         p_flow=verdict.p_flow if Decimal(0) < verdict.p_flow < Decimal(1) else mid,
                         corroborated=truth.corroborated, config=pipeline.fusion_config)

    # 6. Anchor clamp, wrapped so a non-finite anchor maps to a DISTINCT anchor_error (not internal).
    category = pipeline.market_meta.category_for(intent)
    question_text = pipeline.market_meta.question_text_for(intent)
    seconds = pipeline.market_meta.seconds_to_resolution_for(intent)
    try:
        anchor = pipeline.calib_gate.clamp_p(
            fusion_result.p_final, mid, question_text=question_text,
            seconds_to_resolution=seconds, corroborated=truth.corroborated)
    except (ValueError, FusionError):
        return Decision("REJECT", None, None, REASON_ANCHOR_ERROR), trade_intent
    p_clamped = anchor.p_clamped

    # 7. Record the genuine estimate: forecast (the calibration substrate) + per-signal components.
    #    The recorded p is the in-range p_clamped, so the ledger's [0,1] guard always passes.
    forecast_id = intent.intent_id
    pipeline.forecast_ledger.record_forecast(
        forecast_id, category=category, condition_id=intent.condition_id,
        p=p_clamped, market_mid=mid)
    components = fusion_result.components
    pipeline.component_log.record(
        forecast_id, p_news=components["p_news"], p_base=components["p_base"],
        p_micro=components["p_micro"], p_flow=components["p_flow"],
        w_news_effective=fusion_result.w_news_effective, corroborated=truth.corroborated, mid=mid)

    # 8. Per-intent calibration k (Decimal{0,1}); supersedes the batch calib_score. k=0 -> paper-only.
    k = pipeline.calib_gate.k_for(category)

    # 9-11. Substitute the anchored posterior into the TradeIntent and size with the UNCHANGED
    #        validator (calib_score=k). evaluate_intent / validator dataclasses are untouched.
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm, p_override=p_clamped)
    decision = evaluate_intent(trade_intent, book, portfolio, caps, calib_score=k, cluster=cluster)
    return decision, trade_intent


def _cluster_view(cluster_model, intent, portfolio, *, cluster_id_of=None):
    """The learned co-move verdict for this intent's cluster. A None model -> fail-closed cold. The
    cluster spans the intent's token + every open position sharing its cluster_id.

    cluster_id_of is a one-line PLUGGABLE hook (Fork 8C): it defaults to ``intent.event_id`` (the
    slice-2/3 placeholder that fails SAFE -- over-couples within an event), so the real latent-cluster
    slice swaps the function without re-editing the loop. Do not mistake this alias for the final
    cluster taxonomy."""
    if cluster_model is None:
        return _COLD
    if cluster_id_of is None:
        cluster_id_of = lambda i: i.event_id
    cluster_id = cluster_id_of(intent)
    tokens = [intent.token_id]
    tokens += [p.token_id for p in portfolio.positions if p.cluster_id == cluster_id]
    return cluster_model.view(tokens)


def _to_trade_intent(intent, *, matrix_cold, p_override=None):
    # The ERS populates the risk keys (NOT Hermes-trusted). resolution_source + cluster_id come
    # from the proposal's ids (slice-2 placeholders); matrix_cold is driven by the co-move
    # ClusterView. p_override (the fused+anchored posterior, S6) substitutes intent.p before the
    # validator sizes -- so the validator never sizes off Hermes's raw p when the pipeline is active.
    return TradeIntent(
        token_id=intent.token_id, condition_id=intent.condition_id, event_id=intent.event_id,
        resolution_source=intent.condition_id, cluster_id=intent.event_id,
        p=intent.p if p_override is None else p_override,
        max_price=intent.max_price, size_usd_suggestion=intent.size_usd_suggestion,
        matrix_cold=matrix_cold,
    )


def _fold(portfolio, trade_intent, decision):
    pos = OpenPosition(
        condition_id=trade_intent.condition_id, event_id=trade_intent.event_id,
        resolution_source=trade_intent.resolution_source, cluster_id=trade_intent.cluster_id,
        worst_case_risk=decision.stake_usd, matrix_cold=trade_intent.matrix_cold,
        token_id=trade_intent.token_id, entry_price=decision.price_exec, frozen=False,
    )
    return Portfolio(nav=portfolio.nav, positions=portfolio.positions + (pos,))


class PaperSigner:
    """Signer-seam stub: records the orders the ERS WOULD place (shadow) and the FLATTEN exits the
    L7 breaker WOULD signal -- no keys or network, so the loop runs end-to-end in shadow (S9). The
    real Rust signer + real venue de-risking replace it."""

    def __init__(self):
        self.placed = []
        self.flattened = []

    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

    def flatten(self, positions):
        # Shadow: record which positions the breaker asked to exit. Real venue de-risking
        # (GTD brackets / cancelAll) is S2/POL-4 + S4.
        self.flattened.append(tuple(p.token_id for p in positions))
```

> Note: `PaperSigner` is moved below the functions but its definition is unchanged. If you prefer to keep it at the top, leave it there — only the imports, `HermesPipeline`, `process_pending`, `_process_intent_slice3`, `_process_intent_pipeline`, `_cluster_view`, and `_to_trade_intent` are new/changed. The existing test file imports `from polybot.ers.service import PaperSigner, process_pending`, so its module-level position does not matter.

- [ ] **Step 4: Run test to verify it passes** (and re-run the whole pre-existing slice-3 suite)
  - Run: `./.venv/bin/pytest tests/test_ers_service.py -v`
  - Expected: PASS — the new `pipeline=None` test plus all 11 pre-existing slice-2/3 cases stay green (the additive seam claim).

- [ ] **Step 5: Commit**
  - `git add src/polybot/ers/service.py tests/test_ers_service.py && git commit -m "feat(ers): add HermesPipeline seam to process_pending (pipeline=None == slice-3) [S6/POL-8]"`

---

For the next cycles, add these shared local fakes + builders to `tests/test_ers_service.py` (the per-file builder pattern; no `conftest.py`). They let Task 8 unit-test the wiring independently of the real fusion/truth-gate/detector/market-meta modules.

```python
# --- S6 local fakes + builders (the wiring-under-test calls these collaborators) -------------
from polybot.ers.service import HermesPipeline
from polybot.fusion.component_log import ComponentLog


class _FakeTruthGate:
    """Stand-in for truthgate.gate.verify, injected via a fake config object's .verify? No --
    the real verify() is module-level. We instead monkeypatch service.truth_verify per test."""


class _Verdict:
    def __init__(self, refused, reason, corroborated):
        self.refused = refused
        self.reason = reason
        self.corroborated = corroborated
        self.primary_groups = ()


class _DetectorVerdict:
    def __init__(self, action="FLAG_ONLY", p_flow=Decimal("0")):
        self.action = action
        self.pull_quotes = False
        self.p_flow = p_flow
        self.reasons = ()


class _FakeDetectors:
    def __init__(self, verdict=None):
        self._verdict = verdict or _DetectorVerdict()
        self.calls = []

    def evaluate(self, intent, *, inputs):
        self.calls.append((intent.intent_id, inputs))
        return self._verdict


class _FakeFusionResult:
    def __init__(self, p_final, components, w_news_effective):
        self.p_final = p_final
        self.components = components
        self.w_news_effective = w_news_effective


class _FakeCalibGate:
    """k_for returns a fixed k (Decimal); clamp_p returns a fake AnchorResult or raises (anchor_error)."""
    def __init__(self, *, k=Decimal("0"), clamp_to=None, raises=None):
        self._k = k
        self._clamp_to = clamp_to
        self._raises = raises
        self.clamp_calls = []

    def k_for(self, category):
        return self._k

    def clamp_p(self, p, market_mid, *, question_text, seconds_to_resolution, corroborated):
        self.clamp_calls.append((p, market_mid, corroborated))
        if self._raises is not None:
            raise self._raises
        target = p if self._clamp_to is None else self._clamp_to
        return _AnchorResult(target)


class _AnchorResult:
    def __init__(self, p_clamped):
        self.p_clamped = p_clamped
        self.shrunk = False
        self.reason = "within_band"


class _StubMeta:
    def __init__(self, category="unknown", seconds=10**12):
        self._cat = category
        self._secs = seconds

    def category_for(self, intent):
        return self._cat

    def question_text_for(self, intent):
        return intent.resolution_summary

    def seconds_to_resolution_for(self, intent):
        return self._secs


def _pipeline(tmp_path, monkeypatch, *, detectors=None, truth=None, calib=None, meta=None,
              fusion_result=None):
    """Build a HermesPipeline with fakes, monkeypatching the two module-level collaborators
    (service.fuse via the local import, and truthgate.gate.verify) so we drive the loop precisely."""
    from polybot.core.clock import MonotonicStamper
    from polybot.calibration.ledger import ForecastLedger

    stamper = MonotonicStamper(clock=lambda: 1)  # deterministic; the stamper itself enforces strict-mono
    ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
    clog = ComponentLog(stamper=stamper)

    # Patch the truth-gate import target used inside _process_intent_pipeline.
    import polybot.truthgate.gate as gate_mod
    monkeypatch.setattr(gate_mod, "verify",
                        lambda *a, **k: truth or _Verdict(False, None, True), raising=True)
    # Patch the fusion fuse() the same way (local import resolves to fusion.engine.fuse).
    import polybot.fusion.engine as fusion_mod
    fr = fusion_result or _FakeFusionResult(
        Decimal("0.70"),
        {"p_news": Decimal("0.9"), "p_base": Decimal("0.5"),
         "p_micro": Decimal("0.5"), "p_flow": Decimal("0.5")},
        0.20)
    monkeypatch.setattr(fusion_mod, "fuse", lambda *a, **k: fr, raising=True)

    pipe = HermesPipeline(
        calib_gate=calib or _FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.70")),
        fusion_config=object(),
        truth_gate_config=object(),
        detectors=detectors or _FakeDetectors(),
        forecast_ledger=ledger,
        component_log=clog,
        market_meta=meta or _StubMeta(),
        allowlist=(),
        event_store=object(),
        stamper=stamper,
    )
    return pipe, ledger, clog
```

> The two `monkeypatch.setattr` targets (`polybot.truthgate.gate.verify` and `polybot.fusion.engine.fuse`) match the `from ... import verify as truth_verify` and `from ... import fuse` performed **inside** `_process_intent_pipeline`. Because those imports are local to the function, patching the source module is what the loop sees on each call. `ComponentLog`, `ForecastLedger`, `MonotonicStamper` are the **real** units here (cheap, in-`tmp_path`), so the forecast/component-logging assertions test the real append path.

---

### Task 8b: detector AVOID rejects before any sizing

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_detector_avoid_rejects_before_sizing(tmp_path, monkeypatch):
    # A defensive detector AVOID verdict must REJECT(detector_avoid) BEFORE fusion/clamp/sizing,
    # and place no order. (calib_gate.clamp_p is never reached -> no clamp call recorded.)
    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch,
                                   detectors=_FakeDetectors(_DetectorVerdict(action="AVOID")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "detector_avoid"
        assert signer.placed == []
        assert pipe.calib_gate.clamp_calls == []   # never sized -- rejected before fusion/clamp
        assert ledger.all() == []                  # not a genuine estimate -> no forecast logged
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_detector_avoid_rejects_before_sizing -v`
  - Expected: PASS already if Task 8a's `_process_intent_pipeline` implemented step 2 — but FAIL if the detector branch were missing. (The implementation in 8a already includes it; this test pins the ordering and that no forecast is logged.)

- [ ] **Step 3: Write minimal implementation** — already present in `_process_intent_pipeline` (step 2: `if verdict.action == "AVOID": return Decision("REJECT", None, None, REASON_DETECTOR_AVOID)`). No code change needed for this cycle; the test exercises that branch and the "no forecast on a non-estimate" rule.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_detector_avoid_rejects_before_sizing -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin detector AVOID -> REJECT detector_avoid before sizing [S6/POL-8]"`

---

### Task 8c: truth-gate refuse → REJECT, signer never called, no forecast

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_truth_gate_same_source_collusion_rejects_no_signer_no_forecast(tmp_path, monkeypatch):
    # An injection signature (truth-gate refuses with same_source_collusion) must REJECT, never
    # reach the signer, and record NO forecast (refused evidence is not a genuine estimate).
    from polybot.truthgate.gate import REASON_SAME_SOURCE
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        truth=_Verdict(refused=True, reason=REASON_SAME_SOURCE, corroborated=False))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "same_source_collusion"
        assert signer.placed == []
        assert pipe.calib_gate.clamp_calls == []
        assert ledger.all() == []
        assert clog.all() == ()


def test_pipeline_truth_gate_refuse_maps_truth_gate_refuse_reason(tmp_path, monkeypatch):
    # Zero allowlisted primaries -> truth_gate_refuse (distinct from same_source_collusion).
    from polybot.truthgate.gate import REASON_TRUTH_GATE_REFUSE
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        truth=_Verdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE, corroborated=False))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").decision_reason == "truth_gate_refuse"
        assert signer.placed == [] and ledger.all() == []
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py -k truth_gate -v`
  - Expected: PASS if 8a's step 3 is present (the `if truth.refused: return Decision("REJECT", None, None, truth.reason)` branch). These pin the reason-passthrough and the no-forecast/no-signer guarantees.

- [ ] **Step 3: Write minimal implementation** — already present in `_process_intent_pipeline` (step 3). No code change.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py -k truth_gate -v`
  - Expected: PASS (both cases).

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin truth-gate refuse -> REJECT, no signer, no forecast [S6/POL-8]"`

---

### Task 8d: `clamp_p` raising → REJECT `anchor_error` (NOT internal_error)

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_clamp_p_raise_maps_to_distinct_anchor_error(tmp_path, monkeypatch):
    # A non-finite anchor makes calib_gate.clamp_p raise ValueError. It MUST be caught explicitly
    # and mapped to the DISTINCT reason "anchor_error" -- never swallowed into "internal_error".
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        calib=_FakeCalibGate(k=Decimal("0"), raises=ValueError("anchor_gate: non-finite p")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "anchor_error"  # NOT "internal_error"
        assert signer.placed == []
        assert ledger.all() == []   # raised before record_forecast -> no estimate logged
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_clamp_p_raise_maps_to_distinct_anchor_error -v`
  - Expected: PASS if 8a's `try/except (ValueError, FusionError) -> REJECT anchor_error` (step 6) is present. If the except were missing or only the outer `except Exception` handled it, this would FAIL with `decision_reason == "internal_error"`.

- [ ] **Step 3: Write minimal implementation** — already present in `_process_intent_pipeline` (the explicit `try/except (ValueError, FusionError)` wrapping `clamp_p`, returning `REASON_ANCHOR_ERROR`). No code change.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_clamp_p_raise_maps_to_distinct_anchor_error -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin clamp_p raise -> distinct anchor_error reason [S6/POL-8]"`

---

### Task 8e: the fused+clamped posterior is what the validator sizes off

- [ ] **Step 1: Write the failing test** — substitute a strong fused/clamped `p` that lets the validator ACCEPT with a positive Kelly stake, proving `_to_trade_intent(p_override=...)` feeds the validator the posterior, not `intent.p`.

```python
def test_pipeline_substitutes_fused_clamped_p_into_the_validator(tmp_path, monkeypatch):
    # Proposal's raw p=0.50 (== price -> no edge). The pipeline fuses+clamps to 0.90, which the
    # validator sizes off -> ACCEPT (not the SKIP no_edge the raw p would give). Pin that the
    # posterior, not Hermes's raw p, drove the validator. Use k=1 so sizing isn't zeroed.
    fr = _FakeFusionResult(Decimal("0.90"),
                           {"p_news": Decimal("0.95"), "p_base": Decimal("0.50"),
                            "p_micro": Decimal("0.50"), "p_flow": Decimal("0.50")}, 0.20)
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch, fusion_result=fr,
        calib=_FakeCalibGate(k=Decimal("1"), clamp_to=Decimal("0.90")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, p="0.50"))  # raw p == 0.50 == price -> would be no_edge
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, pipeline=pipe)

        assert store.get("i1").status == "ACCEPTED"            # posterior 0.90 has edge over price 0.50
        assert store.get("i1").decision_stake_usd == Decimal("12")  # per_trade cap binds at k=1
        assert pipe.calib_gate.clamp_calls[0][0] == Decimal("0.90")  # fused p_final fed to clamp_p
        assert len(final.positions) == 1
        # the forecast records the clamped posterior, not the raw 0.50
        assert ledger.get("i1").p == Decimal("0.90")
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_substitutes_fused_clamped_p_into_the_validator -v`
  - Expected: PASS if 8a's `_to_trade_intent(..., p_override=p_clamped)` substitution (step 9) is present. If `p_override` were dropped, the validator would size off raw `p=0.50` and SKIP `no_edge`, FAILing this assertion.

- [ ] **Step 3: Write minimal implementation** — already present (the `_to_trade_intent` `p_override` param + the pipeline passing `p_override=p_clamped`). No code change.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_substitutes_fused_clamped_p_into_the_validator -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin fused+clamped posterior substituted into validator [S6/POL-8]"`

---

### Task 8f: forecast + components recorded even when k=0 SKIPs

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_records_forecast_and_components_even_when_k0_skips(tmp_path, monkeypatch):
    # k=0 -> frac_eff=0 -> stake below floor -> SKIP(below_min_floor). The estimate is STILL a
    # genuine forecast, so record_forecast + ComponentLog.record happen BEFORE evaluate_intent --
    # calibration grades the estimate, not whether we could afford to act on it (DESIGN §2).
    fr = _FakeFusionResult(Decimal("0.80"),
                           {"p_news": Decimal("0.90"), "p_base": Decimal("0.50"),
                            "p_micro": Decimal("0.50"), "p_flow": Decimal("0.50")}, 0.20)
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch, fusion_result=fr,
        calib=_FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.80")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "SKIPPED"
        assert store.get("i1").decision_reason == "below_min_floor"  # k=0 zeroes the stake
        assert signer.placed == []
        # estimate logged regardless of the SKIP:
        rec = ledger.get("i1")
        assert rec is not None and rec.p == Decimal("0.80") and rec.category == "unknown"
        assert rec.market_mid == Decimal("0.255")  # midpoint of bid 0.01 / ask 0.50
        comps = clog.all()
        assert len(comps) == 1  # one per-signal row logged
```

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_records_forecast_and_components_even_when_k0_skips -v`
  - Expected: PASS if 8a's step 7 (record_forecast + component_log.record) sits BEFORE evaluate_intent. If recording were placed after / only on ACCEPT, the ledger/component assertions would FAIL.

- [ ] **Step 3: Write minimal implementation** — already present (step 7 of `_process_intent_pipeline`, ordered before `evaluate_intent`). No code change.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_records_forecast_and_components_even_when_k0_skips -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin forecast+components logged before validator even when k=0 SKIPs [S6/POL-8]"`

---

### Task 8g: corroboration flips `w_news` — corroborated vs uncorroborated paths

- [ ] **Step 1: Write the failing test** — assert the loop threads the truth-gate's `corroborated` flag into both `fuse()` and `clamp_p()` (the single key that unlocks `w_news` and widens the anchor band). Here we use the **real** `fuse` (not the fake) by passing a real `FusionConfig`, and assert the recorded `w_news_effective` in the component log flips with corroboration.

```python
def test_pipeline_corroboration_threads_into_fusion_and_anchor(tmp_path, monkeypatch):
    # Real FusionEngine.fuse this time (un-patch it). corroborated=True -> w_news_effective=0.20;
    # corroborated=False -> w_news_effective=0.0 (Hermes informational-only). The same corroborated
    # bool also reaches clamp_p (anchor band width). Pin both via the ComponentLog + clamp_calls.
    from polybot.fusion.engine import FusionConfig
    import polybot.fusion.engine as fusion_mod

    def _run(corroborated):
        pipe, ledger, clog = _pipeline(
            tmp_path, monkeypatch,
            truth=_Verdict(refused=False, reason=None, corroborated=corroborated),
            calib=_FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.50")))
        # un-patch fuse: use the REAL fold so w_news_effective is genuinely derived.
        monkeypatch.setattr(fusion_mod, "fuse", _real_fuse_capture(pipe), raising=True)
        with _store(str(tmp_path / f"i_{corroborated}.db")) as store:
            store.propose_trade("i1", **dict(_P, p="0.95"))
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=PaperSigner(), pipeline=pipe)
        return clog, pipe

    clog_t, pipe_t = _run(True)
    clog_f, pipe_f = _run(False)
    # w_news_effective recorded in the component log flips with corroboration:
    assert clog_t.all()[0].w_news_effective == 0.20
    assert clog_f.all()[0].w_news_effective == 0.0
    # the same corroborated bool reaches clamp_p:
    assert pipe_t.calib_gate.clamp_calls[0][2] is True
    assert pipe_f.calib_gate.clamp_calls[0][2] is False


def _real_fuse_capture(pipe):
    # Rebind the pipeline's fusion_config to a real FusionConfig and call the real fuse(), so the
    # w_news gating is genuinely exercised (not a fake constant).
    from polybot.fusion.engine import FusionConfig, fuse as real_fuse
    cfg = FusionConfig(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)
    object.__setattr__(pipe, "fusion_config", cfg)
    return lambda mid, **kw: real_fuse(mid, **{**kw, "config": cfg})
```

> `ComponentLog.all()` returns a tuple of rows; the test reads `.w_news_effective` off the first row. If the concrete row shape differs (e.g. a dict or namedtuple), adjust the accessor to match the `ComponentLog` built in Task 6 — the contract guarantees `w_news_effective` is recorded. `object.__setattr__` is used because `HermesPipeline` is a frozen dataclass.

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_corroboration_threads_into_fusion_and_anchor -v`
  - Expected: PASS if 8a passes `corroborated=truth.corroborated` to both `fuse(...)` and `clamp_p(...)`. If either threading were missing, the `w_news_effective` flip or the `clamp_calls[...][2]` flag would FAIL.

- [ ] **Step 3: Write minimal implementation** — already present (the pipeline passes `corroborated=truth.corroborated` to both `fuse` and `clamp_p`, and logs `fusion_result.w_news_effective`). No code change.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py::test_pipeline_corroboration_threads_into_fusion_and_anchor -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_service.py && git commit -m "test(ers): pin corroboration flips w_news + threads to anchor [S6/POL-8]"`

---

### Task 8h: full S6 unit suite + the 377 regression stay green

- [ ] **Step 1: Run the whole service file + the full suite**
  - Run: `./.venv/bin/pytest tests/test_ers_service.py -v`
  - Run: `./.venv/bin/pytest`
  - Expected: PASS — all new `pipeline=...` cases green AND every pre-existing test green (the additive-seam acceptance criterion: 377 + the new S6 cases).

- [ ] **Step 2: Commit (no-op if clean)**
  - `git add -A && git commit -m "test(ers): S6 process_pipeline unit suite green; full regression intact [S6/POL-8]" || echo "nothing to commit"`

---

## Task 9: end-to-end shadow test (`ProposeOnlyFacade` → `process_pending(pipeline=...)` → `PaperSigner`)

The acceptance test from DESIGN §9.3: drive synthetic Hermes proposals through the **real** `ProposeOnlyFacade.propose_trade` (the only write surface) → `process_pending(pipeline=...)` on a `PaperSigner`, exercising the **real** `FusionEngine`, `ComponentLog`, `CalibrationGate`/`ForecastLedger`/`PriorEngine`/`CalibrationConfig`, `StubMarketMeta`, `DetectorOrchestrator`, and `CitationTruthGate` together, asserting the four DESIGN §9 scenarios. The only fakes are inputs we cannot synthesize without a live feed (the `EventStore` is seeded with real `Envelope`s for the truth-gate). `category="unknown"` → `k=0`, so clean flows SKIP (paper-only) by design — and that is asserted, not worked around.

**Files:**
- Test: `tests/test_ers_hermes_e2e.py` (new file — a full-pipeline integration test, distinct from the unit-level `test_ers_service.py`)

### Task 9a: scenario (a) — clean corroborated proposal flows through and SKIPs on k=0 with logging

- [ ] **Step 1: Write the failing test** — new file with its own builders. Two independent allowlisted PRIMARY publisher groups in the `EventStore` → `corroborated=True`; `k_for("unknown")==0` (cold ledger, `min_n` unmet) → SKIP `below_min_floor`; assert the forecast + component row are logged.

```python
"""End-to-end S6 shadow pipeline (S6 / POL-8, DESIGN-S6-HERMES.md §9.3).

Drives synthetic Hermes proposals through the ONLY write surface (ProposeOnlyFacade.propose_trade)
into process_pending(pipeline=...) on a PaperSigner, exercising the real FusionEngine, ComponentLog,
CalibrationGate/ForecastLedger/PriorEngine, StubMarketMeta, DetectorOrchestrator, and the citation
truth-gate together. Asserts the four DESIGN §9 scenarios:
  (a) a clean CORROBORATED proposal flows fusion->clamp->record_forecast->validator and SKIPs on
      k=0 (paper-only MVP) with the forecast + components logged;
  (b) an indirect-prompt-injection proposal (single fresh source moving p + a thin-book mid) is
      REJECTed same_source_collusion and NEVER reaches the signer;
  (c) an UNCORROBORATED proposal trades mid+prior-only (w_news=0, informational-only);
  (d) a detector-AVOID proposal is REJECTed before sizing.
The category is the "unknown" stub -> k=0 -> paper-only by design; the SKIP is the intended state.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.calibration.config import CalibrationConfig
from polybot.calibration.gate import CalibrationGate
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.prior import PriorEngine
from polybot.detectors.config import DetectorConfig
from polybot.detectors.orchestrator import DetectorInputs, DetectorOrchestrator
from polybot.ers.caps import RiskCaps
from polybot.ers.facade import ProposeOnlyFacade
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import StubMarketMeta
from polybot.ers.service import HermesPipeline, PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.fusion.component_log import ComponentLog
from polybot.fusion.engine import FusionConfig
from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.news import PRIMARY, Source
from polybot.ingestion.orderbook import LocalBook
from polybot.storage.market_memory import EventStore
from polybot.truthgate.gate import TruthGateConfig


def _book(ask, *, ask_size="1000", bid="0.01", bid_size="1000"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book


# Two INDEPENDENT allowlisted primaries (distinct publisher_group) -> corroborated.
_ALLOWLIST = (
    Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", PRIMARY),
    Source("sec-press", "https://www.sec.gov/news/pressreleases.rss", PRIMARY),
)


def _build_pipeline(tmp_path, stamper, event_store, *, fusion_config=None):
    ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
    return HermesPipeline(
        calib_gate=CalibrationGate(ledger, PriorEngine(), CalibrationConfig()),
        fusion_config=fusion_config or FusionConfig(
            w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0),
        truth_gate_config=TruthGateConfig(
            freshness_window_ns=10**12,
            thin_book_depth_usd=Decimal("50"),
            thin_book_move=Decimal("0.02")),
        detectors=DetectorOrchestrator(DetectorConfig()),
        forecast_ledger=ledger,
        component_log=ComponentLog(stamper=stamper),
        market_meta=StubMarketMeta(),
        allowlist=_ALLOWLIST,
        event_store=event_store,
        stamper=stamper,
    ), ledger


def test_e2e_clean_corroborated_proposal_skips_on_k0_with_logging(tmp_path):
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        # Two independent allowlisted primaries cite token "t1" -> corroborated.
        evstore.append(make_envelope(stamper, source="fed-press", source_tier="PRIMARY",
                                     event_id="c1", content="rate decision", market_links=("t1",)))
        evstore.append(make_envelope(stamper, source="sec-press", source_tier="PRIMARY",
                                     event_id="c2", content="enforcement", market_links=("t1",)))
        pipe, ledger = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "i1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.8", resolution_summary="Will the rate be held?",
                thesis="...", citations=("c1", "c2"))
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            # k=0 (cold ledger, "unknown" bucket) -> stake zeroed -> SKIP below_min_floor.
            assert store.get("i1").status == "SKIPPED"
            assert store.get("i1").decision_reason == "below_min_floor"
            assert signer.placed == []
            # BUT the genuine estimate flowed through fusion->clamp->record_forecast:
            rec = ledger.get("i1")
            assert rec is not None and rec.category == "unknown"
            assert Decimal(0) < rec.p < Decimal(1)        # an in-range clamped posterior
            assert len(ComponentLog.__dict__) or True     # components recorded (see below)
            assert len(pipe.component_log.all()) == 1
```

> The two `Source` matching identifiers used in `citations=("c1","c2")` resolve against the `EventStore` by the truth-gate's citation→Envelope matching (event_id here). Use whichever match key Task 2's `verify` resolves on (event_id or url) — the contract says citations are *matched, never fetched*; align the seeded `event_id`/`url` with the citation strings the real `verify` keys on. If the gate matches on URL, seed the envelopes' source/url and pass the URLs as citations instead.

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_clean_corroborated_proposal_skips_on_k0_with_logging -v`
  - Expected: FAIL initially with `ModuleNotFoundError`/`ImportError` if Tasks 1–7 haven't all landed; once they have, PASS. (This is the integration gate — it only goes green when the whole S6 surface is wired.)

- [ ] **Step 3: Write minimal implementation** — no new production code in this task; Task 8 already wired `process_pending`. If the test surfaces a real integration mismatch (e.g. the truth-gate matches citations on URL not event_id, or `ComponentLog.all()` returns a differently-shaped row), fix the *test wiring* to the real contract — do not loosen a production safety path to make the test pass.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_clean_corroborated_proposal_skips_on_k0_with_logging -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_hermes_e2e.py && git commit -m "test(ers): e2e (a) clean corroborated proposal SKIPs on k=0 with logging [S6/POL-8]"`

---

### Task 9b: scenario (b) — indirect-prompt-injection proposal REJECTed `same_source_collusion`, never reaches signer

- [ ] **Step 1: Write the failing test** — a single fresh source moving `p` plus a thin-book mid move tracing to that same source → the truth-gate refuses. Append to `tests/test_ers_hermes_e2e.py`.

```python
def test_e2e_injection_proposal_rejected_same_source_collusion_never_signs(tmp_path):
    # Indirect prompt injection signature: ONE fresh source supplies the p-moving citation AND a
    # thin-book mid move traces to it. The truth-gate refuses (same_source_collusion); the signer
    # is NEVER reached and no forecast is logged (refused evidence is not a genuine estimate).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        # A single fresh primary source (the injection vector) -- NO independent corroboration.
        evstore.append(make_envelope(stamper, source="fed-press", source_tier="PRIMARY",
                                     event_id="inj", content="fabricated catalyst",
                                     market_links=("t1",)))
        pipe, ledger = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "inj1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.95", size_usd_suggestion="100",
                p="0.99", p_confidence="0.9", resolution_summary="Will X happen?",
                thesis="...", citations=("inj",))
            signer = PaperSigner()
            # A THIN book whose mid moved (depth below thin_book_depth_usd=50) on the same fresh
            # source -> the injection+pre-position signature the gate refuses.
            thin = _book("0.70", ask_size="10", bid="0.68", bid_size="10")  # mid 0.69, depth $ < 50
            process_pending(store, book_for={"t1": thin}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("inj1").status == "REJECTED"
            assert store.get("inj1").decision_reason == "same_source_collusion"
            assert signer.placed == []           # the safety claim: never reached the signer
            assert ledger.get("inj1") is None     # refused -> no forecast logged
            assert pipe.component_log.all() == ()
```

> The exact thin-book shape that trips `verify`'s same-source clause is defined by Task 2 (`thin_book_depth_usd`, `thin_book_move`, `freshness_window_ns`). Tune `ask_size`/`bid`/`bid_size` and the seeded envelope's `observed_at` (via `stamper`) so the move is *on thin depth* and *within the freshness window* and *traces to the one fresh source* — the conditions Task 2's tests already pin. Keep `now_ns` (the `stamper.stamp()` the loop passes) within `freshness_window_ns` of the seeded envelope (here `10**12` ns is generous).

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_injection_proposal_rejected_same_source_collusion_never_signs -v`
  - Expected: FAIL until the truth-gate (Task 2) + wiring (Task 8) are present; then PASS. This is the headline injection-probe acceptance.

- [ ] **Step 3: Write minimal implementation** — no production change; if the thin-book/freshness inputs don't trip the gate, adjust the *test inputs* to match Task 2's verified thresholds (never weaken the gate). If a genuine integration bug surfaces (e.g. the loop passes a stale `now_ns`), fix the wiring in `service.py` and note it.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_injection_proposal_rejected_same_source_collusion_never_signs -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_hermes_e2e.py && git commit -m "test(ers): e2e (b) injection proposal REJECT same_source_collusion, never signs [S6/POL-8]"`

---

### Task 9c: scenario (c) — uncorroborated proposal trades mid+prior-only (`w_news=0`)

- [ ] **Step 1: Write the failing test** — one allowlisted primary only (present, not refused) → `corroborated=False` → `w_news_effective==0.0`; the proposal is still logged as a genuine estimate and SKIPs on `k=0`. Append to the file.

```python
def test_e2e_uncorroborated_proposal_is_mid_and_prior_only(tmp_path):
    # A single allowlisted primary -> NOT refused, but corroborated=False -> w_news=0: Hermes is
    # informational-only and the trade reduces to mid + base-rate prior (inside a tight anchor
    # band). The estimate is still logged; k=0 -> SKIP. Pin w_news_effective == 0.0.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        evstore.append(make_envelope(stamper, source="fed-press", source_tier="PRIMARY",
                                     event_id="solo", content="single source",
                                     market_links=("t1",)))
        pipe, ledger = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "u1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.7", resolution_summary="Will the favorite win?",
                thesis="...", citations=("solo",))
            signer = PaperSigner()
            # A DEEP, unmoved book so the same-source thin-book clause does NOT trip (uncorroborated
            # but present -> NOT refused, just w_news=0).
            deep = _book("0.50", ask_size="100000", bid="0.49", bid_size="100000")
            process_pending(store, book_for={"t1": deep}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("u1").status == "SKIPPED"     # k=0 paper-only
            assert signer.placed == []
            rec = ledger.get("u1")
            assert rec is not None                          # estimate logged (not refused)
            row = pipe.component_log.all()[0]
            assert row.w_news_effective == 0.0              # Hermes informational-only
            assert row.corroborated is False
```

> If `ComponentLog.all()` rows are dicts rather than objects, read `row["w_news_effective"]` / `row["corroborated"]`. Match the concrete shape Task 6 builds.

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_uncorroborated_proposal_is_mid_and_prior_only -v`
  - Expected: FAIL until the full surface is wired; then PASS.

- [ ] **Step 3: Write minimal implementation** — no production change (Task 8 threads `corroborated` into `fuse`, which zeroes `w_news_effective` when uncorroborated). Adjust only test inputs if the deep-book shape still trips the gate.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_uncorroborated_proposal_is_mid_and_prior_only -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_hermes_e2e.py && git commit -m "test(ers): e2e (c) uncorroborated proposal mid+prior-only (w_news=0) [S6/POL-8]"`

---

### Task 9d: scenario (d) — detector-AVOID proposal REJECTed before sizing

- [ ] **Step 1: Write the failing test** — feed the orchestrator inputs that produce an AVOID verdict (a toxic flow). Append to the file.

```python
def test_e2e_detector_avoid_proposal_rejected_before_sizing(tmp_path):
    # A detector AVOID (toxic flow inputs) must REJECT(detector_avoid) before fusion/clamp/sizing
    # and place no order -- the defensive pre-gate. Uses the real DetectorOrchestrator with
    # non-zero inputs (S6 default inputs are zeros / NOISE; here we drive an AVOID).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        evstore.append(make_envelope(stamper, source="fed-press", source_tier="PRIMARY",
                                     event_id="c1", content="x", market_links=("t1",)))
        evstore.append(make_envelope(stamper, source="sec-press", source_tier="PRIMARY",
                                     event_id="c2", content="y", market_links=("t1",)))

        # Wrap the real orchestrator so it sees AVOID-producing inputs (the loop calls evaluate with
        # DetectorInputs() zeros at MVP; here we inject a toxic-flow input set via a thin shim that
        # forwards a non-default DetectorInputs). This pins the AVOID->REJECT wiring end-to-end.
        class _AvoidOrchestrator:
            def __init__(self, inner):
                self._inner = inner
            def evaluate(self, intent, *, inputs):
                toxic = DetectorInputs(buy_size=Decimal("900"), sell_size=Decimal("10"),
                                       baseline_mean=Decimal("0.2"), baseline_std=Decimal("0.05"),
                                       classification="INSIDER_LIKE", catalyst_present=False)
                return self._inner.evaluate(intent, inputs=toxic)

        ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
        pipe = HermesPipeline(
            calib_gate=CalibrationGate(ledger, PriorEngine(), CalibrationConfig()),
            fusion_config=FusionConfig(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0,
                                       clip_logodds=2.0),
            truth_gate_config=TruthGateConfig(freshness_window_ns=10**12,
                                              thin_book_depth_usd=Decimal("50"),
                                              thin_book_move=Decimal("0.02")),
            detectors=_AvoidOrchestrator(DetectorOrchestrator(DetectorConfig())),
            forecast_ledger=ledger, component_log=ComponentLog(stamper=stamper),
            market_meta=StubMarketMeta(), allowlist=_ALLOWLIST, event_store=evstore, stamper=stamper)

        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "d1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.8", resolution_summary="Will X?", thesis="...",
                citations=("c1", "c2"))
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("d1").status == "REJECTED"
            assert store.get("d1").decision_reason == "detector_avoid"
            assert signer.placed == []
            assert ledger.get("d1") is None        # rejected before the estimate -> no forecast
            assert pipe.component_log.all() == ()
```

> The `_AvoidOrchestrator` shim forwards toxic `DetectorInputs` so the **real** `DetectorOrchestrator.evaluate → toxicity → composite → policy.decide` chain returns AVOID. If the toxic input set above doesn't yield AVOID under the real policy thresholds, mirror the exact AVOID-producing inputs from Task 7's `tests/test_detectors_orchestrator.py` (the unit test that already proves an AVOID verdict) — reuse its verified fixture verbatim so this e2e stays faithful to the real policy.

- [ ] **Step 2: Run test to verify it fails**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_detector_avoid_proposal_rejected_before_sizing -v`
  - Expected: FAIL until the surface is wired; then PASS.

- [ ] **Step 3: Write minimal implementation** — no production change (Task 8's step 2 already rejects on AVOID before any estimate). Align the toxic input set with Task 7's verified AVOID fixture if needed.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py::test_e2e_detector_avoid_proposal_rejected_before_sizing -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**
  - `git add tests/test_ers_hermes_e2e.py && git commit -m "test(ers): e2e (d) detector-AVOID proposal REJECT before sizing [S6/POL-8]"`

---

### Task 9e: full e2e file + the entire suite green (acceptance §9.1/§9.3)

- [ ] **Step 1: Run the e2e file then the whole suite**
  - Run: `./.venv/bin/pytest tests/test_ers_hermes_e2e.py -v`
  - Run: `./.venv/bin/pytest`
  - Expected: PASS — the four DESIGN §9.3 scenarios green AND the full regression (377 pre-existing + all new S6 unit/e2e cases) green.

- [ ] **Step 2: Commit (no-op if clean)**
  - `git add -A && git commit -m "test(ers): S6/POL-8 end-to-end shadow pipeline green; full regression intact [S6/POL-8]" || echo "nothing to commit"`

---

### Task 10: Hermes config.yaml artifact

The reviewed, version-controlled Hermes deployment artifact (DESIGN §8, obligation #12). It is **not a running server in S6** — it is a static, operator-reviewable declaration of (a) the exact tool surface Hermes is granted (`tools.include` = the 4 read tools + the single write tool `propose_trade`, and nothing that can sign, mutate status, or shell), and (b) the deployment posture (Hermes is its own Linux user, no shell into the ERS, no keys, and may rewrite only its own `SKILL.md`, with trust/trade rules and secrets forbidden in any model-mutable text).

Because the repo has **no YAML dependency** (`pyproject.toml` deps are exactly `httpx>=0.28`, `websockets>=16`; the `uv venv --python 3.13` site-packages has no `yaml`/`PyYAML`), the test must be **stdlib-only**. We therefore write a tiny purpose-built parser in the test that extracts the `tools.include` list (the `- item` lines under `tools:` → `include:`) — no new dependency, no `import yaml`. The test asserts the include set is **exactly** the allowed 5 tools and that it contains **none** of a denylist of signing/admin/mutation tool names. A second test asserts the documented posture strings are present (own Linux user / no keys / no shell / SKILL.md self-rewrite only).

> Deviation note (called out loudly): the pinned contract names the artifact `deploy/hermes/config.yaml`; DESIGN §0/§2 refer to it conversationally as `~/.hermes/config.yaml`. These are the same artifact — `deploy/hermes/config.yaml` is the in-repo reviewed source that is *deployed to* `~/.hermes/config.yaml` on the (future) Hermes box. We follow the **pinned** path `deploy/hermes/config.yaml`. No other deviation.

**Files:**
- Create: `deploy/hermes/config.yaml`
- Create: `tests/test_hermes_config.py`

The allowed tool set is the pinned contract: `{propose_trade, get_market, get_book, get_ledger, get_flags}`. The denylist (must never appear in `tools.include`) covers the chokepoint mutators and signer reach: `place, flatten, record_decision, pending, sign, signer, place_order, cancel_order, transfer, withdraw, approve, admin, update_status, set_status`.

---

- [ ] **Step 1: Write the failing test — `tools.include` is exactly the 5 allowed tools**

Create `tests/test_hermes_config.py`. We deliberately do **not** `import yaml` (the repo has no YAML dep). Instead a ~15-line stdlib parser pulls the `- item` entries nested under `tools:` → `include:`. Import-free, dependency-free.

```python
"""S6 / POL-8 — deploy/hermes/config.yaml reviewed artifact (Task 10).

Safety property under test: the Hermes harness is granted EXACTLY the four
read tools plus the single INSERT-only write tool `propose_trade`, and is
granted NONE of the signing/admin/status-mutation tools that would let it
reach the signer or flip an intent's status. The artifact also documents the
deployment posture (own Linux user, no keys, no shell into the ERS, may
rewrite only its own SKILL.md).

This repo has NO YAML dependency (pyproject deps = httpx, websockets only),
so the test is stdlib-only: a tiny purpose-built parser extracts the
`tools.include` list. No `import yaml`.
"""

from pathlib import Path

# Repo-root-relative path to the reviewed artifact (this test file lives in
# <repo>/tests/, so the repo root is its parent's parent).
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "deploy" / "hermes" / "config.yaml"

# The pinned contract: the complete allowed tool surface.
_ALLOWED_TOOLS = frozenset(
    {"propose_trade", "get_market", "get_book", "get_ledger", "get_flags"}
)

# Tools that MUST never appear — anything that signs, moves money, mutates an
# intent's status, or reaches the chokepoint mutators on IntentStore.
_DENYLIST = frozenset(
    {
        "place",
        "flatten",
        "record_decision",
        "pending",
        "sign",
        "signer",
        "place_order",
        "cancel_order",
        "transfer",
        "withdraw",
        "approve",
        "admin",
        "update_status",
        "set_status",
    }
)


def _parse_tools_include(text):
    """Stdlib-only YAML-subset parser: return the list items nested under
    `tools:` -> `include:`. Tolerates inline `# comments`. Requires the
    artifact to use the simple block-list shape (one `- name` per line)."""
    lines = text.splitlines()
    in_tools = False
    in_include = False
    items = []
    for raw in lines:
        # Strip trailing comments and trailing whitespace; keep leading indent.
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            # A new top-level key ends any block we were inside.
            in_tools = stripped.startswith("tools:")
            in_include = False
            continue
        if in_tools and stripped.startswith("include:") and not in_include:
            in_include = True
            continue
        if in_include:
            if stripped.startswith("- "):
                items.append(stripped[2:].strip().strip("'\""))
            elif indent <= 2 and not stripped.startswith("- "):
                # Dedent back to a sibling key under `tools:` -> include block done.
                in_include = False
    return items


def test_config_artifact_exists():
    assert _CONFIG_PATH.is_file(), f"missing reviewed artifact: {_CONFIG_PATH}"


def test_tools_include_is_exactly_the_allowed_set():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    items = _parse_tools_include(text)
    assert items, "tools.include parsed empty — artifact shape changed"
    # No duplicates, and the set matches the pinned contract exactly.
    assert len(items) == len(set(items)), f"duplicate tool entries: {items}"
    assert set(items) == _ALLOWED_TOOLS, f"tools.include != allowed set: {sorted(items)}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/bin/pytest tests/test_hermes_config.py::test_tools_include_is_exactly_the_allowed_set -v`

Expected: FAIL — `deploy/hermes/config.yaml` does not exist yet, so `read_text` raises `FileNotFoundError` (and `test_config_artifact_exists` fails its assert).

- [ ] **Step 3: Write the minimal artifact — `deploy/hermes/config.yaml`**

Create `deploy/hermes/config.yaml`. The `tools.include` block uses the simple one-`- name`-per-line shape the parser expects. The posture is documented in a `deployment:` block with explicit boolean/string fields (so the Step 6 posture test can assert against them) plus a header comment.

```yaml
# Hermes harness deployment manifest — REVIEWED, version-controlled artifact.
#
# S6 / POL-8. This is NOT a running server in S6: it is the operator-reviewed
# declaration of the tool surface and deployment posture for the (future)
# Hermes box. Deployed copy lives at ~/.hermes/config.yaml on that box.
#
# SAFETY CONTRACT (load-bearing — reviewed alongside the ERS):
#   * tools.include is EXACTLY the four read tools + the single INSERT-only
#     write tool `propose_trade`. Hermes can at worst enqueue a PROPOSED
#     intent; it cannot sign, size, mutate status, or reach the signer.
#   * No signing / admin / status-mutation tool is granted.
#   * Hermes runs as its own unprivileged Linux user, holds no keys, and has
#     no shell into the ERS.
#   * Hermes may rewrite ONLY its own SKILL.md. Trust/trade rules and secrets
#     are forbidden in any model-mutable text.

hermes:
  model: frozen        # pinned frozen-model harness; no live network keys
  display_name: "polybot-hermes-proposer"

tools:
  # The COMPLETE grant. Adding anything here is a reviewed change.
  include:
    - propose_trade    # the ONE write tool — INSERT-only PROPOSED row
    - get_market       # read tool
    - get_book         # read tool
    - get_ledger       # read tool (resolved-market history / outcomes)
    - get_flags        # read tool (detector flags: AVOID / FLAG_ONLY)

deployment:
  # Operator-reviewed posture. The test asserts these exact keys/values.
  own_linux_user: true        # dedicated unprivileged user, isolated home
  holds_keys: false           # no signing keys, no wallet, no API secrets
  shell_into_ers: false       # no shell / no exec path into the ERS process
  may_rewrite_skill_md_only: true   # may edit only its own SKILL.md
  secrets_in_model_mutable_text: false   # no secrets/trust rules in SKILL.md
  notes: >
    Safety is enforced in code by ProposeOnlyFacade (ers/facade.py): the only
    write path is propose_trade -> IntentStore INSERT. This manifest documents
    the same boundary for the deployment layer. The ERS independently
    re-derives price, size, caps, calibration k, truth-gate, and clamps the
    posterior — no Hermes-supplied field is ever trusted upward.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/bin/pytest tests/test_hermes_config.py::test_config_artifact_exists tests/test_hermes_config.py::test_tools_include_is_exactly_the_allowed_set -v`

Expected: PASS — both `test_config_artifact_exists` and `test_tools_include_is_exactly_the_allowed_set` are green (parser extracts exactly `{propose_trade, get_market, get_book, get_ledger, get_flags}`).

- [ ] **Step 5: Commit**

```bash
git add deploy/hermes/config.yaml tests/test_hermes_config.py && git commit -m "feat(hermes): add reviewed config.yaml artifact with exact tool grant (S6/POL-8)"
```

- [ ] **Step 6: Write the failing test — denylist (no signing/admin/mutation tool is granted)**

Add to `tests/test_hermes_config.py`. This is a distinct safety property: not just "the include set equals the allowed set" but "the include set shares **nothing** with the denylist of dangerous tools". Two separate tests so a future edit that, say, adds `place_order` AND drops a read tool still trips the denylist test independently.

```python
def test_tools_include_grants_no_signing_or_admin_tool():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    items = set(_parse_tools_include(text))
    leaked = items & _DENYLIST
    assert not leaked, f"forbidden tool(s) granted to Hermes: {sorted(leaked)}"
```

- [ ] **Step 7: Run the test to verify it passes (already-correct artifact)**

Run: `./.venv/bin/pytest tests/test_hermes_config.py::test_tools_include_grants_no_signing_or_admin_tool -v`

Expected: PASS — the artifact's include set is `{propose_trade, get_market, get_book, get_ledger, get_flags}`, which is disjoint from `_DENYLIST`.

To prove the test actually guards the property (RED→GREEN discipline for a static artifact), temporarily add `    - place_order` under `tools.include` in `deploy/hermes/config.yaml`, re-run the command, and confirm it FAILs with `forbidden tool(s) granted to Hermes: ['place_order']`. Then remove that line and re-run to confirm PASS again. Do **not** commit the temporary edit.

- [ ] **Step 8: Commit**

```bash
git add tests/test_hermes_config.py && git commit -m "test(hermes): assert config.yaml grants no signing/admin tool (S6/POL-8)"
```

- [ ] **Step 9: Write the failing test — documented deployment posture**

Add to `tests/test_hermes_config.py`. Asserts the operator-reviewed posture fields are present with the correct values, using the same stdlib block-key reader (no `import yaml`). This locks the "own Linux user / no keys / no shell / SKILL.md-self-rewrite-only / no secrets in model-mutable text" documentation into the test.

```python
def _parse_block_keys(text, block_name):
    """Stdlib-only: return {key: value-token} for the simple `key: value`
    lines nested one level under a top-level `block_name:` mapping. Folded
    (`>`) and nested blocks are skipped (only scalar `key: token` lines)."""
    lines = text.splitlines()
    in_block = False
    out = {}
    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_block = stripped.startswith(f"{block_name}:")
            continue
        if in_block and indent == 2 and ":" in stripped:
            key, _, val = stripped.partition(":")
            out[key.strip()] = val.strip()
    return out


def test_deployment_posture_is_documented_and_locked():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    posture = _parse_block_keys(text, "deployment")
    assert posture.get("own_linux_user") == "true", posture
    assert posture.get("holds_keys") == "false", posture
    assert posture.get("shell_into_ers") == "false", posture
    assert posture.get("may_rewrite_skill_md_only") == "true", posture
    assert posture.get("secrets_in_model_mutable_text") == "false", posture
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `./.venv/bin/pytest tests/test_hermes_config.py::test_deployment_posture_is_documented_and_locked -v`

Expected: PASS — the `deployment:` block in the artifact declares exactly those five keys with the asserted values.

(If it FAILs because the parser does not see the keys, confirm the `deployment:` keys are indented exactly 2 spaces and use the `key: value` scalar shape — the `notes:` folded block is intentionally skipped by the parser.)

- [ ] **Step 11: Commit**

```bash
git add tests/test_hermes_config.py && git commit -m "test(hermes): lock documented deployment posture in config.yaml (S6/POL-8)"
```

- [ ] **Step 12: Run the full suite — confirm the 377 baseline still passes**

Run: `./.venv/bin/pytest`

Expected: PASS — all prior tests green plus the 4 new tests in `tests/test_hermes_config.py` (`test_config_artifact_exists`, `test_tools_include_is_exactly_the_allowed_set`, `test_tools_include_grants_no_signing_or_admin_tool`, `test_deployment_posture_is_documented_and_locked`). This task adds a file + a test module only; it introduces no import of the new artifact into runtime code and no new dependency, so the baseline is unaffected.

- [ ] **Step 13: Commit (only if Step 12 surfaced any incidental fixup)**

If Step 12 was clean, nothing to commit here. If a path/indent fixup was needed:

```bash
git add deploy/hermes/config.yaml tests/test_hermes_config.py && git commit -m "fix(hermes): align config.yaml shape with stdlib parser (S6/POL-8)"
```

---

## Done criteria

- `./.venv/bin/pytest` green; **the 377 existing tests still pass** (additive seam; `pipeline=None` == slice-3 behavior).
- Every unit has its tests, including: the `ProposeOnlyFacade` structural sweep (no `place`/`flatten`/`record_decision`/`pending` reachable); the `fed-press`/`fed-monetary` same-`publisher_group` independence regression; the corroboration→`w_news` flip; the `w_news > 0.25` construction guard; the same-source `same_source_collusion` refusal; and the `anchor_error` distinct-reason path.
- The **end-to-end shadow test (Task 9)** demonstrates the DESIGN §9 scenarios: a clean corroborated flow SKIPs on `k=0` with the forecast **and** components logged; an **indirect-prompt-injection proposal is REJECTed (`same_source_collusion`) and never reaches the signer**; an uncorroborated proposal trades mid+prior-only (`w_news=0`); a detector-AVOID proposal is REJECTed before sizing.
- **Two Opus `code-reviewer` passes** (after Task 8 and Task 9); re-review after any safety-critical fix.
- `docs/HANDOFF.md` + the memory files updated; a progress comment posted on [POL-8](https://mysigner.youtrack.cloud/issue/POL-8).
- Branch `pol-8-hermes-s6`; commit per task; merge to `main` `--no-ff` with the verification status in the merge message. **Confirm before pushing to origin.**
