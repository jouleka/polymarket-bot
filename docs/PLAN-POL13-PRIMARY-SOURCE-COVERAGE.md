# PLAN — POL-13 primary-source coverage

**Design:** `docs/DESIGN-POL13-PRIMARY-SOURCE-COVERAGE.md`
**Method:** strict serial RED → minimal GREEN → focused tests → canonical suite → checkpoint

## 1. TDD sequence

1. Add an exact default-allowlist contract test for the four approved identities; observe missing
   entries; add only those `Source` values.
2. Add a bounded recent-feed cache test for literal case-insensitive content filtering, wildcard
   literals, input validation, per-source item/content caps, and pagination; observe the missing
   unit; implement the in-memory cache without touching EventStore persistence.
3. Add a poller test proving a successful source poll atomically replaces its bounded cache snapshot;
   preserve the prior snapshot on a source failure.
4. Add `NewsReadView` query-provider forwarding and validation tests; preserve `query=None` on the
   existing EventStore and fail a query closed when the bounded provider is unavailable.
5. Add RPC acceptance/rejection tests; observe exact-schema rejection; add printable-ASCII bounded
   query validation.
6. Add MCP schema tests; observe schema mismatch; add the optional max-128/min-1 printable-ASCII string.
7. Add profile/prompt contract tests; observe missing market-relevant query guidance; update the
   existing profile prompt without changing tool inventory, model, auth, or schedule.
8. Extend the real whole-slice test with unrelated newer PRIMARY traffic and one relevant official
   item; prove the queried exact citation reaches ERS and survives execution/restart/resolution to
   terminal mark and evidence.

Each step receives its own checkpoint only after focused GREEN and a canonical suite pass. Fixes from
review repeat the same serial RED/GREEN discipline.

## 2. Review and mutation gate

Independent specification/security review checks the design table, additive diff, sacred-surface
hashes, exact-six inventory, cache item/content bounds, source trust groups, and whole-slice
non-vacuity.

The isolated mutation battery must kill, at minimum:

- deletion or tier/group/URL drift for each approved source;
- Google PRIMARY promotion or publisher-group collapse;
- query-filter deletion, case-sensitivity, wildcard interpretation, and filter-after-pagination;
- per-source item/content cap removal, stale partial-snapshot publication, and EventStore fallback;
- query cap/control/non-ASCII/non-string acceptance and `None` regression;
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
