# POL-16 shadow-execution verification evidence

Status: implementation and local adversarial verification complete; independent review and landing
approval remain pending

Base: `65a6d7e392a9e5885a5d198a1eb5a1d9f8c4a270`

Branch: `pol-16-shadow-execution-wiring`

Code/test candidate: `ecb87e2`

POL-16 connects an ERS paper ACCEPT to the existing maker simulator, commits the ACCEPT and a
two-target execution outbox atomically, projects exact canonical economics into Maker and Shadow,
survives target-commit crashes and terminal races, and supplies terminal-first marks. It does not
schedule a runtime, sign, submit, deploy, start a service, or move funds.

## Contract and serial implementation

The owner approved
[`DESIGN-POL16-SHADOW-EXECUTION.md`](DESIGN-POL16-SHADOW-EXECUTION.md), implemented serially through
[`PLAN-POL16-SHADOW-EXECUTION.md`](PLAN-POL16-SHADOW-EXECUTION.md):

1. Typed canonical execution plus atomic ACCEPT/audit/two-role outbox.
2. Fresh-best-bid, forced-BUY planner sized from the ERS-approved stake.
3. Idempotent, conflict-detecting Maker/Shadow projection with crash replay.
4. Exact already-settled insertion when a canonical terminal wins the replay race.
5. Terminal-first resolution marks with conservative live-midpoint fallback.
6. Optional ERS/controller wiring and a real-stack whole-slice test.

The serial code history is `38a1661`, `a3a9739`, `a830410`, `6a604ea`, `f9a4603`, `5b874e5`, and
`ecb87e2`. The `shadow_planner=None` seam preserves existing callers, and the general Maker/Shadow
recording APIs retain their post-terminal rejection behavior.

## Local specification and authority review

A base-to-head read of every production change against the approved contract found no unresolved
local finding:

- planner authority is the post-ACCEPT fresh book, canonical POL-15 subject, and approved stake;
- ACCEPT/audit/execution/MAKER+SHADOW outbox commit in one SQLite transaction;
- target apply precedes acknowledgement and exact duplicates are compared field-for-field;
- terminal replay authenticates canonical terminal bytes and checks the full subject before insert;
- terminal rows dominate live books, while ambiguous or corrupt marks fail closed;
- `ers/validator.py`, `ers/facade.py`, `ers/caps.py`, and `ers/signer.py` are byte-untouched;
- no runtime, systemd, chain-write, signing, submission, cancellation, or live-money surface was
  added.

This was a rigorous same-agent review, not the independent different-model review required by
`AGENTS.md`. The current environment did not authorize spawning a reviewer, so this candidate must
not be represented as independently approved or merged until that gate is completed.

## Local mutation battery

Each mutation was applied alone to the clean candidate, its named focused test was observed failing,
and the source was restored before the next probe. All 12 were killed:

| Mutation | Killing test / observed failure |
|---|---|
| fresh best bid → best ask | `test_planner_uses_fresh_best_bid_forced_buy_and_ers_approved_notional` |
| forced BUY → proposal side | `test_planner_uses_fresh_best_bid_forced_buy_and_ers_approved_notional` |
| approved stake / resting bid → approved stake / executable ask | `test_planner_uses_fresh_best_bid_forced_buy_and_ers_approved_notional` |
| acknowledge before target apply | `test_dispatcher_crash_after_maker_commit_replays_then_reaches_shadow` |
| ignore a contradictory Maker duplicate | `test_dispatcher_does_not_acknowledge_contradictory_duplicate` |
| suppress terminal mark authority | `test_mark_uses_live_midpoint_until_terminal_then_terminal_value_dominates` |
| disable typed post-terminal replay | `test_terminal_before_execution_replay_inserts_exact_already_settled_rows` |
| invoke planner for REJECT/SKIP | `test_process_pending_never_plans_rejected_or_skipped_intents` |
| planner failure silently keeps ACCEPT | `test_shadow_planner_error_rejects_before_signer_or_portfolio_side_effect` |
| remove pre-commit failure seam | `test_failure_before_accept_outbox_commit_rolls_back_every_surface` |
| ignore a missing outbox target role | `test_reopen_rejects_missing_target_role_and_noncanonical_sibling_json` |
| accept non-canonical sibling JSON | `test_reopen_rejects_missing_target_role_and_noncanonical_sibling_json` |

The restored worktree passed `git diff --exit-code` before documentation reconciliation.

## Candidate verification

At code/test candidate `ecb87e2`, with the docs reconciliation patch present:

- canonical full suite: `2,118 passed in 34.42s`;
- focused execution/store/e2e/service/controller suite: `97 passed`;
- `python -m compileall -q src scripts tests`: pass;
- `git diff --check`: pass;
- repository-local links added or changed by POL-16: pass;
- no `MUTATION` marker under `src/`;
- sacred validator/facade/caps/signer surfaces: byte-untouched;
- restored implementation tree clean before the documentation-only reconciliation.

## Whole-slice proof

`tests/test_shadow_execution_e2e.py` uses the real IntentStore, ERS processing, S9 simulator,
MakerLedger, ShadowLedger, ResolutionStore, and ResolutionDispatcher. It proves:

1. an untrusted SELL/target/oversize proposal becomes an ERS-approved forced BUY at fresh best bid;
2. the decision and both target deliveries are durable;
3. a crash after Maker commit leaves both outbox targets replayable;
4. replay is idempotent and reaches Shadow with identical canonical identity/economics;
5. POL-15 settles Forecast, Maker, and Shadow from one terminal; and
6. terminal marks return the payout without consulting a live book.

## Remaining landing gates

- fresh independent specification/security review by a different strong reviewer;
- independent adversarial/cross-cutting mutation review;
- final clean-tree check after the documentation evidence commit;
- owner approval before push, PR, merge, YouTrack transition, or deployment.

POL-17 remains responsible for continuous runtime composition. This evidence does not authorize
service activation or live execution.
