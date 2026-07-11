# POL-14 MarketRegistry verification evidence

Status: implementation branch evidence, not deployment authorization
Base: `f3331a4406d79ed2d510d62fb36ceb2d51137bac`
Branch: `pol-14-market-registry`

This file makes the contemporaneously captured RED/GREEN evidence repository-visible after the
independent review noted that the local `/tmp` logs were not visible from its clean checkout. It does
not rewrite or embellish the commit history. Each implementation slice was committed only after its
focused GREEN run. POL-14 does not authorize deployment, database migration, signing changes, or
service activation.

## Original serial slices

| Slice | Observed RED before production change | GREEN/commit |
|---|---|---|
| A1 result and category policy | `tests/test_ers_market_meta.py` stopped at collection because `DEFAULT_CATEGORY_POLICY` did not exist: `1 error in 0.15s`. Captured in `/tmp/pol14-task1-red.log`. | Focused suite: `38 passed`; `517aa41`. |
| A2 strict snapshot indices | Collection stopped because `MarketRegistry` did not exist: `1 error in 0.17s`. Captured in `/tmp/pol14-task2-red.log`. | Focused metadata suite exited 0; `299fa32`. |
| A3 dual-key lookup and clock | Lookup tests reached the A2 registry and failed because `metadata_for` did not exist: `19 failed, 87 passed in 0.31s`. Captured in `/tmp/pol14-task3-red.log`. | Focused metadata suite exited 0; `b5150a9`. |
| A4 ERS metadata gate | Two service tests failed: typed unavailability became `internal_error`, and the one-result metadata seam was never called: `2 failed, 30 passed in 0.91s`. Captured in `/tmp/pol14-task4-red.log`. | Focused metadata/service/E2E suites exited 0; `7e610bc`. |
| A5 whole slice | Test-only composition of real registry through ERS and S6 ledgers; no production behavior added. | Focused suites exited 0; `d8b7712`. |
| A6 event-contained identity | New result-validation, event-relationship, event-token-conflict, and registry-immutability tests failed for their intended missing behavior: `13 failed, 114 passed in 0.29s`. Captured in `/tmp/pol14-task6-red.log`. | Metadata suite and metadata/service/E2E suites exited 0; `2acd6ce`. |

## Independent-review repair slices

The independent specification review at `d8b7712` found real provider-boundary defects. Each
behavioral repair below was first reproduced with a focused failing test.

| Slice | Observed RED | GREEN/commit |
|---|---|---|
| A7 strict RFC3339 | Python-specific `X` and emoji separators were accepted: `2 failed, 9 passed in 0.13s`. Captured in `/tmp/pol14-a7-rfc3339-red.log`. | All 11 malformed-field cases passed; saved live Gamma sample still produced 10 usable rows; `ff4ca40`. |
| A8 exact token whitespace | Blank and whitespace-padded IDs were accepted: `2 failed, 5 passed in 0.15s`. Captured in `/tmp/pol14-a8-token-whitespace-red.log`. | All 7 token-shape cases passed; `1ad91c4`. |
| A9 clock overflow | A huge integer clock escaped as raw `OverflowError`: `1 failed in 0.13s`. Captured in `/tmp/pol14-a9-clock-overflow-red.log`. | Twelve clock-contract cases passed; `350b2ff`. |
| A10 validated construction only | Direct construction bypassed snapshot validation: `1 failed in 0.12s`. Captured in `/tmp/pol14-a10-private-constructor-red.log`. | Constructor/immutability/lookup targets passed; Pyright reported zero errors and Ruff passed on the changed metadata files; `7f74381`. |
| A11 mutation-strengthening | Correct production behavior already passed; tests were added specifically to kill representation-sensitive token conversion and later-element tag-validation mutants, and to pin fusion ordering. | Four focused contract tests passed; `9314559`. |
| A12 unrelated legacy identity | An unrelated event-contained row with an unhashable `conditionId` raised raw `TypeError` before the later valid selected relationship: `1 failed in 0.17s`. | The named regression passed, the complete metadata file reported `136 passed`, and the expanded mutation battery killed the corresponding guard-removal mutant; `68b40ba`. |

The first A9 test attempt used the enormous integer directly as a pytest parameter. Pytest failed
while generating its test ID because of Python's integer-to-string digit limit. That was a harness
failure, not accepted RED evidence. The value was moved behind a clock lambda, after which the test
reached `MarketRegistry.metadata_for` and reproduced the intended raw `OverflowError` shown above.

## Review-fix commits

- `0cb5b4f` pins the market deadline against a conflicting event deadline.
- `8d482e2` removes vacuous mutation passes by keeping both snapshot identities internally
  consistent in token-shape and cross-condition token-reuse tests.
- A7-A11 address the independent review's remaining implementation and test-strength findings.

## Adversarial mutation result

Corrected and expanded isolated-worktree battery at
`68b40ba7cf9b1f2d09227783140019369bd32fa2`:

- baseline focused suite: `173 passed in 1.24s`;
- mutations killed: `26/26`;
- survivors: none;
- restored focused suite: `173 passed in 1.42s`;
- restored worktree status: clean;
- mutation worktree removed and pruned.

The battery covered provider label/slug trust, both category-precedence boundaries, condition/token
sibling bypasses, cross-condition token reuse, numeric coercion, one/three-token acceptance,
market-versus-event deadline ownership, naive and Python-only timestamp acceptance, fractional-time
rounding and negative time, permissive unknown-category fallback, logging before metadata rejection,
unexpected-error swallowing, proposal-owned question substitution, multiple clock reads, registry
mutability, event-token conflict bypass, decimal-token integer round-trip, malformed later tag
skipping, whitespace tokens, direct-constructor bypass, and allowing an unhashable unrelated embedded
condition to reach the selected-identity maps. The complete local ledger was captured as
`/tmp/pol14-mutation-results.json`; temporary mutation files and the detached worktree were not added
to the repository.

## Final-candidate verification

The post-A12 code candidate `68b40ba7cf9b1f2d09227783140019369bd32fa2` produced:

- focused metadata/service/E2E suite: `173 passed in 0.87s`;
- full repository suite: `1446 passed in 9.39s`;
- `python3 -m compileall -q src scripts`: pass;
- Pyright on `src/polybot/ers/market_meta.py`: zero errors, warnings, or information diagnostics;
- Ruff on the changed metadata implementation and focused metadata/E2E tests: pass;
- `git diff --check origin/main`: pass;
- saved-live-snapshot probe: 100 market rows + 100 event rows produced 10 usable registry rows and
  resolved the sampled sports market's canonical 41-character question;
- the eight Ruff findings in the legacy `tests/test_ers_service.py` are identical on `origin/main`
  and the candidate, so POL-14 adds no Ruff regression there;
- `polymarket-ingestion.service`: inactive, dead, and disabled.

The fresh independent final review remains the only open release gate at this checkpoint.

## Required final gate

Before merge, record exact-HEAD evidence for:

1. focused metadata/service/E2E suites;
2. full repository pytest count;
3. compilation, diff, lint, and type checks with baseline diagnostics separated from feature changes;
4. the saved-live-snapshot probe;
5. the corrected adversarial mutation ledger with zero survivors;
6. a fresh independent specification review of the final commit;
7. clean tree, origin divergence, and inactive/disabled service state.

Until all seven are complete, POL-14 remains implemented but not approved for push, merge, or
runtime activation.
