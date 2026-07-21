# VERIFICATION — POL-13 evidence-aligned market selection

**Date:** 2026-07-21
**Implementation commits:** `1432bcf`, `babd676`, `0f168cb`, `4cf5c14`, `a3575d1`, `3c6157a`
**Merge commits:** PR #53 `86fcf95`; PR #54 `ed0d964`
**Result:** PASS; landed, installed, and live-verified in paper/shadow

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

This evidence authorizes no live-money action. The existing profile, model/provider inheritance,
cron ID/schedule/history, exact-six tools, config/databases, and service memory ceilings were
preserved through stopped-safe service-checkout installation and the native in-place cron prompt
update. No proposal was synthesized for verification.

## 6. First activation and follow-up correction

PR #53 merged as `86fcf95a64838434f0c6f7232fb2619cf32f7332`. Stopped-first installation
preserved the production config/profile/SOUL checksums, all seven database inodes and `quick_check`
results, historical raw-firehose checksums, cron ID and every non-prompt field, and exact-six
preflight. Both services restarted active+enabled with zero restarts/swap/pressure/OOM.

Natural run 1005 used the new 20-market page, skipped sports, selected the live-book crypto market
“Will the price of Bitcoin be above $68,000 on July 21?”, read the fresh 0.023/0.026 book, empty
crypto ledger, and `get_news(query="Bitcoin", limit=20)`, then returned `[SILENT]`. Run 1006 repeated
the same market/query and again returned no evidence and `[SILENT]`. This proved the sports mismatch
closed but exposed a repeated empty-evidence candidate loop.

Follow-up commit `a3575d1` replaces choose-then-query with the fixed-resource shortlist described in
design section 6. Its prompt-contract test observed RED before implementation. Independent review
then found that shortlist membership itself did not require an advertised live outcome; that clause
received a second observed RED and is SHA-pinned with the complete prompt. Focused tests pass and the
pre-review canonical checkpoint reports:

```text
2415 passed in 43.36s
```

Closing independent review at exact follow-up head
`3c6157a383274e882755a2430f65c2ecf32cfddd` passed with no new findings; the focused profile suite
reported 42 passed. An isolated exact-head prompt battery killed 18/18 mutations with zero survivors:
shortlist/call/cardinality bounds, limits 10 -> 20/50, priority/category/evidence/order changes,
multiple books/ledgers/proposals, second-page reads, extra tools, contradictory nearest-Bitcoin text,
arbitrary edits, and removal/movement/negation of shortlist live-book membership. The tmpfs worktree
was restored and removed.

## 7. Landing, installation, and natural-cycle evidence

PR #54 merged as `ed0d964`. The service checkout fast-forwarded to that merge while preserving its
untracked production config and pre-POL-17 evidence. Because the follow-up changed only the existing
cron prompt, documentation, and tests, POL-17 remained running and Hermes alone received an ordered
stop/update/start. The native cron update changed only `prompt`: ID `ad1c2d9b8c30`, five-minute
schedule, history, completed-run count, model/provider inheritance, and all other fields were
preserved. The installed no-trailing-newline prompt SHA-256 is
`dd5ffe6d6585c4856c8e86296c1351d01f86b34497af4e1c0e7bfaf1f0533ca5`; the repository file including
its terminal newline is `9a39dab9e57d6dd026cf371c3b1198ec3a80c2921d9eadc97addd52e8e74e52c`.
Exact-six profile preflight passed before restart.

Natural run 1009, session `cron_ad1c2d9b8c30_20260721_090740`, demonstrated the intended positive
discovery path without manufacturing an execution. It read flags and one 20-market page, prioritized
geopolitics, queried `Iran` with limit 10, then read exactly one fresh book and its matching empty
ledger. It proposed outcome `No` on “US x Iran Effective Ceasefire by July 24?” at the live 0.91 ask,
with paper size suggestion 10.00, `p=0.93`, confidence 0.64, and two eligible UN citations. The
proposal RPC accepted intent `cycle-707496-no-20260721`.

ERS then independently re-fetched and evaluated the live book and correctly recorded `SKIP/no_edge`.
Both citations belonged to publisher group `un.org`, so corroboration remained false,
`w_news_effective=0`, and the exact fused forecast anchored to the 0.905 market midpoint. That was
below the 0.91 execution ask. The result is one forecast and component-evidence row, one terminal
skipped intent/audit row, and zero fills, execution outbox rows, executions, maker fills, or shadow
trades. The truth gate, fresh-book authority, and sizing/execution boundaries were not weakened to
force a paper fill.

Natural run 1010, session `cron_ad1c2d9b8c30_20260721_091339`, exercised the negative path. It read
flags and the single 20-market page, then used both permitted evidence calls (`ceasefire`, `Bitcoin`,
each limit 10). The first returned only same-publisher eligible UN records plus ineligible discovery
material; the second returned no records. It returned `[SILENT]` without a book, ledger, or proposal.
The job completed `ok`, advanced to completed count 1010, and scheduled its next five-minute run.

At the closing check both units were active+enabled and `NRestarts=0`. POL-17 current/peak memory was
123,850,752/132,231,168 bytes; Hermes was 260,648,960/263,049,216 bytes. Both cgroups used zero swap
under unchanged 768/512 MiB maximums and 128 MiB swap ceilings; the host reported about 4.4 GiB
available. Runtime status was `RUNNING` with zero pending intents and both outboxes empty. All seven
production databases returned `quick_check=ok` with their pre-install inodes preserved. Persistence
contained 6,011 periodic `clob-midpoint` batches, the full 611,000-row deduplicated `data-api` tape,
and exactly zero raw `clob-ws` rows. There were no resolution terminals, integrity halts, signing
operations, or live-money actions.

This closes the evidence-selection defect. It does not claim a profitable edge: future shadow fills
must arise only from independently corroborated evidence and a genuine executable-price advantage.
