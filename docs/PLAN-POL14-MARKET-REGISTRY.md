# POL-14 — D1 MarketRegistry implementation plan

**Design contract:** [`DESIGN-POL14-MARKET-REGISTRY.md`](DESIGN-POL14-MARKET-REGISTRY.md)
**Execution rule:** serial strict TDD; observe every RED before production implementation; commit only
on green; stop and revise the design if an assumption changes.

## Baseline and scope

- Base: GitHub `main` at `f3331a4406d79ed2d510d62fb36ceb2d51137bac`.
- Baseline: 1,313 tests passing after the POL-13 merge.
- Primary implementation: `src/polybot/ers/market_meta.py`.
- ERS integration: `src/polybot/ers/service.py`.
- Targeted tests: `tests/test_ers_market_meta.py`, `tests/test_ers_service.py`, and one whole-slice
  registry integration test.
- Do not touch risk caps, signer code, deployment scripts, service state, database state, or the
  current ingestion runtime.

## Task 0 — Freeze the approved design

1. Add the design and this plan.
2. Run `git diff --check` and validate local Markdown links.
3. Commit the approved contract before production code.

## Task 1 — Frozen result and category policy

RED:

- Add tests for frozen `MarketMetadata`.
- Add table-driven tests for each approved tag-ID category.
- Add every precedence boundary and prove labels/slugs do not activate an unreviewed ID.
- Add malformed tag wire-shape tests.

GREEN:

- Implement `MarketMetadata`, category constants, and a pure category-classification helper.
- Make policy inputs immutable and reject invalid policy definitions.

REFACTOR/VERIFY:

- Run only the new category/result tests, then all `test_ers_market_meta.py` tests.
- Commit on green.

## Task 2 — Strict two-snapshot construction and identity indices

RED:

- Representative live-shaped market/event rows build a registry.
- Gamma JSON-string and already-parsed token arrays both work.
- Numeric, empty, duplicate, or non-binary token arrays fail.
- Missing/malformed condition, event, question, deadline, or top-level shape fails.
- A missing event and an unmapped event are unavailable rather than `unknown`.
- Identical duplicate rows are idempotent.
- Conflicting condition definitions and token reuse across conditions fail loudly.
- If no usable market remains, construction fails loudly.

GREEN:

- Implement strict parsing, offset-aware deadline conversion, the event-category join, immutable
  condition/token indices, and typed construction/lookup errors.

REFACTOR/VERIFY:

- Run the smallest named test after each behavior, then the complete market-meta target.
- Commit on green.

## Task 3 — Lookup clock and dual-identifier contract

RED:

- Both condition and token must resolve to the same definition.
- Unknown condition, unknown token, and mismatched known siblings are unavailable.
- Gamma question/category override proposal-owned values.
- One wall-clock read is used per lookup.
- Positive fractional seconds floor to an integer; at/past deadline clamps to zero.
- NaN/infinite/non-numeric clock values fail closed.
- Repeated lookup does not mutate the registry.

GREEN:

- Implement `MarketRegistry.metadata_for(intent)` and dynamic time calculation.
- Add `StubMarketMeta.metadata_for` while retaining its explicitly legacy adapter methods.

REFACTOR/VERIFY:

- Run the full market-meta target and commit on green.

## Task 4 — ERS fail-closed integration

RED:

- Replace the three independent metadata calls with one metadata object.
- A typed unavailable error returns `REJECT market_meta_unavailable`.
- The unavailable path writes no forecast and no component row.
- An unexpected metadata bug still maps to `internal_error`.
- A real-registry happy path records the canonical category/question/time and reaches the existing
  calibration/validator path.

GREEN:

- Add the explicit reason code/catch in `service.py`.
- Update test fixtures to the single metadata contract without weakening legacy coverage.

REFACTOR/VERIFY:

- Run named service tests, the Hermes E2E target, and the complete market-meta target.
- Commit on green.

## Task 5 — Whole-slice and compatibility checks

1. Add a representative two-market/two-event fixture shaped like the 2026-07-10 live responses.
2. Prove end-to-end category precedence, market-specific deadline, exact 77-digit token ID, canonical
   question, and mismatch rejection.
3. Prove `StubMarketMeta` remains explicit and deterministic for legacy tests.
4. Run all targeted tests, `python3 -m compileall -q src scripts`, and `git diff --check`.
5. Run the entire suite with `./.venv/bin/pytest -o addopts="" -q`.

## Task 6 — Independent gates

### Specification review

Give a fresh reviewer only the approved design, ticket text, base/head diff, and test output. Require a
PASS/FAIL verdict against every acceptance criterion. Fix only concrete findings, rerun targeted and
full tests, and re-review changed safety behavior.

### Adversarial mutation review

Use a detached temporary worktree at the exact reviewed commit. At minimum mutate independently:

1. classify by tag label/slug instead of reviewed ID;
2. reverse geopolitics/politics precedence;
3. reverse crypto/finance precedence;
4. trust condition while ignoring token;
5. trust token while ignoring condition;
6. allow conflicting token reuse;
7. coerce numeric token IDs;
8. accept one token or three tokens;
9. use event deadline instead of market deadline;
10. accept a naive or malformed timestamp;
11. use `ceil` instead of `floor` near the deadline;
12. allow negative seconds after deadline;
13. map unavailable metadata to `unknown`;
14. record a forecast before metadata rejection;
15. swallow unexpected metadata errors as the known unavailable reason.

Each mutant must be killed by a named test for the intended behavioral reason. Probe sibling fields,
later collection elements, duplicate-order variants, and separate clock invalid classes for equivalent
survivors. Restore byte-clean state and rerun the targeted suite.

## Task 7 — Final checkpoint

- Working tree clean on `pol-14-market-registry`.
- Full suite green with explicit count.
- Spec review PASS.
- Mutation review PASS with kill count and no survivors.
- Service remains inactive/disabled; no deployment performed.
- Post exact evidence to POL-14, but do not resolve/merge/push without the next owner gate.
