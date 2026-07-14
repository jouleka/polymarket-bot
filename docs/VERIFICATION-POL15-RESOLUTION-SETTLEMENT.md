# POL-15 resolution and settlement verification evidence

Status: reviewed feature candidate; not merge, deployment, or live-money authorization

Base: `77dddef5e0988779a809572ceceeca372c5745e1`

Branch: `pol-15-resolution-settlement`

Code/test candidate: `c3378f22a8181437a6f98838f550cadd665f15dd`

POL-15 adds a read-only, two-provider Polygon resolution feed, immutable terminal authority,
restart-fenced durable outbox, and idempotent projection into Forecast, Maker, and Shadow ledgers.
It never signs, submits, redeems, deploys, starts a service, or moves funds. Continuous scheduling
and credentials remain POL-17 work.

## Approved contract and serial implementation

The owner approved the lean contract in
[`DESIGN-POL15-RESOLUTION-SETTLEMENT.md`](DESIGN-POL15-RESOLUTION-SETTLEMENT.md), implemented serially
through [`PLAN-POL15-RESOLUTION-SETTLEMENT.md`](PLAN-POL15-RESOLUTION-SETTLEMENT.md):

1. Immutable subject, exact payout, provider observation, dispute precedence, and canonical terminal
   models.
2. MarketRegistry/ERS identity propagation and settlement/signing race fences.
3. Atomic, immutable, FULL-durability target receipts for Forecast, Maker, and Shadow.
4. Durable central assessment/terminal/outbox/halt/recovery store.
5. Two-provider five-confirmation feed with per-condition isolation and original-coordinate recovery.
6. Strict Polygon JSON-RPC, frozen CTF/adapter authority, pUSD position authentication, bounded log
   history, conservative v1 and positive v2+ path classification.
7. Ordered apply-before-ack dispatcher with idempotent crash replay, persistent conflict halt, and
   restart recovery barrier.
8. Real-ledger, fake-provider, and all-four-adapter whole-slice verification.

Every behavioral cycle was committed only after its focused GREEN run. Independent findings were
reproduced with focused tests, repaired serially, and re-reviewed. The feature history intentionally
retains those small RED/GREEN commits rather than squashing away the audit trail.

## Independent sub-slice gates

### Feed boundary

The feed candidate `7857542` passed independent specification and mutation review after closing
halt-dominance, provider-contract, malformed-observation, and recovery-authority findings.

### RPC boundary

The RPC candidate `d236b9c951c3c4c8a8ae6c19741003111bb6b0d1` passed independent architecture,
frozen-ABI, and mutation review:

- focused RPC/feed: `103 passed`;
- full repository: `2,047 passed`;
- all 20 former survivor behaviors killed;
- five additional chain recheck, exact CTF dedupe/conflict, and exact page-filter mutations killed;
- clean isolated worktree and no mutation markers.

### Dispatcher boundary

The dispatcher candidate `6fcba099cb013703d5333c0c68c89aff95faac36` passed independent specification
and mutation review. The audit killed role/order, bounded and malformed limits, acknowledgement
ordering/omission, post-commit crash placement, transient overtake, conflict-without-halt,
acknowledging a conflict, health/recovery bypass, partial recovery, wrong return counting,
empty/whitespace halt messages, restart persistence, and replacement of the original conflict object.

### Whole-slice boundary

The code/test candidate `e407fdfdaa1524e964090668cc4b5f1fec643bf1` passed independent architecture,
ABI, and mutation reviews:

- parent focused integration run: `519 passed`;
- parent full repository run: `2,069 passed in 29.91s`;
- independent architecture run: `276 passed`, compile/diff/cleanliness PASS;
- independent ABI run: `43 passed`, exact all-policy deployment traces PASS;
- independent mutation run: `556 passed in 17.90s`, zero survivors.

The whole-slice audit killed skip-target/ack, hook-order and non-idempotent replay, fractional
slot/value/status/rational corruption, legacy settlement, missing zero-row receipt, late-row creation,
lifecycle/path collapse, disagreement abort, accepted-terminal re-observation, restart-recovery bypass,
post-acceptance contradiction suppression, v1-manual collapse, frozen-policy omission, bogus adapter
deployment, excluded real-ledger projection corruption, and each Calibration/Maker/Evidence dispute
counter omission for both DISPUTED and MANUAL.

## Candidate verification

At whole-slice candidate `e407fdfdaa1524e964090668cc4b5f1fec643bf1`:

- `tests/test_resolution_e2e.py`, dispatcher, RPC, feed, and target-ledger suite: `519 passed`;
- canonical full suite: `2,069 passed`;
- `python3 -m compileall -q src scripts`: pass;
- `git diff --check`: pass;
- repository-local Markdown links: pass;
- no `MUTATION` marker under `src/`;
- clean worktree.

## Final whole-slice gate

Fresh final reviewers evaluated docs-inclusive code candidate
`c3378f22a8181437a6f98838f550cadd665f15dd` from base
`77dddef5e0988779a809572ceceeca372c5745e1`:

- specification/acceptance: PASS; full repository `2,070 passed`;
- security/authority/ABI: PASS; full repository `2,070 passed`;
- mutation: PASS; 49 meaningful configurations across all 33 required families plus seven public
  `ResolutionProvider` protocol mutations, zero survivors;
- restored mutation worktree: `888` focused and `2,070` full tests passed;
- compile, base-to-head diff, Markdown links, no-marker, clean-tree, and untouched sacred-surface
  checks: PASS;
- `polymarket-ingestion.service`: inactive and disabled.

The only remaining gate is explicit owner approval of the final documentation-reconciliation SHA
before any push or `--no-ff` merge. No deployment, database migration, service activation, signing
change, chain write, or live-money action is authorized by this evidence.
