# HANDOFF — autonomous Polymarket bot (state as of 2026-06-29)

You are taking over an in-progress build. Read this top to bottom, then read the linked docs + the
YouTrack comments, then start at **"Your task"**. The conventions are ENFORCED — do not skip them.

> This supersedes the original POL-3 handoff (preserved in git history). It reflects everything shipped
> through S3/POL-5 slice 2.

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

## 2. Environment / how to run (Windows host, repo in WSL)
- **Repo:** WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot` (GitHub `jouleka/polymarket-bot`).
  The `~/Public/WorkRepos/...` paths in old docs are macOS and DO NOT apply here.
- Run git/python via `wsl -d Ubuntu -- bash -lc '...'`. Edit/Read/Write files via UNC
  `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...`.
- **Venv** (gitignored; system python3.12 lacks ensurepip): `uv venv --python 3.13 .venv && uv pip install
  --python .venv/bin/python pytest "httpx>=0.28" "websockets>=16"`. Tests: `./.venv/bin/pytest` →
  **224 passing** (pyproject sets `pythonpath=["src"]`, no install needed).
- **FIRST: `git pull`** — origin is usually ahead of a fresh local clone and unfetched (it looks docs-only
  until fetched).
- **WSL gotchas:** `wsl bash -lc '...'` mangles single quotes / `$()` / heredoc f-strings (the wrapper is
  single-quoted). For commit messages: write the message to a file via the Write tool, `tr -d "\015"` to
  sanitize CRLF, then `git commit -F <file>`. UNC Read/Edit/Write usually work but intermittently throw
  EISDIR (9p glitch) — retry. Free keyless Polygon RPC: `https://polygon-bor-rpc.publicnode.com` (UA header).
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
| **POL-6** | S4 — Safety envelope + supervisor + reconciliation + Telegram | **KILL PATH (S4.1–S4.3) DONE + pushed** (`pol-6-safety-envelope` → main; 517 tests; SafetyController op-state gate + signer de-risk/GTD/startup-self-test + out-of-band supervisor & the WEDGED-PROCESS acceptance gate — proves SIGKILL-necessity, fate-isolated, best-effort-all de-risk on the supervisor's OWN signer, GTD exits survive; PaperSigner-only/no signing; 2 Opus deep-dives + final whole-slice review. **S4.4–S4.7 (L5 anomaly, 3-way reconcile, Telegram, realized-loss breakers) are contract-level in `DESIGN-S4-SAFETY.md`, NOT yet built**) |
| POL-7 | S5 — Calibration + base-rate prior + Anchor Gate | **DONE + pushed** (`origin/main` @ `1ad52f5`; calibration tracker + prior + Anchor Gate; deep ERS wiring deferred to S6) |
| **POL-8** | S6 — Hermes integration + signal fusion + truth-gate | **DONE + pushed** (`pol-8-hermes-s6` → main; 448 tests; §4.1 fusion + ERS-side citation truth-gate + propose-only facade + `process_pending` wiring; built as pure units, runs end-to-end on PaperSigner; 3 Opus deep-dives — caught + fixed a CRITICAL corroboration bypass (C1) and an orphan-forecast edge; live-Hermes MCP transport + adaptive fusion + MarketRegistry + resolution-feedback DEFERRED) |
| POL-9 | S7 — Smart-money / insider detectors (defensive) | **DONE + pushed** (`origin/main` @ `a6d91dc`; PnL + luck filter + D1–D6 + composite + policy; FOLLOW hard-off; live wiring deferred) |
| POL-10 | S8 — Maker-rewards module | Not started |
| POL-11 | S9 — Shadow harness + ramp controller | Not started |

**Critical path:** `S0 → S2 → S3 → S4 → S6 → S9`, with `S1` feeding `S5`/`S7`. **No real money** until S4's
kill path is tested against a wedged process AND S9 shadow proves a calibrated, net-positive, out-of-sample
edge.

## 5. What is already built (all on `origin/main`, all TDD'd + Opus-reviewed + live-verified; 517 tests)
- **S1 ingestion (`src/polybot/ingestion/` + `core/` + `storage/`):** Gamma normalizer · CLOB market-WS
  collector (sharding + client keepalive + mid-stream sequence-gap detection & resync) · LocalBook
  (staleness-gated) · Data API poller · Polygon on-chain log watcher (CTF ERC-1155 + Exchange,
  empirically-discovered topics) + bounded selective RPC retry (`ingestion/retry.py`) · news fast-path
  (allowlist-gated, sanitized UNTRUSTED, XXE-guarded) + curated `ingestion/allowlist.py` (live-validated;
  **operator must review PRIMARY before it informs trades**) + `news.CalendarScheduler` · synthetic events ·
  the **replay-fidelity / no-look-ahead acceptance gate** (`scripts/replay_fidelity_check.py`,
  staleness-aware + `--forced-resync`) · Market-Memory `EventStore` (append-only) +
  `storage/event_writer.QueuedEventWriter` (off-loop single-writer — **prod WS shards may now rise past 2**,
  verified by `scripts/shard_endurance_check.py`: 6 shards / ~220 rows/s / writer peak backlog ~40 vs 100k).
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
  UMA stub) · S4.5 3-way reconciler + restart-reconcile · S4.6 Telegram (auth/nonce/safety-increasing-only) · S4.7
  realized-loss breakers + ramp-DOWN (consumes the dormant `would_cross_daily_pending_ceiling` predicate) · the
  live-POL-4 primitives (live cancelAll/credential-separation/real canary) + box hardening (systemd/users/egress).

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
  kill-path architecture, §9 open risks) + the S4.1–S4.3 TDD build plan (S4.4–S4.7 are contract-level / not built).
- `docs/VERIFICATION-2026-06-24.md` — Phase-0 signing-path verification (rs-clob-client-v2).
- The **POL-3, POL-5, POL-7, POL-8, POL-9, POL-12 YouTrack comments** — the detailed per-slice record.

## 7. Your task — pick based on what's ready
**The critical path is POL-4 (S2 signing), and it is BLOCKED on the operator:** it needs a funded Polymarket
deposit wallet on a CLEAN non-Windows box. Keys must NEVER touch the Windows/WSL box (documented cracked-game
malware vector). You CANNOT do POL-4 from this machine. So:

- **If the operator has funded a deposit wallet on a clean box →** do **POL-4** (build the signer +
  order-construction on the official Rust `Polymarket/rs-clob-client-v2` as a sandboxed sidecar; Python/TS V2
  SDKs are broken for new deposit wallets; acceptance = empirically place + cancel ONE real min-size order —
  prove rs 0.5.x live, don't guess). This unblocks S3 slice-2's signer seam → S4 → S6 → S9.

- **If NOT funded → continue the no-funding work.** **S3 slice 3, S5/POL-7, S7/POL-9, S6/POL-8, AND the
  S4/POL-6 KILL PATH (S4.1–S4.3) are now DONE + pushed** (see §5). Recommended next, in order:
  1. **Finish the S4 safety envelope (POL-6, S4.4–S4.7)** — kill-path-first is done; the rest is contract-level in
     `DESIGN-S4-SAFETY.md`, all buildable in shadow: S4.4 L5 AnomalyMonitor (clock-skew/abnormal-book/WS/API-storm/
     signing-canary; UMA stub) · S4.5 3-way reconciler + restart-reconcile (the injected-divergence test) · S4.6
     Telegram (auth/nonce/safety-increasing-only, fake transport) · S4.7 realized-loss breakers (daily/weekly/
     consecutive — wires the dormant `would_cross_daily_pending_ceiling`) + auto ramp-DOWN.
  2. **S8 / POL-10 — maker-rewards module** (shadow, honest net-of-adverse-selection; consumes the D1
     `pull_quotes` seam).
  3. **S9 / POL-11 — shadow harness → ramp controller** (the capstone: paper-trade net of
     fees/slippage/lockup/dispute haircut → the calibrated, net-positive, out-of-sample GO/NO-GO evidence).
     NB: S9 needs proposals actually flowing — i.e. a DEPLOYED Hermes feeding the propose-only facade + the
     read-only ingestion running continuously to warm the data-gated machinery (k stays 0 until ≥150 honest
     resolutions accrue, so nothing sizes live until then).
  - **Smaller / feeding:** real latent-cluster assignment (S3 follow-up — makes `comove.py` bite cross-event
    instead of the `event_id` placeholder) · GDELT slow-path · run the read-only ingestion continuously to
    warm comove/priors · the S6 deferreds (live-Hermes MCP transport + an injection probe vs a real Hermes;
    MarketRegistry: Gamma metadata → category/question/seconds; resolution-feedback to warm k; a true
    before/after mid-diff for the same-source gate, DESIGN §10).

## 8. Landmines
- Never let Hermes compute size or touch keys (`propose_trade` is its only write tool, INSERT-only). When S6
  wires Hermes's MCP tools, expose a **propose-only facade** (the slice-2 review note in DESIGN-S3-ERS.md).
- Always re-fetch the live book before submitting; never trust a proposed/stale price.
- bonding/hold-to-resolution = tail-risky (UMA disputes, 1,150+ in 2026); maker ≠ automatically safe (adverse
  selection); correlation is a HARD pre-trade gate (treat unknown corr as +1).
- Broad taker fees since 2026-03-30 (only geopolitics is free) → strong maker-only bias.
- No real money until S4's kill path is tested against a wedged process AND S9 shadow proves a calibrated,
  net-positive, out-of-sample edge. If nothing clears its bar → DO NOT DEPLOY (inaction is free).
