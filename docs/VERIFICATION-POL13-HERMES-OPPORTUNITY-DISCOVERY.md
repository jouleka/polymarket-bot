# VERIFICATION — POL-13 Hermes opportunity discovery correction

**Date:** 2026-07-20  
**Reviewed implementation head:** `2f60a99`  
**Result:** build PASS; installation and live verification pending

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
