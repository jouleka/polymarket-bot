# VERIFICATION — POL-13 evidence-aligned market selection

**Date:** 2026-07-21
**Implementation commits:** `1432bcf`, `babd676`, `0f168cb`, `4cf5c14`
**Result:** build PASS; landing and live reconciliation pending

## 1. Root cause and scope

Natural Hermes runs 998/999 completed correctly but selected unsupported esports markets and returned
`[SILENT]`. A direct live registry read found 99 available markets: 49 sports, 19 politics, 17
geopolitics, 10 crypto, three finance, and one weather. Supported non-sports candidates with live
books already appeared inside the first twenty rows. The defect was category-blind opportunity
selection, not execution, paper-account availability, or missing book transport.

The correction changes the existing prompt to scan exactly twenty sanitized markets, refuse sports,
and select only categories covered by the reviewed PRIMARY set. Independent review then identified
that prompt policy was not a deterministic security boundary. Production now also wires an exact
frozen evidence-category set into `HermesPipeline`; unsupported trusted registry categories reject
as `evidence_category_unsupported` before fusion, forecast/component writes, sizing, shadow planning,
or signing. The optional `None` default preserves existing seams.

No tool, source, process, collector, schema, database, signer, cap, model installation, or authority
was added. `evaluate_intent`, the exact-six facade, and the existing truth/fresh-book gates are
unchanged.

## 2. Strict TDD evidence

1. The initial prompt-contract test failed because no sports/category rule existed, then passed after
   the minimum bounded prompt change. Canonical checkpoint: 2,411 passed.
2. The deterministic sports proposal test first failed because `HermesPipeline` had no
   `evidence_categories` seam, then passed after the production-wired pre-write rejection gate.
   Canonical checkpoint: 2,412 passed.
3. Isolated prompt mutations exposed unpinned page/tool boundaries. Exact-bound tests were observed
   RED against `limit=10` and extra-tool permission mutants, then GREEN after restoration. Removing
   production category wiring was separately observed RED. Canonical checkpoint: 2,413 passed.
4. Closing review exposed a reject-all liveness survivor and contradictory-prompt survivor. A
   supported `politics` positive path was observed RED against an unconditional gate; the exact
   prompt SHA pin was observed RED after adding a contradictory tech/second-page instruction. Both
   passed after restoring the reviewed implementation. Canonical checkpoint:

```text
2414 passed in 33.78s
```

## 3. Independent review

The first review found one HIGH issue: an LLM instruction alone could not enforce the trusted market
category. It also found weak substring prompt tests. The deterministic production gate, rejection
ordering/no-write proof, exact wiring test, restrictive clauses, exact page/tool bounds, and prompt
fingerprint closed that issue.

The second review confirmed the HIGH closed and found two MEDIUM survivors: no supported-category
positive path and contradictory prompt text outside the checked substrings. Both were closed by the
fourth TDD cycle. Closing re-review at exact implementation head
`4cf5c1471810bdd86be9415bb506187b95ff0a05` passed with no new findings: 92 focused tests and
2,414 canonical tmpfs tests passed in 9.75 seconds.

## 4. Adversarial mutation evidence

The initial isolated prompt battery killed 6/9 mutations. It killed sports exclusion removal,
unsupported-category expansion, evidence-relevance removal, live-book removal, eligible-citation
removal, and synthetic-proposal permission. It exposed three survivors: page limit 20 -> 10, page
limit 20 -> 100, and extra-tool permission. All three received observed RED closure tests.

The closing isolated tmpfs battery at exact implementation head `4cf5c14` killed 18/18 mutations
with zero survivors. Its 11/11 prompt attacks covered sports removal, category broadening, relevance
removal, page 20 -> 10, page 20 -> 100, live-book removal, ineligible citations, synthetic activity,
extra tools, a contradictory override that preserved all old safe substrings, and a second market
page. Its 7/7 deterministic attacks covered production wiring removal, covered-set expansion with
sports, membership inversion, moving rejection after fusion/writes, allowing unsupported categories,
reason collapse, and rejecting every configured category. Baseline and post-restore focused result:
92 passed. The isolated worktree was removed and the shared checkout was untouched.

## 5. Operational boundary

This evidence authorizes no live-money action. Landing, stopped-safe service-checkout installation,
native in-place cron prompt update, and natural-cycle observation remain separate steps. The existing
profile, model/provider inheritance, cron ID/schedule/history, exact-six tools, config/databases, and
service memory ceilings must be preserved. No proposal may be synthesized for verification.
