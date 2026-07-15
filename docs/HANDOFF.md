# HANDOFF — autonomous Polymarket bot (state as of 2026-07-15)

You are taking over an in-progress build. Read this top to bottom, then read the linked docs + the
YouTrack comments, then start at **"Your task"**. The conventions are ENFORCED — do not skip them.

> The current state is summarized in §7. Older per-slice detail below is retained as historical evidence;
> when it conflicts with the dated 2026-07-10 update, the newer update and `AGENTS.md` win.

---

## 1. What this is
A fully-autonomous, 24/7 **Polymarket** prediction-market trading bot. Brain/hands split:
- **Hermes** (NousResearch open-source *agent harness*, NOT an LLM model) = reasoning brain. Gets **only
  read tools + ONE write tool `propose_trade(...)`** that does nothing but INSERT a PENDING row. Never holds
  a key; cannot sign/submit/cancel/move funds.
- **ERS** (Execution & Risk Service, deterministic Python) = the hands + **sole key-holder**. Treats every
  proposed field as untrusted, re-fetches the live book, re-sizes (¼-Kelly), runs every guardrail, and is the
  only thing that signs+submits — or vetoes. *"Hermes proposes; the ERS disposes."*
- **Deterministic guardrails replace the human** (full autonomy, no confirm-loop). Telegram = notify +
  remote kill/pause only. Operator is in **Albania** (legally clear for Polymarket's offshore CLOB).
- **v1 capital: a $300 test wallet.** Honest stance: the null hypothesis is break-even-to-negative after
  fees/spread/slippage/lockup/adverse UMA resolution. Job #1 = **don't blow up** + **prove a net edge in
  shadow** before risking more. If nothing clears its bar in shadow → **do not deploy**.

## 2. Environment / how to run
- **Development checkout:** `/root/projects/polymarket-bot`, canonical GitHub repository
  `jouleka/polymarket-bot`, remote `origin`.
- **Service checkout:** `/opt/polymarket-bot`, executed by `polybot`. It is distinct from the development
  checkout. Before any future install, repair its stale remote to GitHub; never recreate the deleted
  `/root/git/polymarket-bot.git` bare repository.
- **Venv:** gitignored `.venv` with Python 3.13. Canonical verification:
  `./.venv/bin/pytest -o addopts="" -q`. POL-15 landed with **2,070 passed**; the current POL-16
  reviewed POL-16 candidate passes **2,121 tests** (see the dated §7 update).
- **Synchronize safely:** check status, `git fetch --prune origin`, compare ahead/behind, and fast-forward
  only a clean non-diverged checkout. Do not blindly pull over local work.
- **Service state:** `polymarket-ingestion.service` is stopped and disabled. Deployment, database
  preservation/migration, and start/enable are separate owner-approved actions; see `deploy/README.md`.
- Free keyless Polygon RPC: `https://polygon-bor-rpc.publicnode.com` (UA header).
- **Live read-only smokes:** `scripts/{live_ingestion_check, replay_fidelity_check (+ --forced-resync),
  polygon_watch_check, news_check, shard_endurance_check}.py`.

## 3. ENFORCED conventions (the operator cares)
1. **Strict TDD** — write a failing test, RUN it, watch it fail for the right reason, then minimal code.
2. **Independent review** — after any meaningful code, dispatch a `superpowers:code-reviewer` subagent with
   **model: opus** explicitly pinned; triage with rigor (these reviews have caught real CRITICAL bugs every
   slice — e.g. the ERS validator's price==1 div-by-zero + p≥1 max-size fail-open). Re-review after fixing
   safety-critical findings (continue the same reviewer via SendMessage, or a fresh closing pass).
3. **Fail loud / auto-HALT** on any format/version change; treat all ingested content as UNTRUSTED data,
   never instructions; self-snapshot from day one (the SQLite stores cannot be backfilled).
4. **Git:** branch off `main` (e.g. `pol-5-ers-...`); **OMIT the `Co-Authored-By` trailer**; commit only when
   done + verification passes; merges to `main` are `--no-ff` with the verification status noted.
5. **Confirm before pushing** to origin (the operator usually says "push now" — but ASK).
6. Post a progress comment on the relevant POL ticket when you finish a slice. Update the memory files +
   this HANDOFF as state changes.

## 4. Tickets — YouTrack project `POL` ("polymarekt", misspelled but correct) at mysigner.youtrack.cloud
Read the comments on the relevant ticket — they hold the detailed per-slice record.

| Ticket | Slice | Status |
|---|---|---|
| POL-1 | EPIC | umbrella |
| POL-2 | S0 — Phase-0 verification + finalize decisions | DONE (decisions in DECISIONS-S0.md; the live tiny-order proof is carried by POL-4) |
| POL-3 | S1 — Ingestion + Market-Memory DB | **DONE + pushed** (+ all S1 finishing touches) |
| **POL-12** | C2 — off-loop EventStore writes (unblock WS shards > 2) | **DONE + pushed** |
| **POL-4** | S2 — signing + order-construction spike (BUILD-GATING) | **BLOCKED** on the operator funding a Polymarket deposit wallet on a CLEAN non-Windows box |
| **POL-5** | S3 — ERS skeleton + pending_intents + propose_trade | **slices 1+2+3 DONE + pushed** (slice 3 = co-move matrix + per-cluster cap + L7 breaker, `origin/main` @ `d17224e`) |
| **POL-6** | S4 — Safety envelope + supervisor + reconciliation + Telegram | **KILL PATH (S4.1–S4.3) + S4.5 3-way reconcile + S4.4 L5 AnomalyMonitor ALL DONE + pushed**. Kill path: SafetyController op-state gate + signer de-risk/GTD/startup-self-test + out-of-band supervisor & the WEDGED-PROCESS acceptance gate. S4.5: durable `fills` ledger + pure `ThreeWayReconciler` + `RestartReconciler` (crash=HOLD). S4.4: the running-cadence anomaly kill-switch — `ers/anomaly.py` AnomalyMonitor (6 sentinel seams, severity order skew→recon→canary→book→api→ws) + `ERSController(anomaly=)` edge-triggered halt-first one-shot cancel_all + the per-cycle reconcile cadence (`make_recon_provider`, DIVERGED→`l5_recon_mismatch`) + canary scheduler (never-blind-retry) + 7 tighten-only hashed caps + `last_frame_at` WS-health accessors; STICKY halts (operator fork: only the clean boot-reconcile ever auto-resumes); shadow-only on PaperSigner; **660 tests**; 5 sub-slices each spec+pinned-opus reviewed with FULL mutation batteries + a final whole-slice review (APPROVED FOR MERGE — its own cross-cutting mutation surfaced + closed a co-fire coverage edge). **S4.7 realized-loss breakers + flow gate + tighten-only ramp ratchet ALSO DONE** — durable dual-stamped `flow_journal` (monotonic `at` + wall-clock `wall_at`; windows are wall-clock rolling 1h/24h/7d, restart-surviving) + the per-cycle flow gate in `verdict`'s RUNNING branch (`wire_flow_gate` one-shot; rate caps 2/hr 6/day + the $24 daily ceiling via the dormant predicate w/ `new_worst_case=per_trade` — blocks WITHOUT touching op-state, auto-slides) + `ers/lossbreaker.py` (weekly >$36 frozen-excluded → sticky HALTED + one best-effort cancel_all + ramp step; trailing streak ≥3 → sticky PAUSED; pending >$24 → sticky PAUSED + ramp step; fail-closed HALT(flow_data_error) on journal corruption; NONE on the empty shadow journal) + `ers/ramp.py` (TIGHTEN_DIRECTION over ALL 38 fields structurally pinned; `assert_tighten_only` fail-loud; `step_daily` 9/45/255/45, `step_weekly` 6/30/270/30, min()-composed idempotent) + `SafetyController.swap_caps` (guard→hash→no-op→audit(kind=caps_swap)→mutate) + **the `active_caps()` re-plumb** (run_cycle now sizes off the controller's swappable reference — swaps provably bite next cycle). **763 tests**; 4 sub-slices × (spec review + pinned-opus mutation battery, ~40 mutations, every survivor closed with a mutation-verified pin) + final whole-slice review APPROVED (two cross-cutting mutations caught). **S4.6 L8 TelegramController ALSO DONE — the LAST S4 sub-slice** — the remote authenticated safety-control channel: `ers/telegram_auth.py` `CommandAuth` (five fail-closed gates IN ORDER — neutralize/structure → allowlisted chat-id → six-verb command-set → constant-time HMAC-SHA256 under the rotating secret → monotonic per-chat-id nonce; hardened by review: isascii-nonce guard, reject-pipe-delimiter, fail-closed-on-internal-exception) + `ers/telegram.py` `TelegramController` (structurally-bounded like `ProposeOnlyFacade` — name-mangled `SafetyController`, public surface EXACTLY `{drain, notify}`, structurally NO open-trade verb; six-verb `__apply` map: KILL/PAUSE→existing, RESUME→`set_state(RUNNING)` from PAUSED or HALTED [operator-trusted, the ONLY operator HALTED→RUNNING], FLATTEN→existing FLATTENING, LOWER_CAPS→`step_weekly` via tighten-only `swap_caps`, BLACKLIST→a durable `(kind,value)` set [enforcement Fork-2 hard-off]; per-message isolation) + `notify()` best-effort fire-and-forget with the alerts-down→sticky-HALT fail-safe + the `blacklist` durable table + the `ERSController(telegram=)` seam **draining at the TOP of `run_cycle`** (a KILL dominates the cycle) on the serial runloop. **853 tests**; 4 sub-slices × (spec + pinned-opus security/mutation battery; genuine catches: the isdigit-vs-int nonce crash, the fail-open coverage gap, the pipe-delimiter collision, the except-Exception breadth, the empty-value asymmetry) + final whole-slice review APPROVED FOR MERGE (the two authority paths proven DISJOINT: `ProposeOnlyFacade ∩ TelegramController` public == ∅; the no-trade-verb guarantee locked by 3 independent pins). Shadow-only over a fake transport; nothing signs. **THIS CLOSES THE S4 SAFETY ENVELOPE.** |
| POL-7 | S5 — Calibration + base-rate prior + Anchor Gate | **DONE + pushed** (`origin/main` @ `1ad52f5`; calibration tracker + prior + Anchor Gate; deep ERS wiring deferred to S6) |
| **POL-8** | S6 — Hermes integration + signal fusion + truth-gate | **DONE + pushed** (`pol-8-hermes-s6` → main; 448 tests; §4.1 fusion + ERS-side citation truth-gate + propose-only facade + `process_pending` wiring; built as pure units, runs end-to-end on PaperSigner; 3 Opus deep-dives — caught + fixed a CRITICAL corroboration bypass (C1) and an orphan-forecast edge; live-Hermes MCP transport + adaptive fusion + MarketRegistry + resolution-feedback DEFERRED) |
| POL-9 | S7 — Smart-money / insider detectors (defensive) | **DONE + pushed** (`origin/main` @ `a6d91dc`; PnL + luck filter + D1–D6 + composite + policy; FOLLOW hard-off; live wiring deferred) |
| **POL-10** | S8 — Maker-rewards module | **DONE + pushed** (`origin/main` @ `17e0901`; branch `pol-10-s8-maker`; 853→1006 tests; honest net-of-adverse-selection ledger→calculators→binary GO/NO-GO gate→facade + quote-policy; shadow-only, data-gated dormant, purely additive) |
| **POL-11** | S9 — Shadow harness + ramp controller | **DONE + pushed** (`origin/main` @ `826e210`; branch `pol-11-s9-harness`; 1006→1113 tests; the earn-autonomy capstone — maker-fill simulator + net-of-everything shadow ledger + walk-forward/MC evidence + dispute-freeze stress + binary SHADOW/TINY_LIVE/RAMP controller + the RestartReconciler boot seam; shadow-only, data-gated dormant, additive but for one inert `ERSController(reconciler=None)` seam) |

**Critical path:** `S0 → S2 → S3 → S4 → S6 → S9`, with `S1` feeding `S5`/`S7`. **No real money** until S4's
kill path is tested against a wedged process AND S9 shadow proves a calibrated, net-positive, out-of-sample
edge.

## 5. What is already built (historical per-slice detail; current summary and test count are in §7)
- **S1 ingestion (`src/polybot/ingestion/` + `core/` + `storage/`):** Gamma normalizer · CLOB market-WS
  collector (sharding + client keepalive + mid-stream sequence-gap detection & resync) · LocalBook
  (staleness-gated) · Data API poller · Polygon on-chain log watcher (CTF ERC-1155 + Exchange,
  empirically-discovered topics) + bounded selective RPC retry (`ingestion/retry.py`) · news fast-path
  (allowlist-gated, sanitized UNTRUSTED, XXE-guarded) + curated `ingestion/allowlist.py` (live-validated;
  **operator must review PRIMARY before it informs trades**) + `news.CalendarScheduler` · synthetic events ·
  the **replay-fidelity / no-look-ahead acceptance gate** (`scripts/replay_fidelity_check.py`,
  staleness-aware + `--forced-resync`) · Market-Memory `EventStore` (append-only) +
  `storage/event_writer.QueuedEventWriter` (off-loop single-writer; historical shard endurance proved the in-memory
  WS path can exceed 2 shards). **Current D4a production wiring deliberately does not attach the raw-frame
  `PersistingSink`: WS books stay in memory, one versioned midpoint batch is written every 60 seconds, and the Data
  API trade tape is retained. The old raw collectors/replay path remain available for tests and explicit legacy
  evidence replay, not new production persistence.**
- **S3 ERS (`src/polybot/ers/`):**
  - `caps.py RiskCaps` — the signed DECISIONS-S0 §4 envelope (NAV $300 · at-risk ≤$60 · per-trade ≤$12 ·
    ¼-Kelly · per-event-union ≤$24 · per-market ≤$18 · per-source ≤$30 · reserve ≥$240 · max-concurrent ≤4 ·
    matrix-cold ≤3 · $5 floor), construction-time consistency-verified, content-hashed.
  - `validator.py evaluate_intent(intent, book, portfolio, caps) → Decision` — PURE: re-price (touch,
    staleness-gated) → fail-closed input guards (degenerate price, impossible p, bad calib) → ¼-Kelly →
    clamp by every cap (smallest headroom wins) → concurrency + fail-closed matrix-cold sub-cap → Kelly-vs-
    floor SKIP. **Provably bounded by the envelope, fails closed** (Opus-verified after fixing 2 CRITICALs).
  - `intent_store.py IntentStore` — the chokepoint. `propose_trade` = Hermes's ONLY write: INSERT-only (no
    status param → **chokepoint by construction**), idempotent, parameterized; ERS-only `record_decision`
    transitions status + appends an immutable atomic audit row. Mutable `pending_intents` (separate from the
    append-only EventStore).
  - `service.py process_pending` — the ERS loop: poll PROPOSED → re-fetch live book per intent → validate →
    record + audit → fold each ACCEPT into the portfolio (cross-intent caps hold) → call the signer SEAM on
    ACCEPT. Per-intent isolation (a raising intent → REJECT internal_error, batch continues). `PaperSigner`
    = shadow stub; the real signer needs S2/POL-4.
  - **slice 3 (pushed, `origin/main` @ `b1ec7eb`):** `comove.py` — Pearson co-move estimator (fail-closed
    ρ=1 on degenerate input) + `ClusterModel` warm/cold gate (any unknown pair → cold) + `build_bar_series`
    EventStore→midpoint-bar adapter (point-in-time, no look-ahead). `caps.py RiskCaps.cluster_cap(ρ)` =
    `per_trade + (1−ρ)·(total_open−per_trade)` clamped + 4 L7 fields. `validator.py` — `ClusterView` +
    `Portfolio.cluster_risk` + the `per_cluster_cap` min() term (warm only TIGHTENS / relaxes the cold ≤3
    count gate) + `OpenPosition` mark fields. `breaker.py DrawdownBreaker` (L7) — mark-to-mid NET drawdown,
    triggers freeze>$18 / flatten>$30 / velocity / stale-mark / **per-position-loss**, frozen excluded,
    never FLATTEN blind. `service.py` runs the breaker FIRST + wires the per-intent `ClusterView`
    (matrix_cold = not warm) + folds marks + `PaperSigner.flatten`. Two Opus reviews → PROVABLY BOUNDED +
    FAILS CLOSED. **Data-gated:** the matrix stays cold in prod (≡ slice-1) until bars accrue; `cluster_id`
    is still the `event_id` placeholder (per-cluster aliases per-event, fails safe) → real latent-cluster
    assignment is deferred.
- **S5 calibration (`src/polybot/calibration/`, POL-7):** the L3 GO/NO-GO sizing gate + the
  anti-overconfidence Anchor Gate. `ledger.py` append-only forecast→outcome store (point-in-time, records
  the market-mid baseline; rejects non-finite). `scoring.py` Brier + Murphy decomposition + Brier-skill
  (pure Decimal). `tracker.py` the **binary {0,1} k multiplier** → the validator's `calib_score`: GO iff
  ≥`min_n` honest resolutions AND beats the market-mid baseline AND reliability≈0 AND resolution>reliability;
  DISPUTED_LOST/VOID excluded (whale-flip immunity). `prior.py` curated reference-class priors + longshot
  shrink (operator review required). `anchor.py` clamp p to the intersection of prior+market log-odds bands
  (corroboration widens, still bounded; fail-loud on non-finite). `config.py` consistency-checked knobs.
  `gate.py` the `CalibrationGate` facade. 2 Opus reviews → no CRITICAL, k-gate fail-safe-toward-paper,
  Anchor Gate bounded. **DATA-GATED:** dormant until S6 feeds forecasts + markets resolve (every category
  cold/k=0 until ≥150 honest resolutions). **Deep ERS wiring deferred to S6** (delivered package + facade);
  **S6 obligation:** wrap `clamp_p` in the per-intent try/except so a fail-loud raise rejects one intent.
- **S7 detectors (`src/polybot/detectors/`, POL-9):** the DEFENSIVE smart-money + insider analytics. `pnl.py`
  realized PnL from the cash-flow ledger (per-condition, exact Decimal, NEVER /leaderboard). `luck.py` the
  skill gate — binomial-z (wins beat the price-implied baseline, p<0.001) + deterministic normal-CI + single-
  event-dominance (crash-free + fails-closed). `classify.py` {SHARP,LUCKY,MARKET_MAKER,INSIDER_LIKE,NOISE}
  (MM excluded first). `sybil.py` union-find funder clustering. `toxicity.py` D1 order-flow toxicity (ratio≥0.75
  AND z≥2) → the `pull_quotes` maker seam (rejects negative sizes). `signals.py` D2–D6 (NaN-safe `clamp01`).
  `composite.py` 0–10 + bands + single-Critical override (clamps in/out). `policy.py` **`FOLLOW_ENABLED=False`**
  (the only FOLLOW branch is dead code) → default AVOID/FLAG. 2 Opus reviews → no CRITICAL; FOLLOW structurally
  off (grep + 40-combo sweep), luck filter crash-free/fails-closed; HIGH input-validation gaps fixed.
  **Deferred:** live `/activity` + on-chain wiring · the real S8 maker module (D1 = a seam) · Hermes's D3
  catalyst timeline · FOLLOW (hard-off until precision proven + legal/ToS review).
- **S6 Hermes integration (POL-8) — the chokepoint wired end-to-end (shadow/PaperSigner):** built as isolated
  pure units + the `process_pending` wiring (see `DESIGN-S6-HERMES.md` / `PLAN-S6-HERMES.md`).
  `fusion/engine.py fuse()` — the §4.1 weighted-log-odds fold, market-mid prior, `w_news≤0.25` hard cap,
  **corroboration-gated** `w_news` (0→0.20), per-signal clip, identity `recalibrate` stub (adaptive layer
  deferred). `fusion/component_log.py` — append-only per-signal sidecar (preserves the un-backfillable
  substrate; does NOT touch `ForecastLedger`). `truthgate/gate.py verify()` — ERS-side citation truth-gate:
  allowlist + **≥2 independent `publisher_group` corroboration** (added `Source.publisher_group`, default =
  registrable domain; fed-press/fed-monetary collapse to one group) + same-source/thin-book injection refusal;
  **C1 fix** = per-citation ambiguity exclusion (a single feed can't forge two groups via event_id/entities
  collision). `ers/facade.py ProposeOnlyFacade` — composes IntentStore; exposes ONLY `{propose_trade, get,
  audit_log}` + 4 read tools; structural sweep proves no `place/flatten/record_decision/pending/__call__`
  path (the "Hermes can at worst enqueue" guarantee, load-bearing in code). `detectors/orchestrator.py` —
  composes toxicity→d2..d6→composite→policy into a defensive AVOID/FLAG verdict (FOLLOW stays off;
  `catalyst_present` a documented reserved POL-9 seam). `ers/market_meta.py StubMarketMeta` — MVP stub
  (`category="unknown"`→k=0 paper-only; `seconds_to_resolution` sentinel; real MarketRegistry deferred).
  `ers/service.py` — `HermesPipeline` + `process_pending(pipeline=…)`: per-intent **breaker→detector→
  truth-gate→fuse→clamp_p (try/except→distinct `anchor_error`)→record forecast+components→k_for→
  evaluate_intent(calib_score=k)→ACCEPT place+fold**; `pipeline=None` == verbatim slice-3 (back-compat);
  **`evaluate_intent`/validator/intent_store/caps UNCHANGED** (wires AROUND the pure validator). End-to-end
  `tests/test_ers_hermes_e2e.py` proves the 4 §9 scenarios incl. an injection probe REJECTed
  `same_source_collusion` that never reaches the signer (mutation-verified genuine). `deploy/hermes/config.yaml`
  — reviewed tool-grant artifact (exactly the 5 tools; inert in S6). 3 Opus deep-dives → APPROVED FOR MERGE.
  **Data-gated/paper-only:** k=0 until ≥150 honest resolutions accrue → everything SKIPs below floor in prod;
  nothing can sign (PaperSigner only). **Deferred to later slices:** live-Hermes MCP transport + injection
  probe vs a real Hermes · adaptive fusion (EMA weights + isotonic recal) · MarketRegistry (Gamma metadata →
  category/question/seconds) · resolution-feedback wiring (warms k) · Hermes catalyst→d3/d5 · real
  cross-event latent clusters · §4.2 edge-hurdle H · a true before/after mid-diff for the same-source gate
  (DESIGN §10; the current thin+wide-book proxy is safe because uncorroborated ⇒ w_news=0 + tight anchor).
- **S4 kill path (POL-6, S4.1–S4.3) — the safety envelope's go-live gate (shadow/PaperSigner):** `ers/safety.py
  SafetyController` = the op-state machine (RUNNING/PAUSED/HALTED/FLATTENING) consulted at the TOP of
  `process_pending` via `controller=` (precedence KILL > op-FLATTENING > L7-FLATTEN > FREEZE > NONE;
  `controller=None` == pre-S4; verdict returns the SPECIFIC reason l8_kill/l8_paused/unclean_restart/op_flatten;
  FLATTENING de-risks ONCE then settles to HALTED). `ers/controller.py ERSController` = the runloop (starts HALTED;
  beats the heartbeat then process_pending; wires `gtd_for`). `ers/signer.py Signer` Protocol + `PaperSigner.cancel_all`
  (KEEPS the GTD exits — cancels working entries only) / `place_gtd_bracket` / `run_canary`; `ers/gtd.py derive_bracket`
  (aggregate standing-exit ≤ total_open). New frozen `RiskCaps` fields (weekly/consecutive/rate/GTD/skew/canary/
  deadman/recon) — tighten-only, content-hashed; `ers/startup_selftest.py verify_or_refuse` (refuse-to-start on
  caps-hash/pUSD-addr mismatch). `ers/heartbeat.py` = fate-isolated FILE heartbeat (atomic os.replace; +inf
  fail-closed on missing/non-finite). `ers/supervisor.py OutOfBandSupervisor` = the separate-PROCESS watchdog:
  `decide` (dead-man timer) + `on_wedge` (SIGKILL the ERS, then best-effort-ALL cancel_all+flatten on its OWN
  distinct signer_B; a failing cancel can't skip flatten). **THE ACCEPTANCE GATE** (`tests/test_ers_supervisor_kill.py`,
  subprocess-backed): a real `multiprocessing` child ACCEPTs+stages a GTD bracket then wedges → the parent supervisor
  SIGKILLs it (child IGNORES SIGTERM/SIGINT so a `-SIGKILL` exitcode PROVES hard-kill necessity; bounded exitcode poll
  = no flake), de-risks on signer_B, GTD exits survive. `evaluate_intent`/validator/`propose_trade` chokepoint
  UNCHANGED; nothing signs. 2 Opus deep-dives + a final whole-slice review (APPROVED, shadow-only). **Deferred
  (contract-level in `DESIGN-S4-SAFETY.md`):** S4.4 L5 AnomalyMonitor (clock-skew/abnormal-book/WS/API-storm/canary;
  UMA stub) · S4.6 Telegram (auth/nonce/safety-increasing-only) · S4.7 realized-loss breakers + ramp-DOWN (consumes
  the dormant `would_cross_daily_pending_ceiling` predicate) · the live-POL-4 primitives (live cancelAll/credential-
  separation/real canary) + box hardening (systemd/users/egress).
- **S4 3-way reconcile (POL-6, S4.5) — shadow/PaperSigner, branch `pol-6-s4.5-reconcile` → local `main` `--no-ff`
  (pending push); see `docs/DESIGN-S4.5-RECONCILE.md` + `docs/PLAN-S4.5-RECONCILE.md`:** the durable append-only
  `fills` ledger (`IntentStore.record_fill`/`fills_log` + `accepted()`; wired via the additive `fill_sink=None` seam
  + `make_fill_sink`, `fill_sink=None` == pre-S4.5 byte-for-byte) feeds the pure leg-parsers (`ers/reconcile.py`
  `internal_balances`/`clob_balances`/`onchain_balances` — fail-closed skips, keyed on `token_id`) →
  `ThreeWayReconciler.reconcile`: price-free divergence = `|internal−onchain shares| × $1` vs `reconcile_tolerance`
  ($0.50); **settle-window keyed on the IN-SESSION monotonic fill stamp** (`reconcile_settle_window_seconds`=90, a
  hashed tighten-only cap; replayed rows get `latest_fill_at=None` ⇒ no grace); on-chain AUTHORITATIVE, CLOB advisory;
  **DORMANT only when `wallet=None`/no chain leg** (the data-gated shadow-clean state — proven unable to mask a live
  divergence) → `ers/restart.py RestartReconciler` (crash=HOLD: boot replays the durable stores, reconciles, flips
  `HALTED→RUNNING` ONLY on OK/DORMANT else stays `HALTED(unclean_restart)`; rebuilds the Portfolio from `accepted()`).
  **The injected-divergence acceptance test passes** (boot DIVERGED ⇒ stays HALTED, never auto-resumes). 556 tests;
  each of the 4 sub-slices spec+pinned-opus reviewed, full mutation set on the safety paths, + a final whole-slice
  review (APPROVED). `evaluate_intent`/validator/`propose_trade` chokepoint/`process_pending` decision-flow UNCHANGED;
  nothing signs. **Deferred:** the per-cycle running-cadence reconcile → S4.4 (consumes `ReconResult`); wiring
  `RestartReconciler` into `ERSController` boot + live wallet-scoped CLOB/on-chain feeds + the on-chain∩ACCEPTED
  rebuild + 6-decimal share-unit empirical verification → POL-4 / the S9 harness assembly.
- **S4.4 L5 AnomalyMonitor (POL-6) — the running-cadence anomaly kill-switch (shadow/PaperSigner); see
  `docs/DESIGN-S4.4-ANOMALY.md` + `docs/PLAN-S4.4-ANOMALY.md`:** NEW `ers/anomaly.py` — `AnomalyState` (frozen;
  HALT ⟹ non-empty triggers unrepresentable) + `AnomalyMonitor(caps, *, clock, …6 None-default seams)` whose
  `evaluate(positions, book_for)` consults in the pinned **severity order skew → recon → canary → book → api → ws**,
  collects ALL firing triggers (co-fire pinned), every consult **fail-closed wrapped** (a raising seam/book fires its
  own trigger — it can never void collected triggers or crash the loop). Triggers: `ClockSkewSentinel` (injected
  wall/ntp refs, >2s) · the **per-cycle reconcile cadence** `make_recon_provider` in `ers/reconcile.py`
  (wallet=None short-circuits to DORMANT WITHOUT scanning the event store — proven; DIVERGED or unknown-status →
  `l5_recon_mismatch`) · the **signing-canary scheduler** (`signing_canary_interval_seconds`=300; stamp-BEFORE-call ⇒
  a falsy OR raising canary is NEVER blind-retried — both paths mutation-pinned) · abnormal book on held tokens
  (non-stale crossed/locked; ≥80% top-depth collapse over a 1000-share floor; ≥$0.15 mid jump; per-token prev-state
  poison-proof across stale AND crossed interludes) · API storm (≥5 5xx or ≥2 auth/60s, injected sentinel) ·
  WS staleness (NEW non-consuming `MarketStream.last_frame_at()` + min-across-shards `ShardedMarketCollector`
  accessor — replay determinism proven byte-identical; >30s or wired-but-silent fires). `ERSController(anomaly=)`
  additive seam: **edge-triggered from RUNNING/PAUSED only** (never re-fires on HALTED, never preempts FLATTENING),
  **halt-FIRST** (`set_state(HALTED, reason=triggers[0])` before the one-shot BEST-EFFORT `cancel_all` — a raising
  signer is audited `FAILED:` and never unwinds the halt; GTD exits survive; `kind="cancel_all"` op-audit row).
  **ALL L5 HALTS ARE STICKY** (operator fork 2026-07-02): nothing in S4.4 ever sets RUNNING — the only automatic
  HALTED→RUNNING remains the clean boot-reconcile (structurally pinned). 7 new tighten-only content-hashed `RiskCaps`
  fields + 5 new `REASON_L5_*` codes. UMA dispute watch = the inert `dispute_flagger` seam (stored, never consulted;
  no dispute-ingestion source exists). **660 tests** (was 556); 5 sub-slices, each strict-TDD + spec-compliance +
  pinned-opus review with FULL mutation batteries (~35 mutations, every one killed by a named test after review
  fixes), + a final whole-slice opus review → **APPROVED FOR MERGE**. `evaluate_intent`/validator/`propose_trade`
  chokepoint/`process_pending` signature+flow byte-for-byte UNCHANGED (anomaly=None == pre-S4.4); nothing signs.
  **Deferred (documented seams):** real NTP/chrony ref · live API/WS health feeds + the live recorder · the UMA
  dispute flagger itself · a real canary (POL-4 Rust signer) · per-market cancel scoping · event-scan narrowing +
  the `clock_ns` (monotonic-ns!) assembly binding of `make_recon_provider` → POL-4/S9 · per-token prev-map eviction
  (S9 housekeeping) · S4.6 RESUME is the operator path that clears a sticky L5 halt.
- **S4.6 L8 TelegramController (POL-6) — the remote authenticated safety-control channel; the LAST S4 sub-slice;
  see `docs/DESIGN-S4.6-TELEGRAM.md` + `docs/PLAN-S4.6-TELEGRAM.md`:** `ers/telegram_auth.py` `CommandAuth` = the
  five fail-closed gates IN ORDER (neutralize/structure → allowlisted chat-id → six-verb command-set →
  constant-time HMAC-SHA256 under the rotating `SecretHolder` → monotonic per-chat-id nonce; mirrors the news
  allowlist-first gate; the message is UNTRUSTED DATA). `ers/telegram.py` `TelegramController` = the
  structurally-bounded L8 surface (name-mangled `SafetyController`, public surface EXACTLY `{drain, notify}`,
  structurally NO open-trade verb — mirrors `ProposeOnlyFacade`; the two authority paths are proven DISJOINT).
  The six-verb `__apply` map + `drain()` (poll→authenticate→apply→audit, per-message isolation) + `notify()`
  best-effort with the alerts-down→sticky-HALT fail-safe + the `blacklist` durable table + the
  `ERSController(telegram=)` seam that drains at the TOP of `run_cycle` (KILL dominates the cycle) on the serial
  runloop. **853 tests**; shadow-only over a fake transport. **RESOLVED FORKS (operator 2026-07-03):** RESUME =
  operator-trusted `set_state(RUNNING)` from PAUSED or HALTED (the only operator HALTED→RUNNING); BLACKLIST =
  durable `(kind,value)` set with enforcement deferred (Fork 2, hard-off — the docs' only definition is
  wallets); nonce = in-session monotonic (rotation covers cross-restart). Sacred surfaces byte-clean
  (safety.py +4 REASON_L8_* only; intent_store additive). **Deferred (documented seams):** the real Telegram
  send/recv transport (fake for tests) · the off-repo secret VALUE + its rotation cadence (deploy-config) ·
  reconcile-gated RESUME (S9 — the reconciler isn't in the runloop, DORMANT in shadow) · BLACKLIST enforcement
  in the sizing path (Fork 2 consumer hard-off) · a durable nonce table · the heartbeat-stop dead-man tie-in ·
  a `max(1, alerts_down_threshold)` clamp when a deploy passes the kwarg (alerting knob, not a signed cap) · an
  optional RESUME+anomaly same-cycle composition test. **THIS CLOSES THE S4 SAFETY ENVELOPE** — S4.1–S4.7 all done.
- **S8 maker-rewards module (POL-10) — DONE + pushed (`origin/main` @ `17e0901`; branch `pol-10-s8-maker`;
  pytest 853→1006; docs/DESIGN-S8-MAKER.md + docs/PLAN-S8-MAKER.md):** the honest net-of-adverse-selection
  maker economics, as a NEW self-contained shadow-analytics package `src/polybot/maker/` mirroring
  `calibration/`'s shape (append-only ledger → pure exact-Decimal calculators → binary GO/NO-GO tracker → thin
  facade + a quote-policy). **Purely ADDITIVE — imports NOTHING from `ers/`/`detectors/`/`calibration/`/`ingestion/`
  at module load; nothing consumes it yet (S9 is the first consumer), so the pre-S8 suite stayed green trivially.**
  The load-bearing identity, honest by construction: `net = reward + rebate + spread_capture − adverse_selection
  − fees − lockup_cost − dispute_haircut`; **the gate reads `MakerNetPnL.net` ONLY, never a gross leg.** Modules:
  `config.py` (`MakerConfig`/`FeeCategory`/`DEFAULT_FEE_SCHEDULE`, self-verifying, `is_finite()` before every range
  compare — Infinity/NaN fail LOUD as named ValueError; Fork 3 parameterized dossier-corrected fee schedule =
  sports active 0.03/exp-1, other cats planned-inactive→0, geopolitics free) · `fees.py` (`taker_fee` per-category
  `C·feeRate·p·(1−p)^exp` with the exponent-0 flat-fee path, `rebate`) · `inventory.py` (`MakerFill`, `net_inventory`
  BUY/SELL folding, `adverse_selection` = SIGNED `Σ sgn·shares·(price_exec − mark)`, sgn(BUY)=+1/sgn(SELL)=−1;
  **fail-CLOSED marks** — None/NaN/out-of-[0,1] → worst-case adverse, never a phantom gain) · `reward.py`
  (`spread_score` = the documented `S(v,s)=(v−s/v)²·b`, `reward_accrual` with the max_spread eligibility gate) ·
  `netpnl.py` (`MakerNetPnL` frozen breakdown + `net_pnl` — the identity computed IN net_pnl, no gross accessor;
  one-signed legs reject negatives, two-signed legs may be either sign) · `ledger.py` (`MakerLedger` append-only
  SQLite mirroring `ForecastLedger` EXACTLY — WAL, exact-string Decimals, idempotent `record_fill`, `record_settlement`
  WON/LOST-require-value / DISPUTED/VOID-require-None, dispute-flip overwrite clears the stale value, restart-stable) ·
  `quote_policy.py` (`decide_quote` QUOTE/WIDEN/PULL — CONSUMES the D1 `pull_quotes` seam; **fail-safe PULL** on
  any trigger {pull_quotes / recent_adverse>break_even / locked_effective>locked_cap} AND on any None/non-finite
  numeric — the quoting loop never crashes, never quotes into ambiguity) · `gate.py` (`MakerTracker` = the binary
  `go` over the honest net-of-cost sample per the pinned leg-derivations; **DISPUTED/VOID counted separately +
  EXCLUDED from every leg** (whale-flip immunity), unknown status → ValueError fail-loud; `go = n_settled ≥
  min_samples AND net > net_margin_min`, strict; `MakerGate` facade = the SINGLE seam S9 wires into, ANDs with the
  calibration `k`). Built via subagent-driven strict-TDD, 4 sub-slices (S8a–S8d), each spec-compliance +
  pinned-opus MUTATION BATTERY reviewed (~40 mutations across the slice, every one killed by a named test) + a
  final whole-slice opus review with a 7-mutation cross-cutting battery (the reward-gross doctrine-killer, the
  DISPUTED-inclusion leak, the adverse sign-flip, the `_SGN` ripple, active-fee zeroing, `settled()` leaking
  unsettled rows, the quote fail-safe) → **APPROVED FOR MERGE**. Genuine catches fixed along the way: an
  Infinity-accepted config knob (would poison the net identity), an `exponent=0`/`p=1` `0**0` crash, an unguarded
  rebate fraction, a plan-arithmetic errata (two negative-adverse net expectations dropped the fixed debits —
  implementer caught it, refused to bend the test, escalated; corrected 5.75→5.00 / 6.75→6.00 after independent
  verification), the sole config-injection mutation survivor pinned, and a fail-loud guard on divergent resolution
  marks for one token_id. **DATA-GATED / shadow-only:** `go_for(cat)` returns False until `n_settled ≥ min_samples`
  (default 150) AND the net-of-cost margin clears; cold → all-None stats, go False; nothing quotes/signs/sends.
  **DEFERRED seams (documented, not built):** live order placement/queue-position (POL-4) · real reward-pool data
  + the exact `S(v,s)`→pool mapping + `b` (deploy calibration) · the resolution feed flipping fills to WON/LOST→$1/$0
  (S6/S9) · a true aggressor-flow VPIN adverse measure (Fork 2 uses mark-out instead) · the live fills-recorder +
  `mark_for` = `LocalBook.midpoint()`/resolution wiring (S9) · the per-day×days `lockup_cost` folding (currently
  `rate × total notional` — needs the real time-to-resolution feed) · `net_inventory` awaits its S9/position-reporter
  caller. **Every live NUMBER is a re-pullable seam, not a trusted constant — the gate is only as honest as the
  fee/reward/dispute/lockup inputs S9 supplies.** POL-10 comment posted.
- **S9 shadow harness → earn-autonomy ramp controller (POL-11) — DONE + pushed (`origin/main` @ `826e210`; branch
  `pol-11-s9-harness`; pytest 1006→1113; docs/DESIGN-S9-HARNESS.md + docs/PLAN-S9-HARNESS.md):** the CAPSTONE that
  turns accrued shadow evidence into the earn-autonomy decision. A NEW package `src/polybot/harness/` mirroring
  `maker/`/`calibration/`'s shape (self-verifying config → pure Decimal calculators → append-only ledger → evidence
  evaluator → binary stage-machine controller). It COMPOSES the existing gates — S5 calibration `k`, S8 maker `go`,
  the S8 `net_pnl` identity, S4.5 `RestartReconciler`, S4.7's ratchet — and adds the walk-forward OOS split, the
  multiple-comparisons margin, the dispute-freeze stress, and the stage machine. **The honesty spine (mutation-pinned
  end-to-end): the controller advances a category ONLY on `net_of_everything`, OUT-OF-SAMPLE (`net_oos`, never
  `net_full`), positive-with-MC-margin PnL — never gross, never in-sample.** Modules: `config.py` (`RampConfig`
  self-verifying — Stage-0 thresholds, OOS holdout, the MC penalty, tail-survival minimums, `oos_n_bins`) · `fill_sim.py`
  (`simulate_fill` — maker-only resting entry from an intent + a LocalBook; crossed/stale/None-mid → `filled=False`
  fail-closed; reward via S8's `reward_accrual`) · `ledger.py` (`ShadowLedger` append-only SQLite mirroring `MakerLedger`,
  table `shadow_trades`, ordered by settled_at; fails loud) · `pnl.py` (`window_net` = the S8 seven-leg identity over a
  time-window, DISPUTED/VOID excluded, fails LOUD on an unhandled status — exact `MakerTracker` parity) · `evidence.py`
  (`evaluate_category` — the walk-forward OOS split, `required_margin = net_margin_min + mc_penalty·(family_size−1)`
  with a `family_size ≥ 1` guard, Brier-beats-mid + reliability over the OOS forecast window, reads `k_for`/`go_for`;
  `ready` = the AND of every gate; fail-closed cold/insufficient) · `stress.py` (`dispute_freeze_stress` — the
  DECISIONS-S0 §4 invariant: `reserve_after = nav − non-frozen-encumbered − adverse_fraction·frozen-cluster-wcr ≥
  reserve_floor`, inclusive at the $60 ceiling; `tail_survived`) · `ramp_controller.py` (`RampController.decide` →
  `RampDecision{stage, promote_recommended, ramp_down, reason}`; **advisory ramp-UP — NO cap-mutation surface at all,
  proven by an exact-`{decide}`-allowlist structural pin**; ramp-DOWN only raises a flag for the existing S4.7 ratchet;
  the $60 ceiling is structurally non-loosenable). **The ONE existing-file edit in the whole slice** is the additive
  `ERSController(reconciler=None)` boot seam + a `boot()` method (the DORMANT `wallet=None → RUNNING` shadow path,
  finally wiring the standalone S4.5 `RestartReconciler` into boot); `reconciler=None` == today byte-for-byte,
  `run_cycle` untouched. Built via subagent-driven strict-TDD, 4 sub-slices S9a–S9d, each spec-compliance +
  pinned-opus MUTATION BATTERY (~45 mutations across the slice) + a final whole-slice opus review with an 8-mutation
  cross-cutting battery (the `net_full`-for-`net_oos` doctrine-killer, the fail-open promote, the adverse sign-flip,
  the phantom-reward, the family_size fail-open, the `swap_caps` no-cap-surface probe, the inert-boot-seam flip, the
  stress cluster inversion) → **APPROVED FOR MERGE, no survivor**. Genuine catches fixed along the way: a `window_net`
  fail-loud deviation (silently dropped an unhandled status vs the plan's exhaustive raise — restored to exact
  MakerTracker parity), a **REAL fail-open — `evaluate_category` had no `family_size ≥ 1` guard, so `family_size=0`
  made `required_margin` negative and would pass a net-NEGATIVE OOS window through the money gate** (closed + pinned),
  two mutation-survivor coverage holes (the OOS-sample floor + the brier-skill sub-gate), a crossed-book fail-closed
  coverage gap, and two plan-arithmetic errata (the drafters' figures, caught + corrected after independent
  recomputation). **DATA-GATED / shadow-only:** `decide` returns `stage=SHADOW` until the sample clears every Stage-0
  gate AND the tail is survived AND the stress passes AND no breaker is tripped; nothing quotes/signs/sends/widens a
  cap. **DEFERRED (documented seams, not built):** actually RUNNING the shadow period to accrue ≥150 resolved/category
  (needs a DEPLOYED Hermes feeding the propose-only facade + continuous ingestion — operational) · the live
  fills-recorder feeding the harness · Stage-1+ LIVE cap-widening (the operator's human ramp-up gate + POL-4 signer) ·
  the real resolution/dispute feed. **POL-4 handoff caveats (non-blocking for shadow):** (1) `boot()` adopts the
  reconciler's rebuilt portfolio even on a DIVERGED status (it stays HALTED, so harmless in shadow) — when a live
  wallet is wired, a human resolving a divergence must treat the adopted portfolio as PROVISIONAL, not authoritative;
  (2) `mc_penalty`/`net_margin_min`/`reliability_max`/the fee schedule are conservative re-pullable deploy seams that
  must be re-calibrated before the shadow GO is trusted. POL-11 comment posted.

## 6. Docs to read (in the repo)
- `docs/CONTEXT.md` — onboarding; verified Polymarket/Hermes facts; landmines. **Read first.**
- `docs/DECISIONS-S0.md` — the finalized S0 decisions + §4 risk envelope (the numbers that replace the human).
- `docs/specs/2026-06-24-autonomous-polymarket-bot-design.md` — the full master design (§2 division of labor,
  §4 algorithm, §5 safety envelope L0–L8, §7 build decomposition S1–S9).
- `docs/DESIGN-S3-ERS.md` — the ERS decomposition + slice contracts (slices 1+2+3 done).
- `docs/DESIGN-S5-CALIBRATION.md` · `docs/DESIGN-S7-DETECTORS.md` — the calibration + detector decompositions.
- `docs/DESIGN-S6-HERMES.md` + `docs/PLAN-S6-HERMES.md` — the S6 design (resolved forks, the 12-step pipeline,
  §10 open risks) + the executed TDD build plan.
- `docs/DESIGN-S4-SAFETY.md` + `docs/PLAN-S4-SAFETY.md` — the S4 safety-envelope design (the 7 sub-slices, the
  kill-path architecture, §9 open risks) + the S4.1–S4.3 TDD build plan. **ALL 7 S4 sub-slices are now BUILT**
  (S4.1–S4.3 kill path, S4.4 anomaly, S4.5 reconcile, S4.6 Telegram, S4.7 breakers) — the envelope is CLOSED.
- `docs/DESIGN-S4.6-TELEGRAM.md` + `docs/PLAN-S4.6-TELEGRAM.md` — the S4.6 L8 TelegramController design (resolved
  forks: operator-trusted RESUME, durable-set BLACKLIST, in-session nonce; the five auth gates; the two-authority
  disjointness) + the executed 38-task TDD plan (4 sub-slices).
- `docs/DESIGN-S4.5-RECONCILE.md` + `docs/PLAN-S4.5-RECONCILE.md` — the S4.5 3-way reconcile design (resolved forks:
  settle-window keying, the $1-ceiling divergence metric, the DORMANT shadow path) + the executed 19-task TDD plan.
- `docs/DESIGN-S4.4-ANOMALY.md` + `docs/PLAN-S4.4-ANOMALY.md` — the S4.4 L5 AnomalyMonitor design (resolved forks:
  ALL-sticky halts, the default thresholds; the pinned severity order; §7 built-vs-deferred) + the executed 37-task
  TDD plan (5 sub-slices).
- `docs/DESIGN-S4.7-BREAKERS.md` + `docs/PLAN-S4.7-BREAKERS.md` — the S4.7 design (resolved forks: step sizes
  25%/50%, rolling windows, weekly cancel_all, sticky streak-pause; the rows-70-vs-72 interplay; the caps-swap
  re-plumb rationale) + the executed 34-task TDD plan (4 sub-slices). NB for S9/live wiring: the rate arm is
  BATCH-granular (one over-full pending batch can exceed 2/hr within a single cycle — bound the batch or
  re-consult per-intent when live); a frozen position masks ALL realized flow on its token_id (per-position
  attribution needs a fill/position journal linkage); optional defense-in-depth: a composition test co-wiring
  anomaly= AND lossbreakers= on one controller.
- `docs/DESIGN-S8-MAKER.md` + `docs/PLAN-S8-MAKER.md` — the S8 maker-rewards design (resolved forks: full
  accounting+quote-policy+gate scope, mark-to-resolution adverse selection, parameterized dossier-corrected fee
  schedule; the pinned §4 contract; §3 net-identity legs; the pinned tracker leg-derivations) + the executed
  28-task TDD plan (4 sub-slices). NB for S9/live wiring: every live number (fee schedule, `reward_b`, pool→`S(v,s)`
  mapping, P(dispute)/lockup/forced-taker-exit) is a re-pullable seam, NOT a trusted constant — re-pull/calibrate
  at deploy before any GO is trusted; inject `mark_for` = `LocalBook.midpoint()` live / resolution at settle (a
  missing feed fails closed = worst-case adverse, safe but pessimistic); `lockup_cost` = `rate × notional` until
  the real time-to-resolution feed lands; `net_inventory` awaits its S9 caller.
- `docs/DESIGN-S9-HARNESS.md` + `docs/PLAN-S9-HARNESS.md` — the S9 shadow-harness → ramp-controller design (resolved
  forks: one slice sub-sliced S9a–S9d, maker-primary fill reusing S8, walk-forward + multiple-comparisons OOS rigor,
  the boot-wiring as an opt-in `reconciler=None` seam; the pinned §4 contract; §3 criteria table; the honesty spine)
  + the executed 41-task TDD plan (4 sub-slices). NB for the shadow run / POL-4: the whole engine is dormant until a
  real shadow period accrues ≥150 resolved/category (needs a deployed Hermes + continuous ingestion); the conservative
  knobs (`mc_penalty`/`net_margin_min`/`reliability_max`/fee schedule) are re-pullable deploy seams; `boot()` adopts a
  DIVERGED reconciler's rebuilt portfolio while staying HALTED (provisional, not authoritative, once a live wallet lands).
- `docs/VERIFICATION-2026-06-24.md` — Phase-0 signing-path verification (rs-clob-client-v2).
- The **POL-3, POL-5, POL-6, POL-7, POL-8, POL-9, POL-10, POL-11, POL-12 YouTrack comments** — the detailed per-slice record.

## 7. Your task — pick based on what's ready

**HISTORICAL UPDATE 2026-07-05 — superseded by the 2026-07-10 block below.** "Deploy + run shadow" decomposed into a
code half + an ops half (see `docs/DESIGN-D4a-INGESTION-RUNTIME.md` + `docs/PLAN-D4a-INGESTION-RUNTIME.md`).
**D4a — the continuous ingestion runtime — is BUILT + on `main`** (new package `src/polybot/runtime/`: self-verifying
`IngestionConfig` + loader, `discover_universe`, the `IngestionRuntime` supervision core with durable `TaskGroup`
shutdown, `build_ingestion_runtime` + `main` + entry `python -m polybot.runtime.ingestion`, the `_supervised`
fail-loud guard; branch `pol-13-d4a-ingestion-runtime`; suite 1113 → 1145; strict-TDD, both-stage-reviewed per
sub-slice + a whole-slice review — read-only import invariant proven, durability spine mutation-pinned). Remaining
CODE seams for a full shadow loop: **D4a.2** (Polygon/news/synthetic + dynamic universe refresh) · **D1** MarketRegistry
(Gamma metadata → real category → warms `k`; today `StubMarketMeta` pins `k=0`) · **D2** resolution/settlement feed ·
**D3** shadow-execution wiring (fills-recorder → ShadowLedger, `mark_for`) · **D4b** the ERS+harness runtime. Remaining
OPS: the **Phase-0 VPS deploy** — a dedicated `polybot` user + a uv/standalone-3.13 venv + a system systemd unit,
mirroring `memebot` on the VPS `srv1779077` (100.111.199.109; runs memecoin-bot + a shared root Hermes; `/opt/<bot>`,
push-to-deploy via a `/root/git/<bot>.git` bare repo) → run ingestion → then layer the full shadow loop. Operator
state: **VPS + Hermes are up** (small model now, GPT-5.5 planned; polymarket must stay self-contained from the other
bots); **Polymarket account exists, $0 funded** (shadow needs no funds; POL-4/live still blocked on a funded clean box).

**UPDATE 2026-07-10 — D4a downsample code + release gate complete; deployment approval pending.** The POL-13 development tree
now keeps sharded WS books only in memory (`sink=None`), persists one strict versioned `clob-midpoint` batch every
60 seconds, and retains the full deduplicated Data API trade tape. The ERS co-move adapter auto-selects those batches
while explicit legacy raw replay remains available. Synthetic events are **not** reconstructable from this compact
production history and remain deferred pending a tuned live contract.

The VPS kit still exists (`srv1779077`; dedicated `polybot` user, `/opt/polymarket-bot`, system unit), but the
service remains **STOPPED + DISABLED** after the old raw firehose measured roughly 30 GB/day. The corrected D4a
build is on GitHub `main` but has not been installed, enabled, or started. Independent spec review passed, the final mutation battery
killed 41/41, and the required 1,800-second/200-market release gate passed without loosening the **≤0.5 GiB/day**
ceiling: elapsed 1,800.006 seconds; total DB+WAL+SHM 5,586,944 bytes; source counts
`{"clob-midpoint":29,"data-api":3500}`; 1,800 usable quotes; exactly zero raw rows; all batches decoded; no HALT;
graceful close; **0.249755 GiB/day**; exit 0. Earlier short evidence remains diagnostic only: 70-second probes
failed under startup/full-page distortion, while a 300.006-second five-market smoke passed at 0.406486 GiB/day.

Any later deployment requires separate approval and must use the GitHub-authoritative checkout pattern (never
recreate `/root/git/polymarket-bot.git`), preserve the old raw database under an evidence filename with recorded byte
size and SHA-256, install against a fresh `market_memory.db`, and leave the service stopped until explicit
start/enable approval. **OWNER DECISION (2026-07-05): finish the remaining build first, then a max-2-week light
shadow, then go live.** Remaining build order is **D2 → D3 → D4b → brain → 2-week shadow → live.**
The brain deploys as a dedicated `polymarket` Hermes profile. Go-live remains gated on POL-4 and a funded wallet on
a clean non-Windows box.

**UPDATE 2026-07-11 — POL-14 landed on `main` via
[PR #1](https://github.com/jouleka/polymarket-bot/pull/1).** The immutable, strict two-snapshot Gamma
registry validates condition, token, and Gamma-owned event identity, derives category only from
reviewed Gamma tag IDs, uses the market-owned deadline, and rejects unavailable metadata before
forecast/component writes. Merged `main` passes 1,482 tests; the 64/64 required mutation ledger and
19/19 bounded equivalent sweep have zero survivors. The merge commit is `31f3390`; it is not deployed
or runtime-composed, and POL-17 owns that composition.

**UPDATE 2026-07-14 — POL-15 resolution/settlement landed on `main` via
[PR #3](https://github.com/jouleka/polymarket-bot/pull/3) and is installed on the VPS.** The
owner-approved design is implemented:
two agreeing Polygon providers at five confirmations; exact CTF payout and pUSD token-position
authority; frozen reviewed UMA adapters with conservative dispute/manual classification; immutable
canonical terminals; FULL-durability central outbox and target receipts; restart recovery fencing;
and ordered idempotent fanout into Forecast, Maker, and Shadow. Exact reviewed head `6dc9f6a` passes
2,070 tests and merged as `5c4eb7b`. Fresh final specification, security/ABI, and mutation reviews
pass; the final
ledger covers 49 meaningful configurations across all 33 required families plus seven public-protocol
mutations with zero survivors. Repository evidence is in
[`VERIFICATION-POL15-RESOLUTION-SETTLEMENT.md`](VERIFICATION-POL15-RESOLUTION-SETTLEMENT.md).
The GitHub-linked service checkout is installed at the merge commit after a fresh 1,800.052-second
storage gate passed at 0.236382 GiB/day. The old raw database is checksummed and preserved under
`/opt/polymarket-bot/data/raw-firehose-20260714T155112Z`. The service remains stopped and disabled,
and POL-17 still owns continuous runtime composition. No chain write/signing path was added.

**UPDATE 2026-07-15 — POL-16 is independently reviewed and owner-approved for landing.**
Exact code/test candidate `1ebb026` atomically couples an ERS ACCEPT to a canonical two-target execution
outbox, re-fetches fresh best bid for a forced-BUY maker simulation sized only from approved stake,
and projects idempotently into Maker and Shadow. Target-commit crashes replay safely; a terminal that
wins the race produces exact already-settled rows; terminal values dominate live midpoint marks. The
real-stack test runs intent → ACCEPT/outbox → injected crash → replay → both ledgers → POL-15 terminal
fanout → exact marks. The canonical suite passes 2,121 tests. The independent review caught and
closed two real gaps: repeating Decimal share division could round infinitesimally above approved
notional, and reopen integrity did not fence invalid outbox state or execution-to-intent drift.
Explicit round-down and restart fences are regression-pinned and passed closing re-review. Twelve
local mutations plus a separate isolated 8/8 cross-cutting battery were killed with zero survivors.
The sacred validator/facade/caps/signer surfaces are untouched, and no runtime, signing, deployment,
or live-money path was added. Evidence is in
[`VERIFICATION-POL16-SHADOW-EXECUTION.md`](VERIFICATION-POL16-SHADOW-EXECUTION.md).

**Next work:** land POL-16, then begin POL-17 the continuous
ERS/harness runtime, and POL-18 the isolated propose-only Hermes brain. Only after those land and are deployed does the ≤2-week
paper/shadow period begin. The shadow must accrue honest resolved outcomes and prove calibrated,
net-positive, out-of-sample results; otherwise do not proceed.

**POL-4 remains the later live-money gate and is BLOCKED on the operator:** it needs a funded Polymarket deposit
wallet on a clean non-Windows box. Keys must never touch a compromised machine. When unblocked, build and
empirically place/cancel one minimum-size order through the official Rust client sidecar; do not infer signing
viability from documentation alone.

## 8. Landmines
- Never let Hermes compute size or touch keys (`propose_trade` is its only write tool, INSERT-only). When S6
  wires Hermes's MCP tools, expose a **propose-only facade** (the slice-2 review note in DESIGN-S3-ERS.md).
- Always re-fetch the live book before submitting; never trust a proposed/stale price.
- bonding/hold-to-resolution = tail-risky (UMA disputes, 1,150+ in 2026); maker ≠ automatically safe (adverse
  selection); correlation is a HARD pre-trade gate (treat unknown corr as +1).
- Broad taker fees since 2026-03-30 (only geopolitics is free) → strong maker-only bias.
- No real money until S4's kill path is tested against a wedged process AND S9 shadow proves a calibrated,
  net-positive, out-of-sample edge. If nothing clears its bar → DO NOT DEPLOY (inaction is free).
