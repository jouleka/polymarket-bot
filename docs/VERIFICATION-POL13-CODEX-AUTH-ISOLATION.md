# POL-13 Codex OAuth auth-store isolation verification

**Date:** 2026-07-19  
**Candidate:** `bf9e317` on `pol-13-codex-auth-isolation`  
**Deployment state:** reviewed build only; both shadow services remain inactive and disabled

## Incident evidence

During the prior shadow observation, Hermes 0.18.2 created the forbidden profile-local
`/root/.hermes/profiles/polymarket/auth.json`. Both services were stopped and disabled immediately.
No credential value was printed, copied, or used as a fallback.

Stopped evidence recorded on 2026-07-19:

- forbidden artifact: regular file, `root:root`, mode `0600`, 4,790 bytes, born/modified
  `2026-07-18 13:50:31 UTC`, SHA-256
  `c8dcedf6baff26f92c89af6aca00d460372e95805b8a84fb1855bf12ea55f020`;
- native root store: regular file, `root:root`, mode `0600`; it remains authoritative and was not
  overwritten from the profile;
- `polymarket-ingestion.service`: inactive/disabled;
- `polymarket-hermes.service`: inactive/disabled;
- auth-writer socket: not installed in the service checkout and inactive.

The known forbidden artifact remains preserved pending reviewed installation and the exact guarded
cleanup in `deploy/hermes/README.md`.

## Implemented boundary

- Bootstrap pins Hermes's active auth resolver to the existing native root store before other
  Hermes runtime imports can cache auth behavior.
- The LLM-bearing unit retains read-only access to `/root/.hermes` and cannot directly save root
  auth state.
- A root-only socket-activated one-shot writer performs Hermes's native atomic save to the single
  fixed root target. It has no model, tools, network, wallet, signer, or trading surface and no idle
  process memory.
- Requests are bounded to 1 MiB, require a root Unix peer, preserve every non-Codex provider/pool
  field, and permit changes only to `openai-codex` provider/pool state.
- The caller transfers its actual held native `auth.lock` open-file description with `SCM_RIGHTS`.
  The writer rejects missing, wrong-path, unlocked, and independently opened decoy descriptors and
  retains a valid lease through native save and response.
- Accepted writer processes are bound to the Hermes lifecycle, limited to one concurrent
  connection, capped at 128 MiB with zero swap, terminated after 20 seconds, and collected after
  exit/failure.

No profile/model/cron/tool grant, proposal facade, ERS, signer protocol, controller, execution,
resolution, database, or production-data surface changed.

## TDD and regression evidence

Observed REDs included:

1. native Hermes pool persistence created profile `auth.json`;
2. missing root auth did not fail with the pinned contract;
3. file-only systemd write access could not support native sibling-temp/rename;
4. direct shared-root write access violated confinement;
5. the first writer protocol lacked target/peer/content/runtime bounds;
6. a request without a transferred native lock was accepted;
7. an independently opened decoy descriptor passed while another descriptor owned the flock; and
8. a writer releasing the transferred lease before save survived until the synchronized regression
   was added.

Focused closing gate:

```text
tests/test_pol18_auth_writer.py
tests/test_pol18_profile.py
tests/test_pol18_deploy.py
tests/test_deploy_contract.py
68 passed
```

Systemd gate:

```text
systemd-analyze verify deploy/polymarket-hermes.service \
  deploy/polymarket-hermes-auth-writer.socket \
  deploy/polymarket-hermes-auth-writer@.service
PASS (no diagnostics)
```

Canonical repository gate:

```text
TMPDIR=/dev/shm ./.venv/bin/pytest -o addopts="" -q \
  --basetemp=/dev/shm/pol13-auth-pytest-19
2363 passed in 9.12s
```

No skips or xfails were added.

## Independent review

- Closing specification review gave exact head `bf9e317` **PASS** after 56 focused tests and clean
  systemd verification, including the complete auth-isolation and no-authority-expansion contract.
- Security review found and blocked two real caller-death serialization flaws: lack of lock-lease
  transfer, then acceptance of an unlocked decoy descriptor. Both were fixed with deterministic
  regressions. Exact head `bf9e317` received final security **PASS**.
- A final test-rigor review caught a race in the first caller-death regression. Two-event
  synchronization now proves that the original caller descriptor is closed before the save callback
  probes the transferred lease. Ten repeated focused runs passed and the early-release mutation was
  deterministically killed.

## Isolated adversarial mutations

Every mutation was applied alone, its named test was observed failing, and the mutation was removed
before the next case.

| Mutation | Killing evidence |
|---|---|
| Remove native root resolver redirect | `test_gateway_auth_guard_delegates_native_saves_to_isolated_writer` |
| Remove delegated native save | `test_gateway_auth_guard_delegates_native_saves_to_isolated_writer` |
| Give Hermes direct root-auth write access | `test_brain_unit_uses_existing_root_hermes_profile_and_does_not_activate_pol17` |
| Remove fixed target rejection | `test_auth_writer_client_rejects_unreviewed_requests[target]` |
| Remove non-Codex projection comparison | `test_auth_writer_preserves_every_non_codex_root_credential` |
| Restore a response timeout after connect | `test_brain_unit_delegates_atomic_auth_without_shared_root_write_access` |
| Remove writer runtime ceiling | `test_brain_unit_delegates_atomic_auth_without_shared_root_write_access` |
| Accept a non-root peer | `test_auth_writer_server_rejects_non_root_peer_before_read_or_save` |
| Accept an unlocked decoy descriptor | `test_lock_lease_rejects_unlocked_decoy_for_locked_native_path` |
| Release transferred lease before save | `test_server_holds_transferred_lock_while_saving_after_caller_death` |
| Remove profile-secret artifact guard | `test_gateway_auth_guard_rejects_profile_local_secret_artifacts` |
| Increase concurrent accepted writers | `test_brain_unit_delegates_atomic_auth_without_shared_root_write_access` |

The earlier no-descriptor protocol mutation is also pinned by
`test_auth_writer_rejects_request_without_transferred_native_lock`. Zero mutation survived.

## Remaining deployment gates

Publication/merge, stopped installation, guarded incident cleanup, stopped exact-five/native-save
preflight, and shadow restart are separately recorded actions. This verification does not itself
authorize production data migration or any live-money capability. The user has authorized continuing
the existing paper/shadow workflow; services remain stopped until the reviewed code is landed and
the stopped host gate passes.
