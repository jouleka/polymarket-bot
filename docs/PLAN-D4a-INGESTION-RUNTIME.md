# D4a — Continuous ingestion runtime — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a long-running, durable, supervised process that captures the un-backfillable Polymarket order-book +
trade stream (sharded CLOB WS + Data API `/trades`) into the existing `EventStore`, deployable as an isolated
systemd service.

**Architecture:** a NEW purely-additive package `src/polybot/runtime/` in three layers — a self-verifying
`IngestionConfig` + loader, a pure `discover_universe`, and an `IngestionRuntime` supervision core that runs the
already-tested async collectors in one `asyncio.TaskGroup` with signal-driven, durability-preserving shutdown — plus
a thin `build_ingestion_runtime` factory + `main` entry point. No existing file is modified.

**Tech Stack:** Python 3.12/3.13 · asyncio (`TaskGroup`, `except*`) · `tomllib` · the S1 ingestion collectors +
`QueuedEventWriter`/`EventStore`/`MonotonicStamper` · `httpx` · pytest.

**Spec:** [`docs/DESIGN-D4a-INGESTION-RUNTIME.md`](DESIGN-D4a-INGESTION-RUNTIME.md) (§4 contract, §5 invariants, §7 acceptance).

---

## Shared context / contract block (READ FIRST — the pinned reused signatures)

**Conventions (ENFORCED):** strict TDD (write the failing test, RUN it, watch it fail for the RIGHT reason, then
minimal code); one concern per test; a commit per GREEN cycle; every numeric untrusted-input fails closed; fail loud
on format/shape changes. Run tests BARE: `./.venv/bin/pytest <path> -o addopts="" -q` (do NOT pipe through
head/tail). Baseline before starting = **1113 passed, exit 0** on `main`; this branch is `pol-13-d4a-ingestion-runtime`.

**Purely additive invariant:** create ONLY `src/polybot/runtime/**` and `tests/test_runtime_*.py`. `git diff
--name-only main -- src/` must show only new files under `src/polybot/runtime/`. No existing `src/` file is edited.

**Reused primitives — verbatim signatures (do NOT reimplement; import and call):**

```python
# polybot.core.clock
class MonotonicStamper:
    def __init__(self, clock=None): ...          # clock defaults to time.monotonic_ns; NO public clock accessor
    def stamp(self) -> int: ...                   # strictly-increasing ns; ONE instance shared by all collectors

# polybot.storage.market_memory
class EventStore:
    def __init__(self, path, *, check_same_thread=True): ...   # use check_same_thread=False under the writer thread
    def append(self, envelope) -> None: ...
    def all(self) -> list: ...                    # materialized list, ORDER BY observed_at, rowid
    def close(self) -> None: ...
    # context manager (__enter__/__exit__)

# polybot.storage.event_writer
class QueuedEventWriter:
    def __init__(self, store, *, max_queued=100_000): ...
    def append(self, envelope) -> None: ...       # sync, non-blocking; HALTs (raises) if writer thread died / backlog full
    def close(self) -> None: ...                  # IDEMPOTENT; drains queue -> joins thread -> store.close() -> re-raises writer error
    def peak_pending(self) -> int: ...

# polybot.ingestion.persistence
class PersistingSink:
    def __init__(self, store, source="clob-ws", source_tier="VENUE"): ...   # store may be a QueuedEventWriter (duck-typed .append)
    def __call__(self, observation) -> None: ...  # SYNC sink

# polybot.ingestion.sharding
class ShardedMarketCollector:
    def __init__(self, connect, stamper, asset_ids, *, sink=None,
                 max_assets_per_shard=500, detector=None, synthetic_sink=None, **socket_kwargs): ...
    # RAISES ValueError on: max_assets_per_shard<=0; empty asset_ids; DUPLICATE asset_ids. Builds its OWN per-shard streams.
    async def run(self, max_connections=1) -> None: ...   # max_connections=None => reconnect forever (production)
    @property
    def shard_count(self) -> int: ...

# polybot.ingestion.data_api
class DataApiPoller:
    def __init__(self, fetch, stamper, store, source="data-api"): ...   # fetch: async (path, params) -> list[dict]; store may be the writer
    async def poll_once(self, path, *, params=None, source_tier="DATA") -> int: ...
    async def run(self, path, *, params=None, source_tier="DATA", interval=2.0,
                  limiter=None, sleep=asyncio.sleep, max_polls=None) -> None: ...   # max_polls=None => forever

# polybot.ingestion.transport
GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
WS_RECONNECT_ON = (OSError, ConnectionClosed)
def make_httpx_fetch(base_url=DATA_API_URL, timeout=15.0, client=None): ...   # -> async fetch(path, params) -> parsed JSON
async def open_market_ws(url=CLOB_MARKET_WS): ...   # MarketSocket transport (the `connect` arg for ShardedMarketCollector)

# polybot.ingestion.gamma
def normalize_market(raw) -> Market: ...   # RAISES on format change / missing clobTokenIds|outcomes|outcomePrices
# Market: .condition_id .question .slug .outcomes(tuple[Outcome]) .active(bool) .closed(bool)
# Outcome: .name .token_id(str) .price(Decimal)     # binary market == len(outcomes) == 2

# polybot.ers.heartbeat
class Heartbeat:
    def __init__(self, path, *, clock=None): ...   # clock defaults to time.time
    def beat(self) -> None: ...                     # atomic write (temp + os.replace + fsync); SYNC
```

**Net-new unit signatures (pin these; later tasks depend on exact names):**

```python
# src/polybot/runtime/config.py
@dataclass(frozen=True)
class IngestionConfig:
    db_path: str
    universe_max_markets: int = 400
    max_assets_per_shard: int = 500
    data_api_enabled: bool = True
    data_api_interval_seconds: float = 2.0
    data_api_limit: int = 500
    heartbeat_path: str | None = None
    heartbeat_interval_seconds: float = 5.0
    gamma_url: str = GAMMA_URL
    log_level: str = "INFO"
def load_config(toml_path: str | None = None, *, env: Mapping[str, str] = os.environ) -> IngestionConfig: ...

# src/polybot/runtime/discovery.py
def discover_universe(fetch, config: IngestionConfig) -> list[str]: ...   # fetch: SYNC (params: dict) -> list[dict]
def make_gamma_fetch(gamma_url: str, *, timeout: float = 30.0): ...        # default production SYNC fetch

# src/polybot/runtime/ingestion.py
class IngestionRuntime:
    def __init__(self, *, services, writer, heartbeat=None,
                 heartbeat_interval_seconds: float = 5.0, sleep=asyncio.sleep): ...   # services: Sequence[Callable[[], Awaitable]]
    def request_stop(self) -> None: ...
    async def run(self) -> None: ...
def build_ingestion_runtime(config, *, gamma_fetch=None, ws_connect=None,
                            data_fetch=None, stamper=None) -> IngestionRuntime: ...
def main(argv=None) -> int: ...
```

---

## Task 1: `IngestionConfig` + `load_config` (D4a-1)

**Files:**
- Create: `src/polybot/runtime/__init__.py` (empty package marker)
- Create: `src/polybot/runtime/config.py`
- Test: `tests/test_runtime_config.py`

- [ ] **Step 1.1 — Write the failing test: a valid config round-trips + defaults.**

```python
# tests/test_runtime_config.py
import math
import pytest
from polybot.runtime.config import IngestionConfig
# NOTE: load_config is NOT imported at module level here — it does not exist until Step 1.11, and a
# top-level import of a not-yet-defined name fails COLLECTION of the WHOLE file (dragging the earlier
# IngestionConfig tests red too). Its tests import it LOCALLY (Step 1.9) so each RED stays clean + isolated.

def test_valid_config_defaults():
    c = IngestionConfig(db_path="/data/m.db")
    assert c.db_path == "/data/m.db"
    assert c.universe_max_markets == 400
    assert c.max_assets_per_shard == 500
    assert c.data_api_enabled is True
    assert c.data_api_interval_seconds == 2.0
    assert c.heartbeat_path is None
    assert c.log_level == "INFO"
```

- [ ] **Step 1.2 — Run it, watch it fail** (`ModuleNotFoundError: polybot.runtime`).

Run: `./.venv/bin/pytest tests/test_runtime_config.py::test_valid_config_defaults -o addopts="" -q`
Expected: FAIL (import error).

- [ ] **Step 1.3 — Minimal implementation** (create the package + the frozen dataclass; no validation yet).

```python
# src/polybot/runtime/__init__.py
# (empty — package marker)
```

```python
# src/polybot/runtime/config.py
"""Ingestion runtime config — the FIRST config surface in the repo (every component is otherwise pure-DI).
Frozen + self-verifying (fail LOUD at construction); a thin TOML+env loader overlays it."""
from __future__ import annotations

import dataclasses
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from polybot.ingestion.transport import GAMMA_URL

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


@dataclass(frozen=True)
class IngestionConfig:
    db_path: str
    universe_max_markets: int = 400
    max_assets_per_shard: int = 500
    data_api_enabled: bool = True
    data_api_interval_seconds: float = 2.0
    data_api_limit: int = 500
    heartbeat_path: str | None = None
    heartbeat_interval_seconds: float = 5.0
    gamma_url: str = GAMMA_URL
    log_level: str = "INFO"
```

- [ ] **Step 1.4 — Run it, watch it pass.**

Run: `./.venv/bin/pytest tests/test_runtime_config.py::test_valid_config_defaults -o addopts="" -q`
Expected: PASS.

- [ ] **Step 1.5 — Write the failing test: invalid values fail LOUD** (one assert per bad field).

```python
# tests/test_runtime_config.py  (append)
@pytest.mark.parametrize("kwargs", [
    {"db_path": ""},                                    # empty path
    {"db_path": "/d", "universe_max_markets": 0},       # < 1
    {"db_path": "/d", "max_assets_per_shard": 0},       # < 1
    {"db_path": "/d", "data_api_limit": 0},             # < 1
    {"db_path": "/d", "data_api_interval_seconds": 0},  # not > 0
    {"db_path": "/d", "data_api_interval_seconds": math.inf},   # not finite
    {"db_path": "/d", "heartbeat_interval_seconds": -1},        # not > 0
    {"db_path": "/d", "log_level": "LOUD"},             # unknown level
])
def test_invalid_config_raises(kwargs):
    with pytest.raises(ValueError):
        IngestionConfig(**kwargs)
```

- [ ] **Step 1.6 — Run it, watch it fail** (no validation yet → constructs without raising).

Run: `./.venv/bin/pytest tests/test_runtime_config.py::test_invalid_config_raises -o addopts="" -q`
Expected: FAIL (`DID NOT RAISE ValueError`).

- [ ] **Step 1.7 — Add `__post_init__` validation.**

```python
# src/polybot/runtime/config.py  (add method to IngestionConfig)
    def __post_init__(self) -> None:
        if not self.db_path:
            raise ValueError("db_path must be non-empty")
        if self.universe_max_markets < 1:
            raise ValueError("universe_max_markets must be >= 1")
        if self.max_assets_per_shard < 1:
            raise ValueError("max_assets_per_shard must be >= 1")
        if self.data_api_limit < 1:
            raise ValueError("data_api_limit must be >= 1")
        for name in ("data_api_interval_seconds", "heartbeat_interval_seconds"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0")
        if self.log_level not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_LOG_LEVELS)}")
```

- [ ] **Step 1.8 — Run both tests, watch pass.**

Run: `./.venv/bin/pytest tests/test_runtime_config.py -o addopts="" -q`
Expected: PASS (all params).

- [ ] **Step 1.9 — Write the failing test: `load_config` overlays TOML then env, then self-verifies.**

```python
# tests/test_runtime_config.py  (append)
def test_load_config_toml_then_env(tmp_path):
    from polybot.runtime.config import load_config   # local: undefined until Step 1.11 -> keeps this RED isolated
    toml = tmp_path / "ingest.toml"
    toml.write_text('db_path = "/from/toml.db"\nuniverse_max_markets = 50\n')
    # env overrides the toml value; unrelated keys keep toml/default
    cfg = load_config(str(toml), env={"POLYBOT_INGEST_UNIVERSE_MAX_MARKETS": "77",
                                      "POLYBOT_INGEST_DATA_API_ENABLED": "false"})
    assert cfg.db_path == "/from/toml.db"      # from toml
    assert cfg.universe_max_markets == 77      # env overrode toml
    assert cfg.data_api_enabled is False       # env-coerced bool
    assert cfg.max_assets_per_shard == 500     # default

def test_load_config_env_only():
    from polybot.runtime.config import load_config
    cfg = load_config(None, env={"POLYBOT_INGEST_DB_PATH": "/env.db"})
    assert cfg.db_path == "/env.db"

def test_load_config_invalid_still_fails_loud():
    from polybot.runtime.config import load_config
    with pytest.raises(ValueError):
        load_config(None, env={"POLYBOT_INGEST_DB_PATH": "/e.db",
                               "POLYBOT_INGEST_UNIVERSE_MAX_MARKETS": "0"})
```

- [ ] **Step 1.10 — Run it, watch it fail** (`load_config` undefined).

Run: `./.venv/bin/pytest tests/test_runtime_config.py -k load_config -o addopts="" -q`
Expected: FAIL (AttributeError / ImportError).

- [ ] **Step 1.11 — Implement `load_config` + `_coerce`.**

```python
# src/polybot/runtime/config.py  (append at module level)
_ENV_PREFIX = "POLYBOT_INGEST_"
_INT_FIELDS = {"universe_max_markets", "max_assets_per_shard", "data_api_limit"}
_FLOAT_FIELDS = {"data_api_interval_seconds", "heartbeat_interval_seconds"}
_BOOL_FIELDS = {"data_api_enabled"}


def _coerce(field_name: str, raw: str):
    if field_name in _INT_FIELDS:
        return int(raw)
    if field_name in _FLOAT_FIELDS:
        return float(raw)
    if field_name in _BOOL_FIELDS:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw  # str fields: db_path, heartbeat_path, gamma_url, log_level


def load_config(toml_path: str | None = None, *, env: Mapping[str, str] = os.environ) -> IngestionConfig:
    """Overlay: defaults <- optional TOML file <- POLYBOT_INGEST_* env vars, then IngestionConfig(**merged)
    (which self-verifies). The ONLY place env/files are read — every collector stays pure-DI."""
    field_names = {f.name for f in dataclasses.fields(IngestionConfig)}
    values: dict = {}
    if toml_path:
        with open(toml_path, "rb") as fh:
            loaded = tomllib.load(fh)
        for key, val in loaded.items():
            if key not in field_names:
                raise ValueError(f"unknown ingestion config key in {toml_path}: {key!r}")
            values[key] = val
    for name in field_names:
        env_key = _ENV_PREFIX + name.upper()
        if env_key in env:
            values[name] = _coerce(name, env[env_key])
    return IngestionConfig(**values)
```

- [ ] **Step 1.12 — Run the full file, watch pass.**

Run: `./.venv/bin/pytest tests/test_runtime_config.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 1.13 — Commit.**

```bash
git add src/polybot/runtime/__init__.py src/polybot/runtime/config.py tests/test_runtime_config.py
git commit -m "feat(runtime): IngestionConfig + load_config (D4a-1)"
```

---

## Task 2: `discover_universe` (D4a-2)

**Files:**
- Create: `src/polybot/runtime/discovery.py`
- Test: `tests/test_runtime_discovery.py`

Discovery is a PURE sync function over an injected `fetch(params) -> list[dict]` (the raw Gamma `/markets` rows). It
filters to active + binary + `acceptingOrders`, ranks by 24h volume, takes the top-N markets, and returns the flat,
de-duplicated `clobTokenIds` (which `ShardedMarketCollector` requires to be unique). It fails LOUD on an unusable
response and on a wholesale format change (zero tradeable markets), but skips an individually-malformed row.

- [ ] **Step 2.1 — Write the failing test: rank by volume, cap to N, flatten+dedupe token_ids.**

```python
# tests/test_runtime_discovery.py
import pytest
from polybot.runtime.config import IngestionConfig
from polybot.runtime.discovery import discover_universe

def _market(cid, yes, no, vol, accepting=True):
    return {"conditionId": cid, "acceptingOrders": accepting, "volume24hr": vol,
            "active": True, "closed": False,
            "clobTokenIds": f'["{yes}", "{no}"]', "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]'}

def test_ranks_by_volume_and_caps(monkeypatch):
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=2)
    rows = [_market("c1", "t1a", "t1b", 10.0), _market("c2", "t2a", "t2b", 99.0),
            _market("c3", "t3a", "t3b", 50.0)]
    tokens = discover_universe(lambda params: rows, cfg)
    # top-2 by volume are c2 (99) then c3 (50); c1 (10) dropped. Order preserved, deduped.
    assert tokens == ["t2a", "t2b", "t3a", "t3b"]
```

- [ ] **Step 2.2 — Run it, watch it fail** (`discover_universe` undefined).

Run: `./.venv/bin/pytest tests/test_runtime_discovery.py::test_ranks_by_volume_and_caps -o addopts="" -q`
Expected: FAIL (ImportError).

- [ ] **Step 2.3 — Minimal implementation.**

```python
# src/polybot/runtime/discovery.py
"""Market-universe discovery: Gamma /markets -> top-N active binary tradeable markets by 24h volume ->
the flat, de-duplicated clobTokenIds the sharded WS collector subscribes to. Pure but for the injected `fetch`."""
from __future__ import annotations

import httpx

from polybot.ingestion.gamma import normalize_market
from polybot.runtime.config import IngestionConfig


def _volume(raw) -> float:
    try:
        return float(raw.get("volume24hr"))
    except (TypeError, ValueError):
        return 0.0  # missing/unparseable volume sorts last (fault-isolated)


def discover_universe(fetch, config: IngestionConfig) -> list[str]:
    rows = fetch({"limit": config.universe_max_markets * 3, "closed": "false",
                  "active": "true", "order": "volume24hr", "ascending": "false"})
    if not isinstance(rows, list):
        raise TypeError(f"Gamma /markets response is not a list: {type(rows).__name__}")
    ranked = []
    for raw in rows:
        if not raw.get("acceptingOrders"):
            continue
        try:
            market = normalize_market(raw)
        except Exception:
            continue  # individually-malformed row: skip so one bad market never breaks discovery
        if market.closed or not market.active or len(market.outcomes) != 2:
            continue
        ranked.append((_volume(raw), market))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    token_ids: list[str] = []
    seen: set[str] = set()
    for _vol, market in ranked[: config.universe_max_markets]:
        for outcome in market.outcomes:
            if outcome.token_id not in seen:
                seen.add(outcome.token_id)
                token_ids.append(outcome.token_id)
    if not token_ids:
        raise RuntimeError("discover_universe found no tradeable markets — possible Gamma format change")
    return token_ids
```

- [ ] **Step 2.4 — Run it, watch it pass.**

Run: `./.venv/bin/pytest tests/test_runtime_discovery.py::test_ranks_by_volume_and_caps -o addopts="" -q`
Expected: PASS.

- [ ] **Step 2.5 — Write failing tests: filters (non-accepting / non-binary), malformed-skip, dedupe, empty-fails-loud, non-list-fails-loud.**

```python
# tests/test_runtime_discovery.py  (append)
def test_filters_non_accepting_and_non_binary():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    multi = {"conditionId": "m", "acceptingOrders": True, "volume24hr": 100.0,
             "active": True, "closed": False,
             "clobTokenIds": '["a", "b", "c"]', "outcomes": '["A", "B", "C"]',
             "outcomePrices": '["0.3", "0.3", "0.4"]'}          # 3 outcomes -> not binary
    rows = [_market("ok", "t1", "t2", 5.0), _market("no", "x1", "x2", 9.0, accepting=False), multi]
    assert discover_universe(lambda p: rows, cfg) == ["t1", "t2"]

def test_skips_individually_malformed_row():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    bad = {"conditionId": "bad", "acceptingOrders": True, "volume24hr": 999.0}  # missing clobTokenIds -> normalize raises
    rows = [bad, _market("ok", "t1", "t2", 1.0)]
    assert discover_universe(lambda p: rows, cfg) == ["t1", "t2"]

def test_dedupes_shared_token_across_markets():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    rows = [_market("c1", "shared", "t1b", 10.0), _market("c2", "shared", "t2b", 9.0)]
    tokens = discover_universe(lambda p: rows, cfg)
    assert tokens == ["shared", "t1b", "t2b"]     # 'shared' appears once (collector rejects dupes)

def test_empty_result_fails_loud():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    with pytest.raises(RuntimeError):
        discover_universe(lambda p: [{"acceptingOrders": False}], cfg)   # nothing tradeable

def test_non_list_response_fails_loud():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    with pytest.raises(TypeError):
        discover_universe(lambda p: {"unexpected": "shape"}, cfg)
```

- [ ] **Step 2.6 — Run them, watch pass** (the impl already covers these).

Run: `./.venv/bin/pytest tests/test_runtime_discovery.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 2.7 — Add the default production fetch + a test that it targets `/markets`.**

```python
# tests/test_runtime_discovery.py  (append)
def test_make_gamma_fetch_hits_markets_endpoint(monkeypatch):
    from polybot.runtime import discovery
    captured = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return [{"ok": 1}]
    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url; captured["params"] = params
        return _Resp()
    monkeypatch.setattr(discovery.httpx, "get", fake_get)
    fetch = discovery.make_gamma_fetch("https://gamma.example")
    assert fetch({"limit": 3}) == [{"ok": 1}]
    assert captured["url"] == "https://gamma.example/markets"
    assert captured["params"] == {"limit": 3}
```

- [ ] **Step 2.8 — Run it, watch it fail** (`make_gamma_fetch` undefined).

Run: `./.venv/bin/pytest tests/test_runtime_discovery.py::test_make_gamma_fetch_hits_markets_endpoint -o addopts="" -q`
Expected: FAIL.

- [ ] **Step 2.9 — Implement `make_gamma_fetch`.**

```python
# src/polybot/runtime/discovery.py  (append)
def make_gamma_fetch(gamma_url: str, *, timeout: float = 30.0):
    """Default production SYNC fetch(params) -> list[dict] for discover_universe (Gamma /markets).
    Kept here (not in transport.py) so transport stays untouched and the fetch stays injectable."""
    def fetch(params):
        resp = httpx.get(f"{gamma_url}/markets", params=params, timeout=timeout,
                         headers={"user-agent": "polybot/0.1"})
        resp.raise_for_status()
        return resp.json()
    return fetch
```

- [ ] **Step 2.10 — Run the file, watch pass. Commit.**

```bash
./.venv/bin/pytest tests/test_runtime_discovery.py -o addopts="" -q
git add src/polybot/runtime/discovery.py tests/test_runtime_discovery.py
git commit -m "feat(runtime): discover_universe + make_gamma_fetch (D4a-2)"
```

---

## Task 3: `IngestionRuntime` supervision core (D4a-3) — the correctness-critical unit

**Files:**
- Create: `src/polybot/runtime/ingestion.py` (the `IngestionRuntime` class portion; `build`/`main` added in Task 4)
- Test: `tests/test_runtime_ingestion.py`

The supervision core runs N services (zero-arg async callables) + a best-effort heartbeat in ONE `asyncio.TaskGroup`.
`request_stop()` sets an event; an internal stopper task raises a `_StopRequested` sentinel to unwind the group
cleanly; a service that RAISES (a venue format-change HALT) is NOT the sentinel, so it propagates loudly. On EVERY
exit path a `finally` calls `writer.close()` (drain + join) — the durability invariant.

- [ ] **Step 3.1 — Write the failing test: all services are started concurrently.**

```python
# tests/test_runtime_ingestion.py
import asyncio
import pytest
from polybot.runtime.ingestion import IngestionRuntime


class FakeWriter:
    def __init__(self): self.close_calls = 0
    def close(self): self.close_calls += 1


def test_runs_all_services_then_stops_cleanly():
    started = []
    async def svc_a():
        started.append("a")
        await asyncio.sleep(3600)   # runs "forever" until cancelled
    async def svc_b():
        started.append("b")
        await asyncio.sleep(3600)
    w = FakeWriter()

    async def scenario():
        rt = IngestionRuntime(services=[svc_a, svc_b], writer=w)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)                # let both services start
        assert set(started) == {"a", "b"}
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)  # clean return, no exception

    asyncio.run(scenario())
    assert w.close_calls == 1                    # durability: closed exactly once
```

*Note (confirmed):* the repo has NO pytest-asyncio (no `asyncio_mode`, no `async def test_`). Async code is tested via
`asyncio.run(scenario())` inside a SYNC `test_` function — see `tests/test_sharding.py` / `tests/test_data_api.py`.
Every async scenario below follows that pattern; no plugin, no new dev dependency.

- [ ] **Step 3.2 — Run it, watch it fail** (`IngestionRuntime` undefined).

Run: `./.venv/bin/pytest tests/test_runtime_ingestion.py::test_runs_all_services_then_stops_cleanly -o addopts="" -q`
Expected: FAIL (ImportError).

- [ ] **Step 3.3 — Implement the core (composition + clean stop + finally-close).**

```python
# src/polybot/runtime/ingestion.py
"""Continuous ingestion runtime: supervise the S1 collectors in one event loop with durable shutdown.
IngestionRuntime is the PURE supervision core (fake services -> hermetic tests); build_ingestion_runtime + main
(Task 4) do the live wiring + entry point. Additive; imports only ingestion/storage/core/ers.heartbeat."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("polybot.ingestion")


class _StopRequested(Exception):
    """Internal sentinel raised by the stopper task to unwind the TaskGroup on an operator-requested stop
    (distinct from a collector HALT, which must propagate loudly)."""


class IngestionRuntime:
    def __init__(self, *, services, writer, heartbeat=None,
                 heartbeat_interval_seconds: float = 5.0, sleep=asyncio.sleep):
        self._services = list(services)        # zero-arg callables -> awaitable
        self._writer = writer
        self._heartbeat = heartbeat
        self._heartbeat_interval = heartbeat_interval_seconds
        self._sleep = sleep
        self._stop: asyncio.Event | None = None

    def request_stop(self) -> None:
        # Loop-safe (Event.set) so a signal handler can call it. Idempotent; no-op before run().
        if self._stop is not None:
            self._stop.set()

    async def run(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with asyncio.TaskGroup() as tg:
                for factory in self._services:
                    tg.create_task(factory())
                if self._heartbeat is not None:
                    tg.create_task(self._heartbeat_loop())
                tg.create_task(self._stopper())
        except* _StopRequested:
            pass  # clean, operator-requested stop; a real service error is NOT _StopRequested -> propagates
        finally:
            self._writer.close()  # idempotent drain+join; durability invariant (runs on EVERY path)

    async def _stopper(self) -> None:
        assert self._stop is not None
        await self._stop.wait()
        raise _StopRequested()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._heartbeat.beat()
            except Exception:  # best-effort liveness: a beat hiccup must never kill ingestion
                log.exception("heartbeat beat failed")
            await self._sleep(self._heartbeat_interval)
```

- [ ] **Step 3.4 — Run it, watch it pass.**

Run: `./.venv/bin/pytest tests/test_runtime_ingestion.py::test_runs_all_services_then_stops_cleanly -o addopts="" -q`
Expected: PASS.

- [ ] **Step 3.5 — Write the failing test: a raising service HALTs loudly AND still closes the writer.**

```python
# tests/test_runtime_ingestion.py  (append)
def test_service_halt_propagates_and_still_closes_writer():
    async def good():
        await asyncio.sleep(3600)
    async def halting():
        raise RuntimeError("unknown event_type: format change")   # a venue HALT
    w = FakeWriter()

    async def scenario():
        rt = IngestionRuntime(services=[good, halting], writer=w)
        await asyncio.wait_for(rt.run(), timeout=1)

    with pytest.raises(BaseExceptionGroup) as ei:
        asyncio.run(scenario())
    # the RuntimeError HALT is inside the group (not swallowed as a clean stop)
    assert any(isinstance(e, RuntimeError) for e in ei.value.exceptions)
    assert w.close_calls == 1        # durability on crash
```

- [ ] **Step 3.6 — Run it, watch it pass** (the `except* _StopRequested` deliberately does NOT catch `RuntimeError`, so it propagates; the `finally` still closed the writer).

Run: `./.venv/bin/pytest tests/test_runtime_ingestion.py::test_service_halt_propagates_and_still_closes_writer -o addopts="" -q`
Expected: PASS.

- [ ] **Step 3.7 — Write the failing test: the heartbeat beats while running, and a beat error does NOT kill the runtime.**

```python
# tests/test_runtime_ingestion.py  (append)
class FakeHeartbeat:
    def __init__(self, *, fail=False): self.beats = 0; self._fail = fail
    def beat(self):
        self.beats += 1
        if self._fail:
            raise OSError("disk full")


def test_heartbeat_beats_and_survives_beat_errors():
    async def svc():
        await asyncio.sleep(3600)
    w = FakeWriter()
    hb = FakeHeartbeat(fail=True)     # every beat raises
    async def fast_sleep(_):          # instant sleep so the heartbeat loop spins fast
        await asyncio.sleep(0)

    async def scenario():
        rt = IngestionRuntime(services=[svc], writer=w, heartbeat=hb,
                              heartbeat_interval_seconds=0.001, sleep=fast_sleep)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)   # survived the beat errors -> clean stop

    asyncio.run(scenario())
    assert hb.beats > 0
    assert w.close_calls == 1
```

- [ ] **Step 3.8 — Run it, watch it pass.**

Run: `./.venv/bin/pytest tests/test_runtime_ingestion.py::test_heartbeat_beats_and_survives_beat_errors -o addopts="" -q`
Expected: PASS.

- [ ] **Step 3.9 — Run the whole file + confirm additive invariant. Commit.**

```bash
./.venv/bin/pytest tests/test_runtime_ingestion.py -o addopts="" -q
git status --porcelain      # only new runtime/ + test files
git add src/polybot/runtime/ingestion.py tests/test_runtime_ingestion.py
git commit -m "feat(runtime): IngestionRuntime supervision core + durable shutdown (D4a-3)"
```

> **Reviewer note (D4a-3 is the opus-mutation target):** mutate and confirm a named test fails —
> (1) delete the `finally: self._writer.close()` → `test_service_halt_propagates_and_still_closes_writer` +
> `test_runs_all_services_then_stops_cleanly` must fail on `close_calls`; (2) change `except* _StopRequested` to
> `except* Exception` → `test_service_halt_propagates...` must fail (HALT wrongly swallowed); (3) drop the
> heartbeat try/except → `test_heartbeat_beats_and_survives_beat_errors` must fail.

---

## Task 4: `build_ingestion_runtime` + `main` + entry point + smokes (D4a-4)

**Files:**
- Modify: `src/polybot/runtime/ingestion.py` (append `build_ingestion_runtime`, `main`, the `__main__` guard)
- Create: `scripts/ingestion_runtime_check.py` (manual live smoke — NOT in the suite)
- Test: `tests/test_runtime_build.py`

- [ ] **Step 4.1 — Write the failing test: build wires WS + Data API services and the durable store.**

```python
# tests/test_runtime_build.py
import pytest
from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import IngestionRuntime, build_ingestion_runtime

def _rows():
    return [{"conditionId": "c1", "acceptingOrders": True, "volume24hr": 9.0,
             "active": True, "closed": False,
             "clobTokenIds": '["t1", "t2"]', "outcomes": '["Yes", "No"]',
             "outcomePrices": '["0.5", "0.5"]'}]

def test_build_wires_services_and_store(tmp_path):
    cfg = IngestionConfig(db_path=str(tmp_path / "m.db"), universe_max_markets=5)
    rt = build_ingestion_runtime(
        cfg,
        gamma_fetch=lambda params: _rows(),
        ws_connect=object(),          # never dialed in this test (we don't run())
        data_fetch=object(),
    )
    assert isinstance(rt, IngestionRuntime)
    assert len(rt._services) == 2                       # ws + data-api
    assert (tmp_path / "m.db").exists()                 # EventStore created at db_path

def test_build_omits_data_api_when_disabled(tmp_path):
    cfg = IngestionConfig(db_path=str(tmp_path / "m.db"), universe_max_markets=5,
                          data_api_enabled=False)
    rt = build_ingestion_runtime(cfg, gamma_fetch=lambda params: _rows(),
                                 ws_connect=object(), data_fetch=object())
    assert len(rt._services) == 1                       # ws only
```

- [ ] **Step 4.2 — Run it, watch it fail** (`build_ingestion_runtime` undefined).

Run: `./.venv/bin/pytest tests/test_runtime_build.py -o addopts="" -q`
Expected: FAIL.

- [ ] **Step 4.3 — Implement `build_ingestion_runtime` + `main` (append imports at top of `ingestion.py`).**

```python
# src/polybot/runtime/ingestion.py  (add these imports at the TOP, with the existing ones)
import signal
import sys

from polybot.core.clock import MonotonicStamper
from polybot.ers.heartbeat import Heartbeat
from polybot.ingestion.data_api import DataApiPoller
from polybot.ingestion.persistence import PersistingSink
from polybot.ingestion.sharding import ShardedMarketCollector
from polybot.ingestion.transport import DATA_API_URL, WS_RECONNECT_ON, make_httpx_fetch, open_market_ws
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore
from polybot.runtime.config import IngestionConfig, load_config
from polybot.runtime.discovery import discover_universe, make_gamma_fetch
```

```python
# src/polybot/runtime/ingestion.py  (append at module level, AFTER the IngestionRuntime class)
def build_ingestion_runtime(config: IngestionConfig, *, gamma_fetch=None, ws_connect=None,
                            data_fetch=None, stamper=None) -> IngestionRuntime:
    """Live wiring: discover the universe, then one QueuedEventWriter(EventStore) fed by the sharded WS collector
    (+ the Data API /trades poller when enabled). Injectable seams default to the real transport factories."""
    stamper = stamper or MonotonicStamper()
    gamma_fetch = gamma_fetch or make_gamma_fetch(config.gamma_url)
    ws_connect = ws_connect or open_market_ws
    data_fetch = data_fetch or make_httpx_fetch(DATA_API_URL)

    token_ids = discover_universe(gamma_fetch, config)
    writer = QueuedEventWriter(EventStore(config.db_path, check_same_thread=False))

    ws = ShardedMarketCollector(ws_connect, stamper, token_ids, sink=PersistingSink(writer),
                                max_assets_per_shard=config.max_assets_per_shard,
                                reconnect_on=WS_RECONNECT_ON)
    services = [lambda: ws.run(max_connections=None)]   # None => reconnect forever (production)

    if config.data_api_enabled:
        poller = DataApiPoller(data_fetch, stamper, writer)
        services.append(lambda: poller.run("/trades", params={"limit": config.data_api_limit},
                                           interval=config.data_api_interval_seconds))

    heartbeat = Heartbeat(config.heartbeat_path) if config.heartbeat_path else None
    return IngestionRuntime(services=services, writer=writer, heartbeat=heartbeat,
                            heartbeat_interval_seconds=config.heartbeat_interval_seconds)


async def _amain(runtime: IngestionRuntime) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass  # add_signal_handler is POSIX-only; the VPS is Linux, dev-on-Windows just Ctrl-C's
    await runtime.run()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="polybot-ingestion")
    parser.add_argument("--config", default=None, help="path to an ingestion TOML config")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(level=config.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runtime = build_ingestion_runtime(config)
    try:
        asyncio.run(_amain(runtime))
        return 0
    except Exception:  # a collector HALT surfaces as an ExceptionGroup -> non-zero for systemd Restart=on-failure
        log.exception("ingestion runtime halted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4.4 — Run the build tests, watch pass.**

Run: `./.venv/bin/pytest tests/test_runtime_build.py -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4.5 — Write the failing test: `main` loads a config + builds + runs, and a bad `--config` fails loud.** (Patch `build_ingestion_runtime` to a fake runtime so `main` doesn't touch the network.)

```python
# tests/test_runtime_build.py  (append)
def test_main_builds_and_runs_then_clean_exit(tmp_path, monkeypatch):
    from polybot.runtime import ingestion
    toml = tmp_path / "c.toml"
    toml.write_text(f'db_path = "{tmp_path / "m.db"}"\n')
    class _FakeRuntime:
        def __init__(self): self.ran = False
        def request_stop(self): pass
        async def run(self): self.ran = True
    fake = _FakeRuntime()
    monkeypatch.setattr(ingestion, "build_ingestion_runtime", lambda cfg: fake)
    rc = ingestion.main(["--config", str(toml)])
    assert rc == 0 and fake.ran is True

def test_main_missing_config_file_fails_loud(tmp_path):
    from polybot.runtime import ingestion
    with pytest.raises(FileNotFoundError):
        ingestion.main(["--config", str(tmp_path / "nope.toml")])
```

- [ ] **Step 4.6 — Run them, watch pass** (the impl already supports this; `load_config` raises `FileNotFoundError` on a missing file, which surfaces before the `try`).

Run: `./.venv/bin/pytest tests/test_runtime_build.py -k main -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4.7 — Write the hermetic end-to-end durability test: a real EventStore persists a row under the runtime lifecycle, and readback works after close.**

```python
# tests/test_runtime_build.py  (append)
import asyncio
from polybot.core.models import Envelope
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore
from polybot.runtime.ingestion import IngestionRuntime

def test_end_to_end_durable_persist_and_close(tmp_path):
    db = str(tmp_path / "e2e.db")
    writer = QueuedEventWriter(EventStore(db, check_same_thread=False))
    async def producer():                       # stands in for a collector: append one durable row
        writer.append(Envelope(source="test", source_tier="VENUE", event_id="e1",
                               observed_at=1, content="{}", published_at=None,
                               market_links=("t1",)))
        await asyncio.sleep(3600)

    async def scenario():
        rt = IngestionRuntime(services=[producer], writer=writer)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)  # graceful close drains + joins the writer

    asyncio.run(scenario())
    with EventStore(db) as store:                # fresh main-thread connection after the writer joined
        rows = store.all()
    assert len(rows) == 1 and rows[0].event_id == "e1"
```

*(Confirm the `Envelope` constructor kwargs against `polybot/core/models.py` before writing — match its exact
required fields; the `PersistingSink`/`DataApiPoller` usages above are the canonical field set.)*

- [ ] **Step 4.8 — Run it, watch pass.**

Run: `./.venv/bin/pytest tests/test_runtime_build.py::test_end_to_end_durable_persist_and_close -o addopts="" -q`
Expected: PASS.

- [ ] **Step 4.9 — Add the manual live smoke script** (mirrors the existing `scripts/*_check.py`; NOT run in CI).

```python
# scripts/ingestion_runtime_check.py
"""Manual live smoke for the ingestion runtime (POL-13 / D4a). Read-only; discovers a few live markets,
runs the real runtime for ~8s, asserts rows landed. Run: ./.venv/bin/python scripts/ingestion_runtime_check.py"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import build_ingestion_runtime
from polybot.storage.market_memory import EventStore


async def main():
    db = tempfile.mktemp(suffix=".db")
    cfg = IngestionConfig(db_path=db, universe_max_markets=5, data_api_interval_seconds=2.0)
    rt = build_ingestion_runtime(cfg)
    task = asyncio.create_task(rt.run())
    await asyncio.sleep(8)
    rt.request_stop()
    await asyncio.wait_for(task, timeout=5)
    with EventStore(db) as store:
        rows = store.all()
    print(f"persisted {len(rows)} rows; sources={sorted({r.source for r in rows})}")
    assert rows, "no rows captured — check connectivity / venue"


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4.10 — Full suite + additive invariant. Commit.**

```bash
./.venv/bin/pytest -o addopts="" -q                 # expect: 1113 + new tests passed, exit 0
git diff --name-only main -- src/                    # only src/polybot/runtime/* (new)
git add src/polybot/runtime/ingestion.py scripts/ingestion_runtime_check.py tests/test_runtime_build.py
git commit -m "feat(runtime): build_ingestion_runtime + main + entry + smokes (D4a-4)"
```

---

## Execution notes

- **Async test convention (confirmed):** the repo has NO pytest-asyncio — async code is tested via `asyncio.run(scenario())`
  inside SYNC `test_` functions (see `tests/test_sharding.py`, `tests/test_data_api.py`). All async tests in this plan
  follow that; no plugin, no dev-config change (keeps the additive invariant intact).
- **`Envelope` fields:** confirm `polybot/core/models.py` for the exact `Envelope` constructor before the e2e test.
- **Per sub-slice:** strict TDD (observe each true RED) → (1) general-purpose spec-compliance reviewer (READ + RUN +
  the additive invariant) → (2) pinned `model:opus` `superpowers:code-reviewer` with the mutation battery (D4a-3 gets
  the durability mutations above). RE-REVIEW after any fix. Sweep `__pycache__` after reverting any mutation.
- **Whole slice:** a final pinned-opus review with a cross-cutting mutation → update HANDOFF/memory + the POL comment
  → merge `--no-ff` with the verification status → CONFIRM before pushing.
- **Deferred to D4a.2 (do NOT build here):** Polygon watcher, news + CalendarScheduler, SyntheticDetector wiring,
  dynamic universe refresh, HALT alerting. **Deferred to the Phase-0 ops slice:** the `polybot` user, the uv/3.13
  venv, `/opt/polymarket-bot`, the bare repo, the systemd unit.
