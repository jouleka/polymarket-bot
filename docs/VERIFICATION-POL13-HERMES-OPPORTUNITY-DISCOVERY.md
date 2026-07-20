# VERIFICATION — POL-13 Hermes opportunity discovery correction

**Date:** 2026-07-20
**Reviewed implementation heads:** `2f60a99` and follow-up `0e9d87a`
**Result:** live paper/shadow activation PASS

## 1. Live diagnosis

The existing Hermes service and cron were running, but the proposal brain could not perform the
job described by the master design:

- session `cron_ad1c2d9b8c30_20260720_192714` called `get_flags`,
  `get_market(offset=0, limit=10)`, two books, and the ledger, then returned `[SILENT]`;
- that arbitrary condition-ID page contained a Dodgers–Phillies market roughly seven days from
  resolution instead of the market about to close;
- the next scheduled session again began at page zero and honestly emitted no proposal because it
  had no source evidence or citation identifiers; and
- the proposal and economic stores contained no pending proposal, execution, or outbox work.

This was not a signing or Polymarket paper-account problem. Shadow execution is the repository's
internal deterministic projection. The missing capability was a sanitized EventStore read, while
market discovery was ordered by arbitrary condition ID.

## 2. Corrective contract

The existing same-process POL-17 proposal boundary now composes:

- nearest-positive-resolution ordering in `MarketReadView`, with expired zero-second rows last;
- one per-request snapshot of POL-17's shared live-book token inventory;
- bounded `NewsReadView` access through the existing read-only EventStore connection; and
- one additional read-only `get_news` method across facade, RPC, MCP, authored profile, verifier,
  and existing cron prompt.

The inventory is exactly six tools. `propose_trade` is still the only write and its implementation
is unchanged. No signer, wallet, key, order, cancellation, redemption, chain-write, runtime-control,
or live-money authority was added. Production continues to use `PaperSigner` and re-fetches a live
book in deterministic ERS before acceptance and again before shadow execution.

## 3. Strict TDD evidence

Each concern was introduced as an observed RED, minimally implemented, focused GREEN, full-suite
check, and checkpoint commit:

| Concern | Checkpoint |
|---|---:|
| urgent market deadline ordering | `365c44a` |
| shared live-book availability | `03d14d6` |
| bounded recent evidence query | `777b3d3` |
| sanitized news projection | `2b4ba69` |
| optional facade seam | `8a47c4a` |
| bounded proposal RPC | `fe10073` |
| exact-six MCP surface | `f0a4ce0` |
| production composition | `db7f693` |
| existing-profile/verifier/cron contract | `d1bf34b` |
| evidence-to-terminal whole slice | `9bac55c` |
| bounded exact citation verification | `91b3e55` |
| pagination and citation field caps | `9232cc0` |
| SQL-side pre-projection bounds | `2f60a99` |

The whole-slice test covers sanitized evidence discovery, exact citations, proposal insertion, ERS
truth-gating and validation, atomic Maker/Shadow outbox creation, live-book re-fetch rejection,
apply-before-ack execution replay after process restart, resolution provider consensus, target
failure and retry, terminal fanout, final marks, forecast evidence, and position closure.

## 4. Independent review

The first independent specification/security review found four release blockers:

1. truth-gating called `EventStore.all()` over the production-scale store;
2. evidence pagination had an unbounded offset;
3. citation identifiers had no compatible storage/RPC bound; and
4. full content/entities could be allocated before the read view truncated them.

All four were fixed. Closing re-review at exact head `2f60a99` passed with no findings and 85 focused
tests. The implementation now performs bounded SQL projection, enforces offset `<= 1000`, limit
`<= 50`, citation length `<= 2048`, content length `<= 4096`, and fails the entire truth-gate
citation set closed when exact matching exceeds 1,024 rows.

## 5. Adversarial mutation battery

The independent stage-two battery killed 27 distinct mutations with zero survivors. It covered:

- deadline order, zero-first regression, stable ties, and shared live-book inventory sampling;
- source SQL filtering, newest order, tier mismatch, spotlight sanitization, citation/content bounds,
  pagination bounds, and production read-only wiring;
- broad `all()` restoration, exact-match cap versus cap-plus-one, entity substring matching, and
  hidden cross-group collision handling;
- missing/extra seventh RPC/MCP methods, signer or mutation exposure, prompt fabrication, and
  profile inventory drift;
- stale execution books, execution and resolution ACK-before-apply mutations, restart replay, and
  terminal precedence.

The restored tree passed 181 focused tests and was byte-clean with no mutation residue.

## 6. Test result and operational boundary

Canonical suite on tmpfs:

```text
2390 passed in 13.02s
```

At this evidence point no production file, profile, cron, database, service, or deployment had been
changed. The running service therefore still had the old exact-five inventory. Landing, stopped
installation into the existing profile, ordered restart, and live-cycle observation are recorded
only after they occur.

The correction restores the ability to find urgent live markets and cite trusted evidence. It does
not guarantee a proposal: the configured PRIMARY source set remains deliberately narrow, and an
unsupported market must continue to produce an honest no-trade result.

## 7. Stopped installation and production-data preflight

PR #48 landed as merge `007884c`. Both enabled services stopped cleanly with `Result=success`, zero
restarts, and no surviving proposal/runtime process. The historical raw-firehose checksums and all
seven SQLite `quick_check` results passed. The service checkout fast-forwarded without changing the
production config checksum or database inodes, and the idempotent installer kept both units stopped
and disabled.

The existing profile was updated in place: model `gpt-5.6-terra`, provider `openai-codex`, native
root auth, cron ID `ad1c2d9b8c30`, five-minute schedule, and 973 completed runs were preserved.
Exact-six preflight passed, both MCP environments remained pinned to 1.28.1, and no profile-local
auth/environment file appeared.

Before Hermes was started, a direct production RPC probe caught another real availability defect:
offsets 0 through 1,000 returned only `google-news-top` DISCOVERY rows. The high-volume discovery
feed made every PRIMARY citation-eligible row unreachable inside the deliberately bounded scan.
Hermes remained stopped and POL-17 was stopped again after its clean readiness probe (151 live
books, zero raw rows, zero memory pressure).

Follow-up branch `pol-13-hermes-primary-evidence-priority` makes the bounded SQL read prioritize
exact source identities whose pinned allowlist tier is PRIMARY, with newest-first order retained
within the PRIMARY and DISCOVERY classes. It does not trust a persisted row's tier for priority,
promote discovery evidence, expand offsets/limits, or change deterministic truth-gating. Storage,
read-view, and whole-slice tests reproduce the discovery flood.

The follow-up passed independent specification/security review. Its first 10-mutation battery
killed seven and exposed three test-coverage survivors; direct priority-subset and pagination-bound
tests plus a non-vacuous exact discovery tail closed them. Closing re-review killed all 10/10. The
final canonical suite passed 2,394 tests in 11.54 seconds at exact clean head `0e9d87a`.

## 8. Landing and live-cycle verification

PR #49 landed the production-data correction as merge `8c8b105`. The service checkout fast-forwarded
to that exact merge and the stopped idempotent installer again preserved production config checksum
`f42f99379627f441e1363a7976430ef8a81c979cb5382c6a62afa587ab499361`. Exact-six preflight passed.
A stopped direct read against the 1.02 GB production EventStore returned 50/50 citation-eligible
PRIMARY rows in 0.16 seconds at a 25 MiB probe peak.

POL-17 then reached `RUNNING` in two seconds. Before Hermes start, live RPC proof showed:

- 111 shared fresh-book tokens;
- positive-resolution markets in ascending deadline order;
- truthful per-outcome live-book availability; and
- 50/50 citation-eligible records on evidence page zero.

The existing Hermes service started through its exact-six `ExecStartPre`. Natural catch-up session
`cron_ad1c2d9b8c30_20260720_203757` used `get_flags`, `get_market(offset=0, limit=10)`, a shared fresh
`get_book`, `get_news(offset=0, limit=20)`, and `get_ledger`. It selected the live geopolitics market
“Israel x Iran ceasefire continues through July 20?” with midpoint `0.981`. The returned CFTC, Fed,
and BEA PRIMARY items did not bear on that resolution question, so Hermes correctly returned
`[SILENT]` rather than inventing a citation or proposal. The job completed `ok`, advanced the same
cron to completed run 974, cleared its fire claim, and scheduled the next five-minute run.

Both units are active and enabled with `NRestarts=0`. Observed current/peak memory was
78,721,024/80,175,104 bytes for POL-17 and 285,204,480/287,940,608 bytes for Hermes, below unchanged
soft/hard ceilings. Both cgroups had zero swap, pressure-high, max, OOM, and OOM-kill events. The
profile still has no local auth or environment file. Production has zero pending intents,
execution/resolution outbox rows, fills, shadow trades, forecasts, resolution receipts, and raw
`clob-ws` rows; historical raw-firehose checksums pass.

This is a successful runtime correction, not evidence of a trading edge. The remaining cause of
honest no-trade cycles is source coverage: the pinned PRIMARY feeds cover Fed/SEC/CFTC/BEA material,
while urgent markets are frequently sports or geopolitics. Expanding that trust set requires a
separately reviewed source-ingestion contract; Google discovery must not be promoted or fabricated
into citable evidence merely to force activity. YouTrack evidence is comment `7-383`.
