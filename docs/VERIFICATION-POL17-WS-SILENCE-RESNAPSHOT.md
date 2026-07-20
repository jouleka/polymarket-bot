# POL-17 websocket-silence resnapshot verification

Date: 2026-07-20

## Production finding

The resumed paper runtime stayed active from 2026-07-19 11:09:59 UTC with zero process
restarts, but the durable controller audit recorded a sticky `l5_ws_down` at
2026-07-20 01:08:33 UTC.  The halt correctly ran `cancel_all`; all intent, fill, execution,
resolution, and outbox tables remained empty.  The process, heartbeat, registry, and websocket
collector later remained live, and the propose-only flags endpoint reported a fresh registry and
62 usable books while the controller correctly remained `HALTED`.

The evidence isolated a quiet-but-responsive shard: L5 uses the minimum real bookable frame stamp
across shards, while the venue keepalive returns bare `PONG` frames that deliberately do not grant
book authority.  A shard can therefore cross the immutable 30-second halt boundary without a way
to demand fresh snapshots first.

## Correction

The correction preserves the existing safety meaning.  At 10 seconds of market-frame silence, an
exact venue `PONG` triggers abandonment of that socket and the proven reconnect/resubscribe path.
Books are marked stale before teardown yields.  `PONG` never reaches `MarketStream`, never advances
`last_frame_at`, and never marks a book fresh.  Only real replacement `book` snapshots restore
authority.  An unresponsive or no-real-frame reconnect therefore still reaches the unchanged
sticky `l5_ws_down` halt.  An existing subscribed pre-snapshot delta can advance market health but
cannot restore the stale book; that path remains fail-closed through live-book rejection.

Independent review rejected the initial 20-second threshold because a frame just after PONG could
delay the qualifying PONG until nearly 30 seconds, leaving no snapshot margin.  The corrected
contract uses a 10-second threshold and requires the configured threshold plus ping cadence budget
to be at most 20 seconds, reserving at least 10 seconds before L5 under normal cadence.  Delayed or
missing PONG never refreshes market health and therefore still fails closed at L5.  A production-phase regression proves a real
frame just after PONG survives the next pre-threshold PONG without stale/reconnect, then forces the
following PONG reconnect below 20 seconds.

No RiskCaps hash, anomaly comparison, controller/restart behavior, validator, `evaluate_intent`,
proposal facade, signer, persistence path, raw-websocket policy, wallet, or chain surface changed.
The runtime remains PaperSigner-only.

## TDD and verification

Observed serial REDs:

1. production-shaped silence/replacement test failed because `MarketSocket` lacked the new seam;
2. current-generation grace test caught immediate churn from a retained old frame stamp;
3. NaN/infinity deadline tests caught a disabled-refresh fail-open;
4. bounded warning test caught missing operational evidence;
5. non-callable clock test caught a delayed runtime failure;
6. independent review's worst-phase test failed under the initial 20-second default.

Focused transport/sharding/anomaly/runtime/whole-slice verification passed 63 tests before the
review pins.  The first complete tmpfs suite passed 2,370 tests.  After closing the mutation-test
weakness, warning evidence, eager health-clock validation, and the independent review's cadence
finding, the complete tmpfs suite passes 2,379 tests.

## Adversarial mutation battery

The isolated battery covers five safety mutations:

| Mutation | Named test | Result |
|---|---|---|
| threshold `>=` changed to `>` | responsive-silence boundary | killed |
| `PONG` stamps market health | replacement-generation grace | killed |
| stale revocation removed | stale-before-close observer | initially survived; test corrected; killed |
| silence shares divergence budget | repeated quiet resnapshots at `max_resyncs=1` | killed |
| reconnect `break` changed to `continue` | replacement subscription | killed |
| production default restored to 20s with a 30s combined budget | worst-phase PONG cadence | killed |
| threshold-plus-ping recovery-margin validation removed | construction margin invariant | killed |

The initial stale-revocation survivor was not waived: the test had accidentally relied on the
ordinary second-connection staleness path.  It was changed to a final-budget connection so only the
new silence path can make the book stale, and the mutation then failed with observed `[False]`
instead of `[True]`.  No mutation markers remain in production code.

## Deployment boundary

The already-approved services may be updated only after the branch passes the independent review,
complete suite, GitHub landing, and stopped-install preservation checks.  A controlled ordered
restart is required to run clean walletless reconciliation and clear the existing sticky halt.
That restart grants only paper proposal processing; it grants no signing or live-money authority.
