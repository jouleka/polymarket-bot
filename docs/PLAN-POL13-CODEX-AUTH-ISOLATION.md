# POL-13 Codex OAuth auth-store isolation correction plan

**Design:** `docs/DESIGN-POL13-CODEX-AUTH-ISOLATION.md`  
**Method:** strict serial TDD, independent review, isolated mutations

## 1. Reconcile and preserve the incident

1. Confirm both services are stopped and disabled.
2. Record only forbidden-file metadata, timestamps, mode, ownership, and checksums; never print
   token contents.
3. Trace the installed Hermes 0.18.2 pool refresh and persistence path.
4. Keep the forbidden file in place until the reviewed correction is ready for stopped installation.

## 2. RED: reproduce native pool persistence

Add one test using dummy credentials and temporary active/global auth paths. Load the installed
Hermes auth and credential-pool modules, seed a root Codex provider/pool plus unrelated state, invoke
the real pool persistence boundary, and assert the reviewed bootstrap keeps the profile auth path
absent while updating root. Run only that test and observe the correct failure: current bootstrap
does not redirect the active auth path, so Hermes creates the profile file.

## 3. GREEN: minimal bootstrap correction

Add `_use_native_root_auth_store()` and invoke it before the existing keepalive patch and Hermes main
import. Verify exact profile/root identity and forbidden local artifact absence, then redirect only
Hermes's native active auth resolver. Run the focused test, the POL-18 profile/deploy tests, and the
canonical full suite. Checkpoint the passing cycle.

## 4. Fail-closed and preservation pins

Serially add tests for:

1. wrong profile home;
2. missing or unexpected global auth path;
3. each forbidden local secret artifact;
4. native root token/pool update persistence;
5. preservation of unrelated provider and independent pool state; and
6. bootstrap ordering before any Hermes runtime import can cache auth behavior.

Each concern follows one RED, minimum GREEN, focused suite, full suite, and checkpoint commit.

## 5. Review and mutation gate

Run independent specification review followed by independent security review. Then isolate mutations
that remove the redirect, install it after Hermes import, redirect only one imported alias, allow a
profile auth file, redirect to an unexpected path, silently drop refresh persistence, clobber an
unrelated provider/pool entry, or expand model/tool/runtime authority. Every mutation must be killed
by a named test. Re-review any fix and rerun the complete suite.

## 6. Land and stopped installation

Update verification evidence, HANDOFF, TICKETS, and POL-13. Publish and merge the reviewed branch,
then fast-forward the service checkout while services remain stopped. Run a native Hermes 0.18.2
temp-path persistence probe. Record and unlink only the known forbidden profile auth file; never copy
it into root. Run the exact stopped preflight and prove both units remain disabled.

## 7. Resume bounded shadow

After the stopped gate passes, restart only the already-approved paper/shadow services. Verify exact
five tools, one cron, zero proposals before a genuine brain proposal, no signing authority, bounded
memory, zero raw CLOB websocket rows, and no profile auth/env artifact. Observe through at least the
prior token-refresh boundary and stop on any auth-isolation, authority, integrity, or resource-limit
failure.
