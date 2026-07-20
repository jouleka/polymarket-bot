# PLAN — POL-13: Hermes opportunity discovery correction

## Delivery discipline

Each implementation task is a serial RED → minimum GREEN → focused verification → checkpoint.
No production file or service changes occur until the reviewed branch is complete.

## TDD tasks

1. **Urgent market ordering.** Add one failing read-view test for mixed positive/zero deadlines;
   implement the deterministic sort; run the focused file and checkpoint.
2. **Live-book projection.** Add one failing test proving one sampled live-token generation marks
   each outcome; minimally add the optional provider; run focused tests and checkpoint.
3. **Bounded news storage query.** Add a failing storage test for allowlisted-source newest-first
   pagination without firehose materialization; add the read-only query to both store variants; run
   focused tests and checkpoint.
4. **Sanitized news view.** Add a failing projection test for allowlist/tier filtering, exact citation
   identity, content bounds, and pagination; implement `NewsReadView`; run focused tests and
   checkpoint.
5. **Facade/RPC/MCP exact-six surface.** Add serial failing tests for optional facade delegation,
   parameter validation/dispatch, tool schema, and unknown-method refusal; minimally wire
   `get_news`; run focused tests and checkpoint.
6. **Composition root.** Add a failing root test that proves production uses the existing read-only
   EventStore and shared live books; wire both readers; run focused runtime tests and checkpoint.
7. **Deployment/profile contract.** Add a failing verifier/deploy test for exact-six inventory and
   corrected cron prompt; update templates/install/verifier artifacts; run focused deploy tests and
   checkpoint.
8. **Whole-slice regression.** Extend the proposal boundary test through sanitized evidence → cited
   proposal → deterministic truth gate while retaining live-book re-fetch and paper outbox behavior.
9. Run the canonical full suite, then checkpoint documentation and verification evidence.

## Independent review and adversarial gate

1. Independent specification/security review reads the complete diff and runs focused plus full
   tests, checking the additive/untouched-files invariant and one-write-only authority.
2. Isolated mutations must be killed for: condition-ID ordering restoration, zero-first ordering,
   stale persisted-book substitution, live-token snapshot omission, source allowlist bypass, tier
   mismatch admission, unbounded news query/content, newest-order reversal, citation-ID mutation,
   RPC extra/missing method, facade mutation exposure, prompt citation fabrication, and any signer or
   runtime-authority expansion.
3. Re-review every fix, rerun the entire suite, remove mutation residue/caches, and require a clean
   tree before publication or installation.

## Pre-activation production-data correction

The stopped exact-six preflight found that more than 1,001 newer DISCOVERY rows displaced every
PRIMARY row from the bounded evidence scan. Resolve serially before starting Hermes:

1. Add a failing storage test for one older priority source behind a discovery flood; minimally add
   an optional exact-source priority order while preserving the no-priority behavior.
2. Add a failing read-view test proving priority comes from the pinned PRIMARY allowlist identities;
   minimally pass those exact source names into the bounded store query.
3. Extend the existing whole-slice proof with newer discovery rows and require citable evidence to
   remain first without changing truth-gate authority.
4. Run focused and canonical suites, independent specification/security review, and mutations for
   priority removal, row-tier priority, unbounded reads, pagination drift, and discovery promotion.
