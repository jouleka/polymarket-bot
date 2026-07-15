# PLAN — POL-16 shadow-execution wiring

**Design:** [`DESIGN-POL16-SHADOW-EXECUTION.md`](DESIGN-POL16-SHADOW-EXECUTION.md)

Every task is one serial RED→GREEN cycle. Run the focused file and the full suite before each
checkpoint commit. Never parallelize writers or mutate signing/risk surfaces.

## Task 1 — Typed execution and atomic ACCEPT outbox

1. Add failing store tests for `ShadowExecutionRecord` validation and exact round-trip.
2. Add failing tests proving ACCEPT decision, immutable audit, execution, and MAKER/SHADOW outbox
   rows commit together and survive reopen.
3. Add boundary tests: non-ACCEPT execution, wrong intent ID, invalid limit/ack, sequence ordering,
   exact idempotency, contradiction, and orphan/corrupt rows fail loud.
4. Implement the minimum additive schema and IntentStore methods.
5. Run focused tests, full suite, diff/marker/pycache checks, commit.

## Task 2 — Best-bid planner

1. Add failing planner tests for canonical happy path and hand-computed shares/reward.
2. Pin that the planner uses fresh best bid, hard-coded BUY, and approved stake—not target price,
   ask price, proposal side, or size suggestion.
3. Add stale/missing/one-sided/crossed/unfilled and bad decision/subject tests.
4. Implement `make_shadow_execution_planner` using existing `simulate_fill`.
5. Run focused tests, full suite, mutation probes for every authority input, commit.

## Task 3 — Idempotent Maker/Shadow projection

1. Add failing target tests for new canonical insert, exact replay, and contradictory duplicate.
2. Add a public canonical terminal decoder wrapper without changing terminal authority semantics.
3. Add failing terminal-before-replay tests: typed outbox application inserts exact settled rows;
   legacy `record_fill`/`record_trade` still reject post-receipt creation.
4. Implement `apply_shadow_execution` in both mirrored ledgers.
5. Implement `ShadowExecutionDispatcher.drain` and crash-after-target failure seam.
6. Run target/dispatcher/resolution tests, full suite, mutations, commit.

## Task 4 — Live/terminal mark provider

1. Add failing tests for pending live midpoint, terminal value, terminal precedence, DISPUTED/VOID,
   stale/missing book, unknown token, and durable contradiction.
2. Implement `make_mark_for` over the public ledger records and injected `book_for`.
3. Prove `maker.inventory.adverse_selection` consumes the callable conservatively.
4. Run focused/full tests and mutations, commit.

## Task 5 — ERS/controller wiring

1. Add failing tests for planner called only on ACCEPT and execution passed atomically to
   `record_decision`.
2. Pin unfilled ACCEPT (audit only), planner error → `REJECT shadow_execution_error` with no signer,
   and `shadow_planner=None` compatibility.
3. Thread the optional seam through `ERSController` without changing `run_cycle` ordering.
4. Run ERS/controller/facade/sacred-surface tests and full suite, commit.

## Task 6 — Whole-slice e2e and evidence

1. Build a real-stack e2e: intent → ACCEPT/outbox → injected crash → replay → both canonical rows →
   terminal fanout → exact marks.
2. Run mutation battery: ask-for-bid, target-for-bid, proposed side, proposed sizing, omit a target,
   acknowledge-before-commit, ignore duplicate contradiction, live-over-terminal mark, and
   post-receipt pending insertion.
3. Run full suite, compile, `git diff --check`, Markdown-link, no-marker, clean-tree, and sacred
   surface checks.
4. Perform independent specification/security review and final cross-cutting mutation review.
5. Reconcile HANDOFF/TICKETS/YouTrack and prepare the exact reviewed SHA for owner-approved landing.
