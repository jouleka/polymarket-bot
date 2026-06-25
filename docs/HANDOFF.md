# HANDOFF — continue POL-3 (S1 ingestion) on the autonomous Polymarket bot

You are picking up an in-progress build. Read this top to bottom, then read the linked docs, then
start at **"Your immediate task"**. Do not skip the conventions — they are enforced.

---

## 1. What this project is
A fully-autonomous, 24/7 **Polymarket** prediction-market trading bot. Architecture splits brain from hands:
- **Hermes** (NousResearch open-source *agent harness*, NOT an LLM model) = reasoning brain. It gets
  **only read tools + ONE write tool `propose_trade(...)`** that does nothing but INSERT a PENDING row.
  It never holds a key and cannot sign/submit/cancel/move funds.
- **ERS** (Execution & Risk Service, deterministic Python) = the hands and **sole key-holder**. It treats
  every proposed field as untrusted, re-fetches the live book, re-sizes (¼-Kelly), runs every guardrail,
  and is the only thing that signs+submits — or vetoes. *"Hermes proposes; the ERS disposes."*
- **Deterministic guardrails replace the human** (full autonomy, no confirm-loop). Telegram = notify +
  remote kill/pause only. Operator is in **Albania** (legally clear for Polymarket's offshore CLOB).
- **v1 capital: a $300 test wallet.** Honest stance: the null hypothesis is break-even-to-negative after
  fees/spread/slippage/lockup/adverse UMA resolution. Job #1 = **don't blow up** and **prove a net edge in
  shadow** before risking more. If nothing clears its bar in shadow → **do not deploy**.

## 2. Repo, environment, how to run
- **Repo:** `~/Public/WorkRepos/personal-work/polymarket-bot` (GitHub `jouleka/polymarket-bot`, private).
- **Language/layout:** Python 3.13, src-layout package at `src/polybot/`. Config in `pyproject.toml`
  (`[tool.pytest.ini_options]` sets `pythonpath = ["src"]`, so tests import `polybot` with no install).
- **Virtualenv:** `.venv/` (already created). Deps: `pytest`, `httpx`, `websockets`.
- **Run tests:** `./.venv/bin/pytest`  → currently **56 passing**.
- **Run the live read-only smoke check:** `./.venv/bin/python scripts/live_ingestion_check.py`
  (connects to the real public venue — read-only, no auth, no orders).

## 3. Tickets — YouTrack project `POL` ("polymarekt") at <https://mysigner.youtrack.cloud/projects/POL>
(Note: the project name is misspelled "polymarekt" in YouTrack but it IS the polymarket bot. A *separate*
project `IBK` = ibkr-invest is a different repo — not this one.)

| Ticket | Slice | Status |
|---|---|---|
| POL-1 | EPIC — Autonomous Polymarket + Hermes bot | umbrella |
| **POL-2** | **S0** — Phase-0 verification + finalize decisions | In Progress — decisions DONE (see §5); only the live tiny-order proof remains, which belongs to POL-4 |
| **POL-3** | **S1** — Ingestion + self-snapshotting Market-Memory DB | **In Progress — this is your work** |
| POL-4 | S2 — Signing + order-construction spike (BUILD-GATING) | Blocked on operator funding a deposit wallet; build on the official Rust `rs-clob-client-v2` |
| POL-5 | S3 — ERS skeleton + `pending_intents` + `propose_trade` | Not started (needs S2) |
| POL-6 | S4 — Safety envelope + supervisor + reconciliation + Telegram | Not started |
| POL-7…11 | S5 calibration · S6 Hermes integration · S7 smart-money/insider · S8 maker module · S9 shadow harness + ramp | Not started |

**Critical path:** `S0 → S2 → S3 → S4 → S6 → S9`, with `S1` feeding `S5`/`S7`. **No real money** until S4's
kill path is tested against a wedged process AND S9 shadow proves a calibrated, net-positive, out-of-sample edge.

## 4. Read these docs first (in the repo)
- `docs/CONTEXT.md` — onboarding; verified facts about Polymarket APIs + Hermes; landmines. **Read first.**
- `docs/specs/2026-06-24-autonomous-polymarket-bot-design.md` — full design spec.
- `docs/TICKETS.md` — S0–S9 ↔ POL-2…POL-11 mapping + per-ticket goal/build/acceptance.
- `docs/DECISIONS-S0.md` — the finalized S0 decisions + the verified risk envelope (§5 below).
- `docs/VERIFICATION-2026-06-24.md` — Phase-0 verification (Hermes provenance, signing path).

## 5. The S0 risk envelope (the numbers that replace the human — already decided & adversarially verified)
Operator anchors: **$300 bankroll · single hardened VPS (keys NEVER on the Windows/WSL box) · conservative ·
maker-first · ¼-Kelly**. Verified caps (NAV=$300): total-open-risk ≤ $60 (20% NAV); per-trade ≤ $12; ≤ 4
concurrent (≤ 3 while the co-move matrix is cold); per-event union cap ≤ $24; daily pending-worst-case halt
$24; L7 unrealized breaker freeze@$18 / FLATTEN@$30; reserve floor ≥ $240. Full table + rationale in
`docs/DECISIONS-S0.md`. Signing path decided: **official Rust `rs-clob-client-v2`** as a sidecar (Python/TS
V2 SDKs are broken for new deposit wallets; EOA sigtype-0 rejected).

## 6. What is already built in S1 (all under `src/polybot/`, all TDD'd)
The ingestion pipeline flows end-to-end and is **LIVE-VERIFIED read-only against production Polymarket**:
`WS frame → MarketSocket → MarketStream (dispatch) → per-asset LocalBook + PersistingSink → SQLite store`,
plus a REST `DataApiPoller`.

- `core/models.py` — `Outcome`, `Market`, `Envelope` (UNTRUSTED-by-default canonical record).
- `core/clock.py` — `MonotonicStamper` (strictly-increasing `observed_at`; CONTRACT: **one shared instance
  across all collectors** — the tests' `_stream()` helper and the live script already share one; a thread-lock
  is deferred to the sharding work in §9). Don't create a second stamper in new tests/code.
- `ingestion/gamma.py` — Gamma market normalizer ([0]=Yes/[1]=No, exact-string `token_id`, parses the
  JSON-encoded `clobTokenIds`/`outcomes`/`outcomePrices`, fails loud on format drift). *Clean on 20 live markets.*
- `ingestion/envelope.py` — `make_envelope` (UNTRUSTED default + stamped observed_at).
- `ingestion/sanitizer.py` — untrusted-content sanitizer (strips control/zero-width/bidi chars; fixed-point
  delimiter breakout defense). Injection defense; sits OUTSIDE the model.
- `storage/market_memory.py` — `EventStore` (SQLite WAL; persists across restart; ordered no-look-ahead
  replay via `replay_until`; idempotent dedup on `UNIQUE(source,event_id)`; context manager).
- `ingestion/orderbook.py` — `LocalBook` (rebuild from `book` snapshot + `price_change` deltas; size-0
  removes; crossed/locked book → no midpoint). **Staleness gate done:** `is_stale()`/`mark_stale()`; a book
  is stale until its first snapshot and after a disconnect, and `midpoint()` returns `None` while stale, so
  the ERS can't size off an unverified book. Remaining TODO: mid-stream hash-based sequence-gap detection.
- `ingestion/market_stream.py` — `MarketStream` dispatcher (routes to per-asset books; benign-event
  allowlist `{last_trade_price, tick_size_change}` skipped vs truly-unknown `event_type` → HALT; emits
  `Observation` to a sink).
- `ingestion/market_socket.py` — `MarketSocket` resilient async loop (subscribe-on-connect == resync;
  reconnect with exponential backoff; malformed-frame skip; array/batch frames; injected transport). Flow:
  `run` does `async for frame in transport: self._dispatch(frame)`; `_dispatch` → `stream.ingest`, and an
  unknown `event_type` raises `ValueError` there which **propagates out as a HALT** (intentional). The
  transport exposes `async send()` (the `FakeTransport` in the tests records sent messages in `.sent`).
- `ingestion/persistence.py` — `PersistingSink` (Observation → Envelope → store; every streamed frame is a
  distinct point-in-time row keyed on unique observed_at — NO content dedup, which would silently drop
  reverts/reconnects on a no-backfill store).
- `ingestion/data_api.py` — `DataApiPoller` (`poll_once` persists each item as an Envelope, idempotent on
  item id, skips id-less items, unwraps `{data:[...]}`, all market links; `run` = continuous loop with
  interval + optional rate limiter).
- `ingestion/ratelimit.py` — `RateLimiter` (clock-injected token bucket; `acquire_delay()` returns wait).
- `ingestion/transport.py` — REAL httpx Data-API fetch + CLOB-market-WS connection. **No unit tests by
  design** (mocking httpx/websockets would test the mock); verified by the live smoke check.
- `scripts/live_ingestion_check.py` — read-only end-to-end live check.

Confirmed live: WS subscribe format `{"type":"market","assets_ids":[...]}` works; the reconstructed book
midpoint matched Gamma's independent outcome price exactly; 25 real `/trades` persisted.

## 7. Working conventions (ENFORCED — the operator cares about these)
- **TDD, strictly.** Every production change is test-first: write a failing test, RUN it and watch it fail
  for the right reason, then write minimal code to pass. No production code without a failing test first.
- **Independent review pass.** After any meaningful code/scaffolding, dispatch an independent **Opus**
  reviewer agent (a code-reviewer subagent) before calling it done. Triage findings with rigor — fix the
  real ones, push back on the wrong ones with technical reasoning. This session ran 3 such cycles and they
  caught real bugs (incl. silent data-loss).
- **Commits:** branch off `main` first (name it for the slice, e.g. `pol-3-pong-responder`, matching the
  existing `pol-s0-s1-foundation` / `pol3-live-adapters` pattern); **omit the `Co-Authored-By: Claude` trailer**
  (operator preference); commit only when asked, and only after the task is done and verification passes.
  `main` is currently **11 commits ahead of `origin/main` and NOT pushed** — the operator pushes. Merges to
  `main` are `--no-ff` with the verification status noted in the message.
- **Design landmines (from CONTEXT.md):** fail loud / auto-HALT on any format or version change; treat all
  ingested content as UNTRUSTED data, never instructions; self-snapshot market data from day one (Polymarket
  history is lossy after resolution and cannot be backfilled); never let Hermes compute size or touch keys;
  always re-fetch the live book before submitting; bonding/hold-to-resolution = tail-risky; maker ≠ auto-safe.

## 8. WS keepalive — DONE ✅ (was "pong responder"; empirically it's a client PING *sender*)
**Live-verified 2026-06-25** (5 clean round-trips + a 50s endurance run): the CLOB market channel keepalive is
**CLIENT-driven**, not server-initiated. The client sends a bare `"PING"` text frame (~every 10s); the server
replies with a bare `"PONG"` text frame. The server **never** initiates pings (so there is nothing to "respond"
to — the ticket's "pong responder" framing was wrong). An idle connection survived 45s with zero pings, so the
docs' "drops after ~10s" is stale (as VERIFICATION warned) — but we still send `"PING"` for indefinite-connection
robustness. Implemented as a **per-connection `MarketSocket._keepalive` task** (`ping_interval=10.0` default).
The bare `"PONG"` reply is non-JSON, so `_dispatch`'s existing malformed-frame skip drops it — it never reaches
`stream.ingest`, so it cannot HALT (locked by `test_socket_skips_pong_keepalive_reply_without_halt`); a *JSON*
pong would still HALT, which is the desired fail-loud-on-format-change. The keepalive is best-effort (swallows
`reconnect_on` send errors so it can't mask the receive loop's disconnect handling) and validates
`ping_interval > 0`. Half-open detection (send buffers, recv stalls) is **out of scope** — that's the stale-mark
watchdog's job (DECISIONS-S0). Tests: `tests/test_market_socket.py` (10 socket tests). Independent Opus review:
3-lens panel + closing pass, verdict SHIP.

**Also fixed (a CRITICAL latent bug the review surfaced):** a real disconnect raises `websockets.ConnectionClosed`,
which is **not** an `OSError`, so the old `reconnect_on=(OSError,)` wiring would have let the *normal* disconnect
escape `run()` (no reconnect, no `mark_all_stale`). Added `transport.WS_RECONNECT_ON = (OSError, ConnectionClosed)`
(core stays transport-agnostic) and wired the live script to it (`tests/test_transport.py` guards the tuple).

## 9. Remaining POL-3 work after that (rough order)
1. ~~WS **sharding**~~ **DONE ✅** — `ingestion/sharding.py` `ShardedMarketCollector`: splits assets into
   ≤`max_assets_per_shard` chunks (default 500), one `MarketStream`+`MarketSocket` per shard, ALL sharing the
   one stamper + one sink; concurrent shard tasks under a `TaskGroup` (a HALT in any shard tears down the group);
   per-shard staleness isolation; unified `book_for`. Fail-loud on empty/duplicate asset_ids. `MarketSocket.run`
   now supports `max_connections=None` (unbounded reconnect = the 24/7 production mode). The stamper got its
   `threading.Lock` (correctness must not rest on the GIL; free-threaded 3.13t ships). Live-verified: 2 shards /
   2 concurrent connections, both books built, observed_at globally ordered+unique, bounded AND unbounded modes.
   Independent Opus review (2-lens panel + closing pass): SHIP.
   - ⚠ **C2 FOLLOW-UP before scaling shard count past 2:** `EventStore.append` does a synchronous `commit()`
     per frame ON the event loop; with many shards that stalls sibling receive loops + keepalives (idle-drop
     risk). Pre-existing (single-stream had it); ordering is still correct. Fix = batched commits / single-writer
     queue / off-loop writer. The sink-MUST-be-synchronous invariant is documented in `sharding.py`. **Do not
     raise production shard count beyond the live-verified 2 until this lands.**
2. **Mid-stream sequence-gap detection** — **DONE ✅** (branch `pol-3-orderbook-seqgap`).
   - ⚠ **CRITICAL discovery (corrects §6):** the live `price_change` format had drifted from what the code
     assumed. It is NOT `{asset_id, changes:[…]}`; it is `{market, timestamp, price_changes:[{asset_id,
     price, size, side, hash, best_bid, best_ask}, …]}` — **one frame fans out across a market's legs**, with
     `asset_id` per-entry. The old `ingest` did `message["asset_id"]` → `KeyError` → `_dispatch` swallowed it,
     so **every live delta was silently dropped (the book was snapshot-only)**. Fixed: `MarketStream.ingest`
     fans a frame out to its tracked books, ignores untracked sibling legs (no phantom books), and HALTs on a
     missing `price_changes` list / malformed or non-string entry (fail-loud format change).
   - **Gap detector:** the venue book `hash` is NOT recomputable from the public stream (288 SHA-1
     serializations vs a real `book` frame → 0 matches), so detection uses the per-entry **`best_bid`/
     `best_ask`** (the venue's authoritative resulting top-of-book): after applying the deltas, a reconstructed
     top that disagrees ⇒ dropped/misapplied delta ⇒ `LocalBook.verify_top_of_book` marks the book stale
     (`midpoint()` → None). **Recovery = force a reconnect** (subscribe-on-connect == fresh snapshot == the
     proven resync); re-subscribing on the SAME live socket did NOT resnapshot live, so reconnect is used. A
     persistent re-divergence backs off and HALTs after `max_resyncs` (no zero-delay reconnect storm).
   - **Verified:** `./.venv/bin/pytest` = **111 passing**; live read-only — WS-reconstructed book matched the
     independent REST `/book` oracle 8/8 assets, 0 disagreements over ~600–800 applied deltas; live smoke check
     persists clob-ws price_change rows. Two independent Opus reviews (4-lens panel + a focused re-review) →
     SHIP; their findings (fail-loud detector fields, numeric-coercion symmetry, resync-storm backoff/HALT,
     pre-snapshot persistence) were all fixed. Subscribed-but-unsnapshotted deltas are now archived (the store
     cannot be backfilled). Open follow-ups (non-blocking, both fail-loud-direction): confirm a real
     multi-entry-same-asset frame carries `best_bid/best_ask` on intermediate rows; an optional time-windowed
     reconnect ceiling for a reconcile-one-then-rediverge flap.
3. **Synthetic events** (liquidity-evaporation / large-print) emitted from book deltas.
4. **Polygon on-chain log watcher** (V2 exchange + ConditionalTokens ERC-1155) as tamper-proof ground truth.
5. **News fast-path** (curated primary-source allowlist + calendar pre-stager) + slow-path (one aggregator +
   GDELT for discovery/backtest only, never a trade trigger). Use the sanitizer + UNTRUSTED envelope.
6. **Replay-fidelity / no-look-ahead integration harness** (the S1 acceptance gate): record live frames, then
   replay through the pipeline and assert identical book state with no look-ahead.

## 10. Hard constraints
- **POL-4 (signing) is blocked** on the operator funding a Polymarket deposit wallet — don't attempt order
  signing/placing until then. All current work is read-only ingestion.
- **No real money** until S4 kill-path tested + S9 shadow proves edge. You are building the safe substrate.
- Keep `main` green (`./.venv/bin/pytest` = 56+ passing) and the working tree clean before each commit.
