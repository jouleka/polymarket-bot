# POL-13 websocket receive-buffer memory bound verification

**Date:** 2026-07-19  
**Candidate:** `9e51c55` on `pol-13-ws-buffer-memory-bound`  
**Runtime state:** both paper/shadow services inactive and disabled

## Trigger

After the reviewed Codex auth-isolation correction was installed while stopped, the ordered POL-17
restart failed closed during readiness. The process reached about 600 MiB cgroup memory, consumed
its capped 128 MiB swap, entered `mem_cgroup_handle_over_high`, and exceeded the 60-second startup
deadline. Systemd terminated it; Hermes was deliberately never started.

Evidence at failure:

- `MemoryCurrent`: approximately 600 MiB;
- `MemoryPeak`: approximately 601 MiB;
- process state: uninterruptible memory reclaim in `mem_cgroup_handle_over_high`;
- `memory.events`: `high` increased, with `max=0`, `oom=0`, and `oom_kill=0`;
- systemd result: startup `timeout`, zero restarts;
- configuration, databases, WAL/SHM, raw evidence, and auth stores remained preserved.

An isolated production-construction probe completed at about 75 MiB maximum RSS, locating the
growth after the 16 live websocket shards start rather than in registry/component construction.

## Root cause and correction

The real transport used `websockets.connect(..., ping_interval=None)` without an explicit receive
queue bound. `websockets` therefore applied its default high-water mark independently to every
shard, multiplying buffered-frame memory across the 16-connection production fan-out during host
contention.

The transport now passes `max_queue=1`. In the pinned websockets 16 implementation this sets a
per-connection receive-frame high-water mark of one and applies transport/TCP backpressure; it does
not silently drop or reorder frames. Existing disconnect handling marks the affected shard stale,
and existing top-of-book divergence detection forces reconnect/resync if progress ever diverges.

No universe, persistence, safety/controller, ERS, proposal, signer, auth, database, or live-money
authority changed.

## TDD and review evidence

The focused test first failed because the live connector supplied only `ping_interval=None`; after
the minimal change it proved the exact `max_queue=1` call. Focused transport, market-socket,
sharding, ingestion-runtime, and shared-ingestion tests passed `47/47`.

The canonical command passed after clearing only accumulated `/dev/shm/pol13-*` pytest basetemps:

```text
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol13-ws-memory-pytest-2
2364 passed in 14.96s
```

The first attempt's 37 SQLite failures were solely `database or disk is full` after old project
basetemps filled tmpfs; they disappeared unchanged after cleanup.

- independent specification review: **PASS**; websockets 16.1 compatibility, receive
  backpressure, fragmented-message progress, and unchanged fail-closed resync semantics verified;
- independent security/resource review: **PASS**; all 16 shards use the bounded connector,
  saturation applies TCP backpressure without deliberate frame loss, and no persistence/book/
  trading authority changed;
- mutation: changing `max_queue` from `1` back to `16` was killed by
  `test_live_ws_bounds_each_shards_received_frame_queue`; zero survivor.
- live connector smoke: websockets 16.1 returned a real `ClientConnection` with the bounded call.

## Deployment boundary

Code publication, stopped installation, and ordered restart are recorded separately. The restart
must retain the existing 512 MiB soft / 768 MiB hard POL-17 cgroup ceilings; do not raise them to
make the service pass. Hermes stays off until POL-17 reaches readiness and its memory settles.
