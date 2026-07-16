# POL-13 registry subset isolation verification

Status: corrective build and closing review gates pass; owner-approved landing/install/restart pending

Date: 2026-07-16

## Live observation and cause

The enabled paper/shadow runtime remained fail-closed and within its memory caps, but the extended
observation invalidated the earlier short-window `NRestarts=0` conclusion. The ingestion unit
restarted four times between 17:16 and 18:08 UTC. Three exits were
`MarketSnapshotError: Gamma registry is stale`; one was the existing eight-attempt order-book
resync HALT. Hermes stopped and restarted cleanly through its `PartOf=` relationship. There were no
intent, execution, outbox, signing, wallet, order, cancellation, or chain-write effects and no
memory pressure/OOM event.

The registry restarts were deterministic. Production uses a 300-second refresh cadence and a
900-second maximum age. Gamma can permanently omit a requested active condition from a filtered
fixed-universe request. The previous correction treated every strict subset as unavailable and
never renewed the registry age, so any such universe necessarily expired after 15 minutes and
systemd rediscovered it. A bounded live replay also showed that a newly selected 100-market
generation can return complete 100/100 refreshes; the failure depends on which fixed conditions
Gamma later omits, not on HTTP availability or a global schema change.

## Corrective authority model

The websocket subscription identity remains frozen at startup and cannot expand. A refresh may
publish a coherent strict-subset `MarketRegistry` only when every returned market identity matches
the frozen mapping and every returned event relationship for any frozen condition also matches.
The new generation contains only currently usable metadata. Omitted and metadata-quarantined
conditions therefore receive no ERS metadata, no `get_market` row, no advertised
`live_book_tokens`, and no `get_book` result. Their extra collector subscriptions carry no runtime
or proposal authority. Exact frozen identities may reappear in a later generation.

Expansion, replacement, malformed identity, current or omitted event-token contradiction, and a
replacement with zero usable categorized markets remain fatal without replacing prior authority.
Transport/server failures still retain the last coherent generation without renewing its TTL, and
that generation still becomes fatal after the configured maximum age. Resolution polling for
already durable unresolved forecasts remains independent of current proposal eligibility, so
terminal fanout and precedence are unchanged.

## Strict TDD evidence

The implementation was built as serial RED/GREEN checkpoints:

- `5a22e8b` publishes only coherent subset metadata and intersects Hermes-advertised live books
  with the current usable registry;
- `d22837a` pins exact-identity reappearance without shrinking the frozen universe;
- `424d6a1` closes the first independent-review findings by validating omitted frozen event
  identities and gating `get_book` on current-registry authority;
- `a297588` closes re-review's publication-race finding by deriving flags and book authorization
  from the same returned immutable registry generation, with no separately published token cache.

Each intended RED was observed before the minimum implementation. The closing focused run passes
253 registry, MarketRegistry, cycle, root, whole-slice, Hermes read/RPC, and resolution-adjacent
tests. The canonical tmpfs suite passes **2,311 tests**. `git diff --check` passes.

## Independent review and adversarial mutation

The first independent specification/security reviews rejected `5a22e8b` for two real gaps:

1. event relationships for omitted frozen conditions were compared only with the returned market
   subset, allowing a hidden token contradiction to renew subset TTL;
2. `get_book` still admitted remembered frozen-universe tokens after flags and `get_market` had
   removed current authority.

Both findings now have direct regressions, including omitted, metadata-quarantined, and restored
conditions. Re-review then found a concurrent publication gap between the immutable registry and a
separate token cache; `a297588` removes that cache from authority decisions and pins the exact
interleaving. Independent specification and security closing re-reviews both pass exact candidate
`980586a` with no findings; reviewers independently ran 277 and 136 focused tests, and the
specification reviewer reran the complete 2,311-test tmpfs suite.

An isolated disposable-worktree mutation battery at exact candidate `980586a` killed 10/10
changes: admit universe expansion;
skip returned identity comparison; advertise frozen instead of current tokens; advertise collector
books outside current registry authority; fail to renew a coherent subset's own TTL; shrink frozen
identity so restoration fails; include quarantined metadata tokens; and publish a zero-usable
replacement; validate event relationships only against the returned subset; and authorize
`get_book` from a separately published provider token view. Zero survivors remain.

## Deployment boundary

No code from this branch has been pushed, merged, installed, or loaded by systemd. The development
changes do not touch `/opt/polymarket-bot`, any production database, the raw-firehose evidence,
the native Hermes profile/authentication, or either unit's memory limits. Landing, stopped
installation, and a controlled service restart are separate operational steps. Activation already
exists under the owner's earlier approval, but replacing a running executable still requires the
normal reviewed install/restart gate.
