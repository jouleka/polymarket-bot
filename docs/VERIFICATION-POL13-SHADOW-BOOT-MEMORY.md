# POL-13 walletless shadow-boot memory correction verification

**Date:** 2026-07-19  
**Candidate:** `7e743a0` on `pol-13-shadow-boot-memory`  
**Runtime state:** both paper/shadow services inactive and disabled

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

## Deployment gate

Install while both services remain stopped. Restore and verify the original production config
checksum. Start POL-17 alone under the unchanged 512 MiB soft / 768 MiB hard / 128 MiB swap limits;
Hermes must remain off until POL-17 reports ready with stable memory. Do not weaken restart,
controller, resolution, or persistence authority to pass the gate.
