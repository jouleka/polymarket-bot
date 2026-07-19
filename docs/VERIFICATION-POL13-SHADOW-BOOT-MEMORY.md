# POL-13 walletless shadow-boot memory correction verification

**Date:** 2026-07-19  
**Reviewed head:** `7e743a0`; landed through [PR #44](https://github.com/jouleka/polymarket-bot/pull/44)
as merge `cb8ff79`
**Runtime state:** installed; both paper/shadow services active and enabled

## Root cause

POL-17 repeatedly reached roughly 585–601 MiB cgroup memory plus its capped 128 MiB swap before
readiness, entered `mem_cgroup_handle_over_high`, and had to be stopped. Hermes never started.

Bounded isolation established:

- production component construction: about 75 MiB maximum RSS;
- collector alone with 100 live books / 10,944 price levels: about 64 MiB maximum RSS;
- each ingestion/news/proposal service alone: about 63 MiB maximum RSS;
- complete runtime over fresh temporary databases: ready in under 10 seconds at about 79 MiB
  maximum RSS;
- production process memory: about 460 MiB private anonymous heap plus 128 MiB swap.

The remaining production-only difference was restart reconciliation. `RestartReconciler` called
`ReadOnlyEventStore.all()` before checking `wallet=None`, materializing the entire 685 MiB historical
Market-Memory database to build CLOB/on-chain legs that can have no authority in pure paper mode.
`ThreeWayReconciler` then returned DORMANT because no wallet/chain truth exists, discarding those
materialized legs. The per-cycle reconciler already had the correct walletless no-scan fast path;
boot did not.

## Correction and safety

Walletless paper boot still replays the durable internal fills with `in_session=False`, rebuilds the
portfolio from accepted intents, and follows the existing DORMANT transition. It now supplies the
reconciler with empty CLOB state and the `None` on-chain sentinel without scanning EventStore.

When any real wallet is configured, behavior is byte-for-byte unchanged: EventStore is scanned once,
both external legs are built, divergence remains HALTED, and no settle grace is granted to replayed
fills. This ticket remains paper-only and adds no wallet, signer, order, or live-money path.

## TDD, review, and mutation evidence

The new raising-store regression first failed at `event_store.all()` and then passed after the
minimal branch. Focused restart/reconcile/anomaly/harness coverage passed `54/54`.

```text
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol13-shadow-boot-pytest-1
2365 passed in 10.63s
```

- independent specification review: **PASS**; DORMANT equivalence, durable internal replay,
  portfolio reconstruction, and unchanged wallet-present authority verified;
- independent security/safety review: **PASS**; only exact `wallet is None` bypasses external
  history, while malformed/empty/real wallet inputs retain the fail-closed full branch;
- removing the walletless short circuit is killed by
  `test_dormant_no_wallet_never_scans_historical_event_store`;
- incorrectly applying the shortcut to a configured wallet is killed by
  `test_injected_onchain_divergence_stays_halted`;
- zero mutation survived.

## Installed/live gate

The service checkout was fast-forwarded to merge `cb8ff79` while stopped. The installer preserved
the original production config checksum
`f42f99379627f441e1363a7976430ef8a81c979cb5382c6a62afa587ab499361` and all database/WAL metadata.
With the original universe restored and unchanged cgroup limits:

- POL-17 reached `active/running` in about three seconds;
- startup peak was about 84 MiB instead of 601 MiB, with zero swap and zero `memory.events` pressure;
- controller reported RUNNING with zero pending intents/resolution outbox/execution outbox and no
  registry/news error;
- proposal socket ownership remained `polybot:polybot-proposal 0660`;
- Hermes was started only after POL-17 readiness and settled around 257 MiB, with POL-17 around
  90 MiB; both had zero restarts, swap, pressure, or OOM events;
- the first genuine scheduled Hermes job completed `ok` at `2026-07-19 11:11:04 UTC`; runtime
  status remained controller RUNNING with zero pending intents or outboxes;
- both services were then enabled; the auth-writer socket is active/static with no idle instance;
- persistence remains downsampled: no `clob-ws` source rows, with midpoint batches and the
  deduplicated Data API tape present.

Do not weaken restart, controller, resolution, persistence, or cgroup authority on future starts.
