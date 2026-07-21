# DESIGN — POL-13 evidence-aligned market selection

**Date:** 2026-07-21 · **Ticket:** POL-13 shadow deployment · **Status:** implemented; independent review PASS

## 1. Problem

The exact-six paper/shadow runtime is healthy, but natural Hermes runs 998 and 999 selected
near-deadline esports markets and then correctly returned `[SILENT]`. The reviewed PRIMARY source
set covers politics, geopolitics, crypto, finance, and economics; it does not cover sports.

This is a selection-policy mismatch, not a missing paper account, execution failure, or reason to
weaken the citation gate. A live bounded read on 2026-07-21 returned 99 current registry markets:
49 sports, 19 politics, 17 geopolitics, 10 crypto, three finance, and one weather. The first twenty
already contained live-book politics, crypto, and geopolitics candidates, so another collector or
market API is unnecessary.

## 2. Decision

Use two layers:

- widen the already bounded sanitized `get_market` page from 10 to 20 rows;
- explicitly refuse sports markets;
- consider only politics, geopolitics, crypto, finance, or econ;
- require the configured evidence sources to genuinely bear on the exact selected question; and
- remain silent when no supported live-book market exists; then
- have the production `HermesPipeline` independently reject any trusted registry category outside
  the exact frozen set `{politics, geopolitics, crypto, finance, econ}`.

The ordered flow remains flags -> bounded market page -> one live book -> resolved history -> one
literal bounded news query -> at most one proposal. Fresh-book, citation eligibility, truth-gate,
Decimal, ERS, controller, outbox, settlement, and PaperSigner behavior do not change.

The deterministic gate runs only after the existing condition/token/event/category identity checks
and before fusion, component logging, forecast persistence, sizing, execution, or signing. It returns
`REJECT(evidence_category_unsupported)`. The new `HermesPipeline.evidence_categories=None` default
preserves existing optional-seam behavior; the production composition root explicitly wires the
frozen reviewed set and a root test pins that wiring.

## 3. Safety and resource boundaries

This correction adds no tool, source, network client, process, database, schema, authority, or model
installation. Exact six remains exact six. Hermes remains propose-only and cannot size,
price, sign, submit, cancel, redeem, or operate the runtime. Sports is excluded rather than silently
promoting an aggregator or treating a market resolution URL as predictive evidence.

The maximum market result remains below the existing RPC/MCP cap of 50. Twenty sanitized rows are
transient model context only, so no service memory ceiling or persistent storage footprint changes.

## 4. Failure policy

- Runtime/registry not ready or no live-book tokens: stop without proposing.
- No supported category in the first twenty rows: stop without proposing.
- A proposal for an unsupported trusted registry category: deterministic ERS rejection before any
  forecast/component/shadow write.
- No genuinely relevant citation-eligible evidence: stop without proposing.
- Ambiguous, stale, contradictory, or insufficient evidence: stop without proposing.
- ERS rejection: preserve the deterministic rejection; never retry blindly or synthesize activity.

## 5. Acceptance criteria

1. The deployed prompt reads exactly one bounded page of twenty current markets and excludes sports.
2. Only politics, geopolitics, crypto, finance, or econ may be selected or pass the production
   evidence-category gate.
3. Evidence relevance, fresh-book, eligible-citation, at-most-one, and no-synthetic-proposal rules
   remain explicit.
4. Exact-six discovery and propose-only authority are unchanged.
5. Focused and canonical suites pass, independent review passes, and adversarial prompt/runtime
   mutations are killed.
6. Deployment, cron mutation, and live observation remain separate stopped-safe operational gates.

## 6. Live follow-up: evidence-bearing candidate priority

The first deployment proved the category correction but exposed one narrower liveness loop. Natural
runs 1005 and 1006 both skipped sports, then selected the same live-book crypto question, “Bitcoin
above $68,000 on July 21,” queried literal `Bitcoin`, received zero evidence, and returned
`[SILENT]`. The nearest supported category was still winning before evidence availability was known.

The prompt therefore uses one fixed-resource evidence-first shortlist:

- the same one-page 20-market input;
- at most two non-sports candidates that already advertise a `live_book=true` outcome, with
  geopolitics first because the reviewed current UN, White House, War/Defense, and IAEA publishers
  directly cover it;
- at most two distinct literal `get_news` calls with limit 10 each, preserving the previous maximum
  of 20 returned evidence items;
- only after relevant citation-eligible evidence is found, one fresh live book and one matching
  ledger read; and
- still at most one proposal, otherwise silence.

This does not bypass the deterministic category gate. It reduces repeated empty candidate work while
keeping model context, service memory, collectors, persistence, tools, and authority bounded.
