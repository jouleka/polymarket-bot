# PLAN — POL-13 evidence-aligned market selection

**Design:** `docs/DESIGN-POL13-EVIDENCE-ALIGNED-MARKET-SELECTION.md`

1. Record the live category distribution and confirm supported markets already exist in the current
   bounded registry page.
2. Add one focused prompt-contract test and observe the correct RED for the missing sports/category
   guard.
3. Make the minimum prompt change: bounded limit 20, sports exclusion, reviewed category set, and
   genuine evidence-relevance requirement.
4. Run focused GREEN, the canonical suite, and checkpoint the TDD cycle.
5. Run independent specification/security review and an isolated adversarial mutation battery over
   selection, bounds, live-book, citations, synthetic activity, and exact-six authority.
6. Close the review finding that prompt policy alone is not a deterministic boundary: add an
   optional backwards-compatible `HermesPipeline` evidence-category set, wire the exact reviewed set
   in production, and reject unsupported trusted categories before fusion or any evidence write.
7. Close every prompt/runtime survivor, re-review, and rerun the canonical suite.
8. Record verification evidence, update HANDOFF/TICKETS and POL-13, then publish through a reviewed
   PR.
9. Only after the repository lands, update the existing cron's prompt through its native API while
   preserving every non-prompt field. Observe natural cycles; do not force a proposal.
