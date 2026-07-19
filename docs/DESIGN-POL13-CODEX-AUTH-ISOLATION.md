# POL-13 Codex OAuth auth-store isolation correction

**Date:** 2026-07-19 · **Ticket:** POL-13 shadow deployment · **Status:** owner-approved

## 1. Incident and scope

The stopped preflight requires the `polymarket` Hermes profile to contain no `.env`, `.op.env`, or
`auth.json`; the profile borrows the already-authenticated native root store at
`/root/.hermes/auth.json`. During the live shadow observation, Hermes 0.18.2 refreshed the selected
`openai-codex` OAuth credential and created
`/root/.hermes/profiles/polymarket/auth.json`. The services were stopped and disabled immediately.

Hermes's credential-pool runtime reads a missing profile pool from the global fallback, but every
pool persistence writes the active profile. A refresh therefore persists the borrowed Codex entry
to the profile before its later provider-state write-through updates the global root. Disabling the
unselected Nous keepalive did not cover this selected-provider path.

This correction changes only the reviewed Hermes bootstrap. It does not change Hermes installation,
profile configuration, model selection, cron prompt/schedule, MCP tools, ERS authority, signer
protocol, or any trading surface.

## 2. Safety decision

For this one profile, the native root `auth.json` is both the read and write authority. Before
Hermes CLI startup, the bootstrap must:

1. require the active Hermes home to be exactly `/root/.hermes/profiles/polymarket`;
2. require the profile-local `.env`, `.op.env`, and `auth.json` paths to be absent;
3. resolve Hermes's native global auth path and require it to be exactly
   `/root/.hermes/auth.json`;
4. redirect Hermes's internal active auth-file resolver to that global path; and
5. retain the existing suppression of the unselected Nous keepalive.

Redirecting the single native resolver is smaller and safer than patching individual credential-pool
call sites. All native auth locks, atomic saves, token rotation, pool status, and concurrent-entry
merge behavior remain intact and operate on one store. No rotated refresh token is discarded.

Hermes's native atomic save creates a random sibling temporary file before replacing `auth.json`
and fsyncing `/root/.hermes`; a file-only systemd write exception cannot support that operation.
The LLM-bearing Hermes process must nevertheless never receive write access to the shared parent:
new root-level Hermes files can appear concurrently, so a static mask list is inherently racy.

The deployment therefore uses a socket-activated, one-request auth writer. Hermes retains a
read-only `/root/.hermes` and sends only the complete native auth-store dictionary over a root-only
Unix socket. The writer has no model, tools, network, profile, persistence loop, or target selector;
it validates the bounded JSON request, invokes Hermes 0.18.2's native `_save_auth_store` against the
one fixed `/root/.hermes/auth.json`, returns success/failure, and exits. Socket activation means it
consumes no persistent process memory between the rare auth writes.

The client must already hold Hermes's native `/root/.hermes/auth.lock`. It transfers a duplicate of
that exact locked open-file description with `SCM_RIGHTS`; the writer proves both that the canonical
path is locked and that the transferred descriptor itself owns that flock. The writer retains the
lease through native save and reply. Serialization therefore survives caller death, while a stale,
waiting, or independently opened decoy descriptor fails closed. Accepted writer instances are also
bound to the Hermes service lifecycle and have a 20-second runtime ceiling.

## 3. Contract

`polybot.hermes.profile_bootstrap` adds:

```python
def _use_native_root_auth_store() -> None: ...
```

The function imports `hermes_cli.auth`, verifies the exact active and global paths plus forbidden
profile artifacts, then replaces only `hermes_cli.auth._auth_file_path` with a zero-argument resolver
returning the verified global path. `main()` installs this redirect before other Hermes modules can
import auth helpers.

The fixed literal paths are deliberate deployment identity pins, not user configuration.

`polybot.hermes.auth_writer` adds a bounded length-prefixed Unix-socket client plus a one-request
server. The bootstrap replaces `hermes_cli.auth._save_auth_store` with that client after preserving
the exact root target; the socket-activated server alone calls the unmodified native save.

## 4. Invariants

- Paper/shadow only; no signer, wallet, order, cancellation, redemption, or chain-write surface.
- The profile remains the existing `polymarket` profile using `openai-codex` and high reasoning.
- The profile never owns credentials and never creates a local auth/env store.
- The global root store remains the only OAuth authority and retains native atomic locking/saves.
- The LLM-bearing Hermes unit cannot write `/root/.hermes`; only the no-model auth writer can create
  the native sibling temp, and it has exactly one fixed target.
- Auth writer requests are root-peer-only, bounded, JSON-object-only, single-request, and fail closed.
- Each request carries the caller's actual native lock lease; missing, unheld, decoy, or wrong-path
  descriptors are rejected before the store is read or saved.
- The writer is socket-activated and exits after one request, so it has zero idle memory footprint.
- Token refresh and pool status updates persist; the correction must not silently drop rotation.
- Other global providers and independent pool entries survive a Codex update unchanged.
- Any unexpected profile/global path or forbidden local artifact fails before Hermes starts.
- The existing exact-five MCP grant and propose-only authority remain byte-for-byte unchanged.
- Both services remain stopped and disabled until the correction is reviewed and installed.

## 5. Failure and recovery

Bootstrap verification failures terminate the Hermes process; systemd's existing bounded restart
policy applies only after an operator-authorized start. A forbidden local auth file is evidence, not
a fallback source. It must be recorded without exposing content, removed only while both services
are stopped, and followed by the full stopped preflight.

The existing forbidden file must never be copied over the newer global store. The global store is
authoritative.

## 6. Acceptance criteria

| Requirement | Evidence |
|---|---|
| Reproduce the delayed leak path | A real native-Hermes pool persistence test is RED because the profile `auth.json` is created before the correction. |
| No profile-local persistence | The same pool update writes only the global root; the profile path remains absent. |
| Refresh is not dropped | Updated dummy access/refresh tokens and pool metadata are present in the global test store. |
| Global merge is preserved | An unrelated provider and independent Codex pool entry remain unchanged. |
| Systemd permits atomic save | A stopped socket-activated writer probe performs the real native atomic save while the Hermes unit rejects shared-root writes. |
| Caller-death serialization | A synchronized server test closes the caller descriptor after save entry and proves the transferred lease remains held through save; an early-close mutation is killed. |
| Fail closed | Wrong active/global paths and any local `.env`, `.op.env`, or `auth.json` refuse bootstrap. |
| No authority expansion | Profile config, model, cron, MCP grant, proposal facade, and signer surfaces are untouched. |
| Installed proof | Native Hermes 0.18.2 temp-path probe passes; stopped preflight passes after incident cleanup. |
| Review strength | Independent specification and security reviews pass; isolated mutations have zero survivors. |
| Regression | Canonical repository suite passes with no skips or xfails added. |

## 7. Deployment boundary

The owner has approved continuing the already-authorized shadow workflow. Code landing, stopped
installation, incident-artifact cleanup, preflight, and subsequent service restart are still recorded
as distinct gates. No production database migration is part of this correction.
