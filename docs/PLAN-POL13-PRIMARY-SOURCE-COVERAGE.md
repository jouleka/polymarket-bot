# PLAN — POL-13 primary-source coverage

**Design:** `docs/DESIGN-POL13-PRIMARY-SOURCE-COVERAGE.md`  
**Method:** strict serial RED → minimal GREEN → focused tests → canonical suite → checkpoint

## 1. TDD sequence

1. Add an exact default-allowlist contract test for the four approved identities; observe missing
   entries; add only those `Source` values.
2. Add storage tests for the optional literal case-insensitive content filter, wildcard literals,
   invalid input, and `None` compatibility; observe the missing keyword; implement parameterized SQL
   before ordering and pagination for writable and read-only stores.
3. Add `NewsReadView` forwarding and validation tests; observe missing `query`; add the optional seam.
4. Add RPC acceptance/rejection tests; observe exact-schema rejection; add bounded query validation.
5. Add MCP schema tests; observe schema mismatch; add the optional max-128/min-1 string only.
6. Add profile/prompt contract tests; observe missing market-relevant query guidance; update the
   existing profile prompt without changing tool inventory, model, auth, or schedule.
7. Extend the real whole-slice test with unrelated newer PRIMARY traffic and one relevant official
   item; prove the queried exact citation reaches ERS and survives execution/restart/resolution to
   terminal mark and evidence.

Each step receives its own checkpoint only after focused GREEN and a canonical suite pass. Fixes from
review repeat the same serial RED/GREEN discipline.

## 2. Review and mutation gate

Independent specification/security review checks the design table, additive diff, sacred-surface
hashes, exact-six inventory, SQL parameterization/bounds, source trust groups, and whole-slice
non-vacuity.

The isolated mutation battery must kill, at minimum:

- deletion or tier/group/URL drift for each approved source;
- Google PRIMARY promotion or publisher-group collapse;
- query-filter deletion, case-sensitivity, wildcard interpretation, and filter-after-pagination;
- query cap/control/non-string acceptance and `None` regression;
- priority-order regression or broad `all()` materialization;
- RPC/MCP query omission or a seventh authority-bearing tool;
- prompt omission, citation fabrication, or forced-proposal wording; and
- whole-slice evidence that bypasses the relevant queried official citation.

After every fix, repeat independent review, mutation checks, focused tests, canonical suite, mutation
residue scan, and clean-tree check.

## 3. Landing and stopped-first production verification

1. Update verification, HANDOFF, and TICKETS evidence.
2. Push the reviewed branch, open a PR, verify checks, and merge with an explicit verification record.
3. Wait for the existing Hermes cron to be idle; record service, database, raw-firehose, profile,
   checkout, and memory evidence.
4. Stop Hermes then POL-17; fast-forward `/opt/polymarket-bot`; run the idempotent installer and
   exact-six preflight while stopped.
5. Update only the existing cron prompt in place, preserving cron ID, schedule, run count, model,
   provider, and auth.
6. Start POL-17; verify readiness, source poll isolation, query results, database integrity, zero raw
   CLOB rows, and memory ceilings. Then start Hermes and observe natural cycles.
7. Record honest outcomes. No proposal is fabricated; no real-money action is authorized.
8. Post the final YouTrack evidence and leave both services in their previously approved enabled/running
   state only if all checks pass; otherwise stop fail-closed and report the exact blocker.
