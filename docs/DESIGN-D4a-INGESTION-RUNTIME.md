# DESIGN — D4a: Continuous ingestion runtime (shadow-deployment, slice 1)

**Date:** 2026-07-05 · **Ticket:** POL-13 (shadow deployment — slice D4a; tracking ticket TBD) ·
**Status:** Original runtime design landed. **D4a-downsample amendment is code-complete, independently reviewed,
and release-gate verified on the POL-13 feature branch. Merge, push, installation, and activation remain separately
approval-gated. The VPS service remains STOPPED + DISABLED.**

> **2026-07-10 persistence amendment:** production no longer persists raw CLOB frames. `MarketStream` maintains
> live books in memory; one versioned `clob-midpoint` batch is sampled every 60 seconds and stored alongside the
> full deduplicated Data API trade tape. Synthetic events are not reconstructable from this compact history and
> remain deferred pending a tuned live contract. The immutable release ceiling is **≤0.5 GiB/day** for total
> DB+WAL+SHM projected over the required 30-minute real-venue run. **The release gate passed on 2026-07-10:**
> 1,800.006 seconds, 5,586,944 bytes, source counts `{"clob-midpoint":29,"data-api":3500}`, 1,800 usable quotes,
> exactly zero raw rows, all batches decoded, no HALT, graceful close, and **0.249755 GiB/day** (exit 0).
> Earlier evidence remains diagnostic: two 70-second default-ceiling probes failed at 0.866285 and 1.723114 GiB/day
> under startup/full-page distortion; a five-market E3 smoke passed at 300.006 seconds / 1,515,520 bytes /
> 0.406486 GiB/day; and a lifecycle-only run used a permissive 5.0 smoke ceiling. Only the 1,800-second result is
> release evidence.
> The old raw database must be preserved under an evidence filename before a fresh corrected database is started.
**Depends on:** S1 ingestion (POL-3) + the off-loop `EventStore` writer (POL-12) — every collector, the
`EventStore`, the `QueuedEventWriter`, the `MonotonicStamper`, and `ingestion/transport.py` already exist and are
tested. D4a is a **thin supervision/wiring layer over already-tested components**. **Runs READ-ONLY.** Nothing
signs, sizes, proposes, or trades — it only ingests public market data into the durable store.

> Master design CONTEXT §5 (architecture) + the load-bearing landmine (CONTEXT §7 / master §7): *"Self-snapshot
> market data from day one — Polymarket's `/prices-history` is lossy after resolution and order-book history is
> dead. You cannot backfill it later."* D4a is the operational realization of that mandate and the first slice of
> POL-11's deferred "actually RUN the shadow period" work: stand up **continuous, bounded capture of the
> 60-second midpoint substrate + full deduplicated trade tape** on the clean VPS, in an isolated footprint, before
> anything reasons or trades. Raw order-book history is intentionally not retained: live books exist in memory and
> are sampled into versioned batches. It is Phase 0 of the deploy: prove the plumbing + the deploy environment on the
> smallest possible surface, then layer the Hermes→ERS shadow loop (D1–D4b) on top.

---

## 0. TL;DR + resolved forks

D4a is a self-contained package `src/polybot/runtime/` — a small config surface, a pure universe-discovery
function, and a supervision core that runs the existing async collectors plus the midpoint sampler in one
`asyncio.TaskGroup` with durable, signal-driven shutdown. It retains the compact, point-in-time substrate needed by
the shadow loop: live sharded CLOB books are maintained in memory, one versioned midpoint batch is persisted every
60 seconds, and the deduplicated Data API trade tape is retained in full. It is deployable as an isolated `polybot`
user systemd service on the VPS. It is **read-only** — no component reasons, signs, or trades.

**The durability spine:** every buffered row reaches SQLite on any *clean* exit. `IngestionRuntime.run()` guarantees
`writer.close()` (drain the queue → join the writer thread → re-raise) runs in a `finally` on cancellation OR on a
collector HALT. Only a hard `SIGKILL` can lose the in-flight queue (bounded, ~0 in steady state). SIGTERM (systemd
stop) is graceful.

**Resolved forks (operator-confirmed 2026-07-05, "whatever makes most sense" → ingestion-first minimal cut):**

| # | Fork | Decision |
|---|---|---|
| 1 | Scope | **Minimal and bounded.** Gamma discovery → sharded CLOB WS maintains live books in memory → one versioned 60-second midpoint batch; Data API `/trades` retains the full deduplicated trade tape. Raw `clob-ws` frames are never sent to the production writer. |
| 2 | Universe | **Top-N active binary markets by 24h volume** (default N=400 → ~800 tokens → ~2 shards). Bounds WS load; focuses capture on tradeable liquidity. `discover-once at startup`; restart-to-refresh (no live re-subscription in v1 — the collector shards at construction). |
| 3 | Trade capture | **Data API `/trades` polled as a GLOBAL recency feed** (one request per interval), NOT per-market fan-out (400 markets × 2 s = 200 req/s would be rate-limited). Keep all returned trades and rely on EventStore event-ID deduplication; WS trade frames may update live in-memory state but are not persisted. |
| 4 | Testability | **Three layers.** A PURE `IngestionRuntime` supervision core (fake services → hermetic lifecycle/shutdown tests) + a thin `build_ingestion_runtime` production factory (real wiring, one bounded integration smoke) + a `main`/entry point. Matches the project's "pure unit + deferred live integration behind seams" doctrine. |

**Baked (doctrine-forced, not asked):** ONE process-wide `MonotonicStamper` → strictly-increasing global `observed_at`
across all sources (replay-safe, no look-ahead — S1 invariant, preserved). The off-loop `QueuedEventWriter` is the
only writer. **Fail-loud on a format change:** an unknown WS `event_type` tears the `TaskGroup` down loudly (S1
doctrine) — the `finally` still flushes the writer, and systemd `Restart=on-failure` with a start-limit stops a
*persistent* HALT from crash-looping (it stays down for a human). Config fails LOUD at construction. Purely additive.

---

## 1. Goal & non-goals

**Goal:** the deterministic, testable machinery to run S1's ingestion **continuously and durably** as one supervised
process — a self-verifying `IngestionConfig` + loader, a pure `discover_universe`, and an `IngestionRuntime` that
composes the sharded WS collector + the Data API poller in one event loop with a periodic liveness heartbeat and a
graceful, durability-preserving shutdown — plus the thin production factory + entry point that make it
`systemctl`-able. Concretely the units in §4.

**Non-goals (deferred; §6):** the Polygon on-chain watcher, the news fast-path + `CalendarScheduler`, dynamic
universe refresh, and synthetic-event persistence. Synthetic events can no longer be reconstructed from production
raw history because raw frames are intentionally absent; they require a separately designed, tuned live contract.
The VPS kit exists, but activation of the corrected runtime is an operations action gated on review, the 30-minute
rate test, merge/push approval, old-DB evidence preservation, and separate service start/enable approval. Anything
Hermes/ERS/shadow-loop (D1–D4b) remains outside this runtime slice.

## 2. Architecture

```
src/polybot/runtime/  (NEW package — additive; the composition root that did not exist before)

  config.py     IngestionConfig (frozen, self-verifying) + load_config(toml?, env) — the FIRST config surface in
                the repo (all components were pure-DI). db_path, universe cap, shard size, poll/heartbeat intervals,
                Gamma URL, log level. Fails LOUD on invalid values.
  discovery.py  discover_universe(fetch, config) -> list[str] — Gamma /markets (active, binary, acceptingOrders),
                ranked by 24h volume, top-N, flat de-duped clobTokenIds. Pure but for the injected `fetch`; skips
                individually-malformed rows (fault-isolated); fails LOUD on a Gamma schema change.
  ingestion.py  IngestionRuntime(services, writer, heartbeat, clock, ...) — the PURE supervision core: runs every
                service.run() in ONE asyncio.TaskGroup + a heartbeat task; request_stop() cancels; writer.close()
                in finally (drain + join) on ANY exit. No network, no wiring -> hermetic tests with fake services.
                build_ingestion_runtime(config, *fetch/connect seams) -> IngestionRuntime — the real wiring:
                discover_universe -> QueuedEventWriter(EventStore(db_path, check_same_thread=False)) ->
                ShardedMarketCollector + DataApiPoller as services. Thin; leans on already-tested collectors.
                main(argv) -> int — load_config, build, install SIGTERM/SIGINT -> request_stop, asyncio.run(run()).

  entry point:  python -m polybot.runtime.ingestion   (the module's __main__ guard calls main())

  (reuses: ingestion.sharding.ShardedMarketCollector, ingestion.data_api.DataApiPoller,
   ingestion.market_stream.MarketStream, ingestion.midpoint.MidpointSnapshotter, ingestion.transport.*,
   storage.event_writer.QueuedEventWriter, storage.market_memory.EventStore, core.clock.MonotonicStamper,
   ers.heartbeat.* for the liveness file.)
```

- **One event loop, one writer thread, one stamper.** The sharded WS collector maintains live books in memory; the
  midpoint snapshotter and Data API poller are sibling supervised services. Only the snapshotter and poller feed the
  one `QueuedEventWriter`. The one `MonotonicStamper` preserves globally increasing `observed_at` values.
- **The raw path ends in memory.** WS `book`/`price_change`/`last_trade_price` frames update `MarketStream`; there is no
  production `PersistingSink` on that path. Every 60 seconds `MidpointSnapshotter` obtains usable non-stale books,
  validates exact decimal strings, and emits one versioned batch. Data API `/trades` rows go directly to the writer
  and retain their existing EventStore event-ID deduplication.
- **Data-flow:** `Gamma /markets → token_ids` → sharded CLOB WS → in-memory `MarketStream` → periodic
  `clob-midpoint` batch → writer → SQLite WAL; Data API global `/trades` → same writer. A snapshot writer error or
  collector return propagates as a supervised HALT, after which the writer is closed and drained.

## 3. Durability & lifecycle contract (the load-bearing behaviour)

| Event | Behaviour |
|---|---|
| Normal run | All services run forever in the TaskGroup; heartbeat file rewritten every `heartbeat_interval_seconds`. |
| SIGTERM / SIGINT (systemd stop) | `main`'s handler → `runtime.request_stop()` → TaskGroup cancelled → `finally: writer.close()` drains the queue + joins the writer thread. **Zero rows lost.** Exit 0. |
| A collector HALT (unknown WS `event_type` = venue format change) | TaskGroup raises `ExceptionGroup` → `finally: writer.close()` still flushes buffered rows → runtime re-raises loudly → `main` returns non-zero → systemd `Restart=on-failure`; a *persistent* HALT trips the start-limit and stays down for a human (no crash-loop). |
| Transient WS disconnect | Absorbed **inside** `MarketSocket`/`ShardedMarketCollector` (reconnect + resync, S1) — does not reach the runtime; only that shard's books go briefly stale. |
| SIGKILL | The only lossy exit: the in-flight queue (bounded by `max_queued`, ~0 in steady state) is dropped. Documented; systemd uses SIGTERM. |

**Guarantee under test:** `writer.close()` is called **exactly once** on every code path out of `run()` (clean stop,
collector raise, cancellation). This is the durability invariant and gets dedicated tests (A4/A5).

## 4. Net-new units (the pinned contract block)

```python
# runtime/config.py
@dataclass(frozen=True)
class IngestionConfig:
    db_path: str                                  # durable EventStore path (e.g. /opt/polymarket-bot/data/market_memory.db)
    universe_max_markets: int = 400               # top-N active binary markets by 24h volume
    max_assets_per_shard: int = 500               # ShardedMarketCollector shard size
    data_api_enabled: bool = True
    data_api_interval_seconds: float = 2.0
    data_api_limit: int = 500                     # /trades page size (global recency feed)
    snapshot_interval_seconds: float = 60.0       # one versioned midpoint batch per cadence
    heartbeat_path: str | None = None             # None => no heartbeat file
    heartbeat_interval_seconds: float = 5.0
    gamma_url: str = GAMMA_URL                     # from ingestion.transport
    log_level: str = "INFO"
    # __post_init__ (self-verifying, fail LOUD): non-empty db_path; universe_max_markets >= 1;
    # max_assets_per_shard >= 1; intervals finite and > 0; log_level in the logging level set.

def load_config(toml_path: str | None = None, *, env: Mapping[str, str] = os.environ) -> IngestionConfig:
    """Overlay: defaults <- optional TOML file <- POLYBOT_INGEST_* env vars; then IngestionConfig(**merged),
    which self-verifies. The only place env/files are read (keeps every component pure-DI)."""

# runtime/discovery.py
def discover_universe(fetch, config: IngestionConfig) -> list[str]:
    """fetch(params: dict) -> list[dict] (raw Gamma market rows). Filter to active + binary (2 outcomes) +
    acceptingOrders; normalize via ingestion.gamma.normalize_market; rank by 24h volume DESC; take top
    config.universe_max_markets; return the flat, de-duplicated list of clobTokenIds (str). Skips a row that
    fails to normalize (fault-isolated). Fails LOUD (not silently empty) if the response shape is unrecognizable."""

# runtime/ingestion.py
class IngestionRuntime:
    def __init__(self, *, services: Sequence[Service], writer, heartbeat=None,
                 clock, heartbeat_interval_seconds: float = 5.0) -> None:
        # services: objects each exposing `async run(self) -> None` (the collectors). writer: the QueuedEventWriter
        # (ONLY .close() is called here). heartbeat: optional liveness writer (ers.heartbeat). clock: time source.

    async def run(self) -> None:
        # Enter ONE asyncio.TaskGroup: one task per service + one heartbeat task. On request_stop OR any service
        # raising, exit the group; a `finally` calls writer.close() EXACTLY ONCE (drain queue, join thread, re-raise
        # a writer error). A service ExceptionGroup is re-raised loudly after the writer is flushed.

    def request_stop(self) -> None:
        # idempotent; signals the group to cancel (an asyncio.Event / cancelling the heartbeat gate).

def build_ingestion_runtime(config: IngestionConfig, *, gamma_fetch=None, ws_connect=None,
                            data_fetch=None, clock=None) -> IngestionRuntime:
    # Real wiring. Defaults: gamma_fetch -> httpx GET on config.gamma_url/markets; ws_connect -> transport.open_market_ws;
    # data_fetch -> transport.make_httpx_fetch(DATA_API_URL); clock -> MonotonicStamper()'s clock.
    #   token_ids = discover_universe(gamma_fetch, config)
    #   writer    = QueuedEventWriter(EventStore(config.db_path, check_same_thread=False))
    #   ws        = ShardedMarketCollector(ws_connect, stamper, token_ids, sink=None,
    #                                      max_assets_per_shard=config.max_assets_per_shard, reconnect_on=WS_RECONNECT_ON)
    #   snapshot  = MidpointSnapshotter(token_ids=token_ids, book_for=ws.book_for, stamper=stamper,
    #                                   writer=writer, interval_seconds=config.snapshot_interval_seconds)
    #   data      = DataApiPoller(data_fetch, stamper, writer)   # global /trades when enabled
    #   return IngestionRuntime(services=[ws, snapshot, optional data], writer=writer, heartbeat=..., clock=...)

def main(argv: Sequence[str] | None = None) -> int:
    # load_config(argv's --config) -> build_ingestion_runtime -> loop.add_signal_handler(SIGTERM/SIGINT, request_stop)
    # -> asyncio.run(runtime.run()); return 0 on clean stop, non-zero on a HALT. python -m polybot.runtime.ingestion.
```

*A `Service` is a tiny adapter (`async run()`) wrapping a collector's own `run(...)` with its production args
(`ws.run(max_connections=None)`, `poller.run("/trades", interval=...)`), so `IngestionRuntime` stays agnostic to
collector-specific signatures and is trivially fakeable in tests.*

## 5. Safety / correctness invariants

1. **Read-only.** No signer, no IntentStore, no order path. The runtime composition root imports only ingestion,
   storage, core, and heartbeat surfaces. The amendment's isolated `ers/comove.py` adapter reads EventStore data and
   the midpoint decoder; it does not touch validator, service, caps, safety state, or signer behavior.
2. **Durability:** `writer.close()` called **exactly once** on every exit path (A4/A5). No row lost except on SIGKILL.
3. **Global ordering:** one `MonotonicStamper` for all sources → strictly-increasing unique `observed_at`
   (replay/no-look-ahead invariant from S1, preserved).
4. **Fail loud:** config invalid → construction raises; a venue format change → HALT propagates (never silently
   swallowed); a Gamma schema change in discovery → raise, never a silently-empty universe.
5. **File-scope evidence.** The original landed D4a runtime was additive under `src/polybot/runtime/**`. The later
   POL-13 downsample amendment intentionally adds `src/polybot/ingestion/midpoint.py`, modifies runtime config/wiring,
   and changes only the isolated `src/polybot/ers/comove.py` replay adapter outside those packages. Signed risk,
   validation, service, intent, safety, and signer surfaces remain byte-identical to `main`.
6. **Bounded footprint:** the universe is capped (top-N); the writer queue is bounded (`max_queued`); the poller is a
   single global request per interval — no unbounded fan-out.

## 6. Built-now vs deferred

**Built now:** original D4a runtime plus the POL-13 downsample amendment: WS books remain live in memory with
`sink=None`; `MidpointSnapshotter` writes one strict, versioned batch at the configured 60-second cadence; Data API
trade payload/projection/dedup behavior is unchanged; `build_bar_series` auto-selects midpoint rows while retaining
explicit legacy raw replay. Unit/integration, independent spec, and 41-mutation gates are green; the real
1,800-second release gate passed at 0.249755 GiB/day. **Code acceptance is complete. Merge, push, old-DB evidence
preservation, installation, and service activation remain separate approval-gated actions.**

**Deferred — D4a.2:** the `PolygonLogWatcher` service (on-chain logs are re-queryable by block range) · the
`NewsPoller` + `CalendarScheduler` (feeds re-fetchable; only matters once Hermes reads them) · **synthetic-event
persistence** (not recomputable from the compact production substrate; requires a tuned live contract) · dynamic
universe refresh (live re-subscription / periodic re-discovery + graceful collector rebuild) · HALT alerting
(Telegram/S4.6 notify).

**Operations state:** the VPS user/tree/unit were previously installed, but the service is **STOPPED + DISABLED**
after the raw firehose measured roughly 30 GB/day. Deployment of this corrected build is not approved. It must use
the GitHub-authoritative checkout flow (no recreated `/root/git/polymarket-bot.git`), preserve and hash the old raw
DB under an evidence filename, start from a fresh `market_memory.db`, and keep the unit stopped until separate
start/enable approval.

**Deferred — later slices:** D1 MarketRegistry · D2 resolution feed · D3 shadow-execution wiring · D4b the full
ERS+harness runtime.

## 7. Acceptance criteria

| # | Criterion |
|---|---|
| A1 | `discover_universe` with a fake `fetch`: returns the correct top-N `clobTokenIds`, ranked by 24h volume, filtered to active+binary+acceptingOrders, de-duplicated, with a malformed row skipped (not fatal). |
| A2 | `IngestionConfig` fails LOUD (`ValueError`) on: empty `db_path`, `universe_max_markets < 1`, `max_assets_per_shard < 1`, non-finite/≤0 intervals, unknown `log_level`. `load_config` overlays TOML + env then self-verifies. |
| A3 | `IngestionRuntime.run()` enters `run()` on every one of N fake services concurrently (all observed running before stop). |
| A4 | `request_stop()` (and a SIGTERM path) → every service cancelled → `writer.close()` called **exactly once**; clean return. |
| A5 | A fake service that RAISES → `run()` re-raises loudly **and** `writer.close()` still called **exactly once** (durability on crash). |
| A6 | With a `heartbeat` configured, the heartbeat file is (re)written at least once while running. |
| A7 | `build_ingestion_runtime` wires the REAL collectors and, in a bounded integration smoke (few seconds, live venue or a local WS fake), persists ≥1 row end-to-end through the durable store. |
| A8 | **Historical original-runtime criterion:** the landed D4a implementation was limited to runtime/tests/docs and raised the baseline above 1113. **Amendment evidence:** the POL-13 downsample range is the explicit file surface in `DESIGN-D4a-DOWNSAMPLE.md` §6; full suite, sacred-surface hashes, and structural no-trading checks must pass. |

## 8. Sub-slice decomposition (build order — strict TDD, one implementer each, serial on the branch)

- **D4a-1 · config** — `IngestionConfig` (frozen, self-verifying) + `load_config` (TOML+env overlay). RED: an invalid
  value must raise; a valid overlay must round-trip. (sonnet; mechanical + well-specified.)
- **D4a-2 · discovery** — `discover_universe(fetch, config)`: the rank/cap/filter/normalize/dedupe/fault-isolation
  logic against an injected fake `fetch`. RED per behaviour (ranking, cap, binary-only, acceptingOrders, malformed-skip,
  schema-change-raise). (sonnet.)
- **D4a-3 · supervision core** — `IngestionRuntime`: TaskGroup composition, `request_stop`, the `finally`
  writer.close-exactly-once on clean-stop / raise / cancel, the heartbeat task. The correctness-critical unit — the
  durability invariant lives here. RED with fake services + a fake writer counting `close()`. (**opus**; the shutdown
  ordering + exactly-once is the subtle part — mutation-test A4/A5.)
- **D4a-4 · factory + entry** — `build_ingestion_runtime` (real wiring), `main` (config load + signal handlers +
  `asyncio.run`), the `python -m polybot.runtime.ingestion` entry, and the one bounded integration smoke (A7). Thin;
  lighter unit tests over the wiring + the smoke. (sonnet; opus review still pinned.)

Each sub-slice: strict TDD (observe each true RED) → two-stage review (general-purpose spec-compliance verifying by
READING + RUNNING + the additive invariant, THEN a pinned `model:opus` `superpowers:code-reviewer` with a mutation
battery on the durability tests) → RE-REVIEW after any fix. Final whole-slice opus review with a cross-cutting
mutation before merge `--no-ff`. Confirm before pushing.
