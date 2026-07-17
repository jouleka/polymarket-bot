# VERIFICATION — POL-13 repeated order-book resync correction

Date: 2026-07-17 UTC

## Incident evidence

The enabled paper/shadow runtime exited through its existing fail-loud eight-attempt order-book
resync gate eight times during the observation window. The final three were at 02:46:02,
12:51:18, and 13:20:33 UTC on 2026-07-17; the last two increased systemd's restart counter from
six to eight. The 12:51 process peaked at 285.7 MB RSS with zero swap. Host and cgroup pressure,
OOM, and OOM-kill counters remained zero, so the exits were not memory exhaustion.

The installed build's terminal exception discarded the shard asset and reconstructed/venue top,
so the historical crashes do not prove one asset or race caused every exit. A code/state-machine
audit did reproduce a deterministic defect consistent with the storm:

1. reconnect retained old diagnostic levels but marked the book stale;
2. a delta could arrive before that asset's replacement snapshot;
3. the dispatcher applied it to the retained stale generation;
4. the resulting mixed-generation top mismatched the venue and forced another reconnect before
   the snapshot could restore authority.

Removing the correction in isolation reproduced the exact terminal shape:
`reconstructed='0.70'/'0.62'` versus `venue='0.70'/'0.72'`, halting on the configured attempt.

## Correction

- `MarketStream` archives but never applies a tracked delta while its book is absent or stale.
  Only a full snapshot clears staleness and restores read/ERS authority.
- Every price-change entry passes atomic semantic validation first: exact finite decimals and a
  known book side. Recovery cannot hide a protocol-format HALT.
- Normal websocket close, resync, disconnect, format HALT, and cancellation revoke the abandoned
  generation synchronously before keepalive/socket teardown can yield. Production normal-close
  reconnects retain bounded backoff and cannot hot-loop.
- Terminal evidence contains an ordered, escaped, bounded history of at most eight divergence
  attempts plus shard asset count. It contains no raw frame, sizes, or levels.
- The non-persisting probe uses the production shard construction with `sink=None` and now holds
  the production singleton lock for discovery and collection.
- The shared lock moved from ephemeral `/run/polybot` to the durable, installer-owned
  `/opt/polymarket-bot/data/shadow-runtime.lock`. The stopped installer rejects a symlink/non-file,
  creates it as `0640 polybot:polybot`, and preserves all databases/configuration.

No signer, wallet, key, order submission/cancellation, redemption, chain write, ERS sizing/pricing,
Hermes proposal, controller, execution-outbox, resolution, or database schema authority changed.
The collector remains read-only and persistence remains midpoint batches plus the deduplicated
trade tape with zero raw `clob-ws` rows.

## Strict TDD and review evidence

Observed REDs included:

- missing bounded divergence detail and terminal attribution;
- missing non-persisting diagnostic constructor;
- stale reconnect delta mutating the retained bid from `0.60` to `0.70`;
- normal-close reconnect applying a raced delta to the prior generation;
- malformed decimal text being accepted during the stale window;
- missing ordered multi-attempt history;
- literal newline injection into terminal diagnostics;
- missing production singleton lock;
- an observer seeing a closed generation as fresh across teardown;
- the ephemeral lock path being unusable while `/run/polybot` is absent.

Final focused collector/deployment verification before the canonical run: 96 passed. Final
canonical command:

```text
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol13-final-review-pytest
```

Result: **2,330 passed in 13.87s**, exit 0. `git diff --check` passed.

The isolated mutation battery killed 10/10 weakenings with zero survivors: stale-book revival,
wrong-asset attribution, retry-threshold relaxation, normal-close readiness loss, semantic-format
bypass, global-HALT suppression, history loss, diagnostic bound removal, singleton-lock removal,
and a non-`None` diagnostic sink. The teardown-yield and durable-lock-location defects were each
also introduced first as real failing regressions and observed RED before their fixes.

Independent specification and security reviews each found substantive issues during development:
normal-close readiness, stale-window semantic validation, insufficient attempt history,
single-collector enforcement, log injection/bounds, combined sibling/global recovery coverage,
staleness after a teardown yield, and the stopped-state `/run` lock lifecycle. Every finding was
fixed serially and re-reviewed against the stable candidate.

Final re-review of stable code commit `2c7db2b` passed with no findings. The specification reviewer
ran 113 focused collector/runtime/deployment tests; the security reviewer ran 105. Both confirmed
the synchronous stale transition, durable real lock lifecycle, snapshot-before-authority,
fail-loud format/retry behavior, zero persistence expansion, and unchanged no-signing boundary.

## Bounded live diagnostic

Hermes was stopped first and POL-17 second. Both reported `inactive/dead`, `Result=success`, and
`MainPID=0` before the probe. The probe then ran the exact production discovery and shard sizing:

```text
tokens=200 shards=8 seconds=1800 persistence=none
```

It reached the 1,800-second bound without a terminal resync storm. RSS was approximately 62 MB at
startup and 85 MB near completion. No production database was opened or modified by the probe.
This negative observation does not attribute the historical crashes; it only shows no natural
storm occurred in that bounded window.

The completed probe predated the singleton-lock review fix. Single-collector safety was instead
established operationally by the ordered service stop and zero remaining service PIDs. The landed
candidate additionally enforces exclusion with the shared real `flock`; a real-filesystem test
proves a second runtime is rejected while the probe owns it and succeeds after release.

## Handoff state

At the end of build verification, both production units remain stopped. They were not disabled by
this correction, and no service checkout update, installer run, database migration, production
data rewrite, start, or restart occurred during code verification. Installation must precreate the
durable lock while stopped, then preserve the existing configuration, seven databases, historical
raw-firehose evidence, native Hermes profile/auth, exact-five tool grant, cron, memory ceilings,
and paper-only authority before restarting POL-17 and then Hermes.
