# POL-13 registry subset isolation verification

Status: PASS — reviewed correction landed, installed, and restarted under the existing paper/shadow approval

Date: 2026-07-16

## Live observation and cause

The enabled paper/shadow runtime remained fail-closed and within its memory caps, but the extended
observation invalidated the earlier short-window `NRestarts=0` conclusion. The ingestion unit
restarted four times between 17:16 and 18:08 UTC. Three exits were
`MarketSnapshotError: Gamma registry is stale`; one was the existing eight-attempt order-book
resync HALT. Hermes stopped and restarted cleanly through its `PartOf=` relationship. There were no
intent, execution, outbox, signing, wallet, order, cancellation, or chain-write effects and no
memory pressure/OOM event. A fourth registry-stale exit at 18:28 raised the pre-correction total to
five restarts (four registry TTL expiries plus the one resync HALT).

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

## Landed installation and restart evidence

PR [#32](https://github.com/jouleka/polymarket-bot/pull/32) merged as
`1c4d6cbef54fbee37af1e236951a3c42f6cef151`. Hermes stopped cleanly before POL-17;
both units reached `inactive/dead` with `Result=success`. The service checkout then fast-forwarded
from `100bcec` to `1c4d6cb`; its untracked production config/backup, all database files, and raw
evidence were preserved. Installed compilation and config validation passed. The stopped Hermes
preflight, run with its production supplementary group, reported `exact five; PASS`.

POL-17 restarted at 18:41:10 UTC and reached `RUNNING`; Hermes restarted at 18:41:34 only after the
proposal socket/readiness barrier. The first scheduled live Gamma refresh completed at 18:46:13
without error or restart. Closing checks found both services active+enabled with `NRestarts=0` for
the new invocations, zero swap/pressure/OOM events, approximately 126 MiB POL-17 and 261 MiB Hermes
current memory, and unchanged hard caps. Status showed zero pending intents and zero resolution or
execution outbox rows. The latest observed midpoint batch contained 142 books; persistence held
zero raw `clob-ws` rows. All seven databases returned `integrity_check=ok`, every economic,
forecast, component, Maker, Shadow, execution, resolution, terminal, receipt, and outbox table
remained empty, and the native root auth checksum remained
`50df4b431bb07151f2c09b043191e86258dbd4479dc8a48277343c8a744f829b`.

YouTrack POL-13 remains `In Progress`; landed/restart evidence is comment `7-342`. Continued
bounded paper/shadow observation is required. This installation grants no signer, wallet, order,
cancellation, redemption, chain-write, or live-money authority.

The time-based acceptance gate subsequently passed. Scheduled Gamma market/event refreshes at
18:46:13, 18:51:13, and 18:56:14 UTC all returned HTTP 200; the third refresh crossed the previous
900-second deterministic registry-stale boundary while preserving the original 18:41:10 POL-17
invocation. Both units remained active and enabled with `Result=success`, `NRestarts=0`, zero swap,
and zero cgroup pressure/OOM events. At 18:57 UTC POL-17 used 167,358,464 bytes current and
167,890,944 bytes peak memory; Hermes used 266,788,864 bytes current and 269,582,336 bytes peak,
within their unchanged caps.

Runtime status remained `RUNNING` with `registry_error=null`, zero pending intents, zero resolution
or execution outbox rows, and no news failures. All seven databases still returned
`integrity_check=ok`; raw `clob-ws` persistence remained zero; every proposal, economic, forecast,
component, Maker, Shadow, execution, resolution, terminal, receipt, and outbox table remained
empty apart from 17 expected operational-audit rows. The latest midpoint persistence contained
144 books and the full deduplicated trade tape remained active. YouTrack evidence is comment
`7-343`. POL-13 remains `In Progress` for the bounded paper/shadow observation period.
