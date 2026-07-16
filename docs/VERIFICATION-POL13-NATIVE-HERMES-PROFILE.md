# POL-13 native Hermes profile reconciliation evidence

Date: 2026-07-16 UTC

Status: stopped host corrected to one existing Hermes installation and one native `polymarket`
profile; no cron, database, service start, enablement, or activation performed

Service checkout: `6ea7e94563a1222529ad4f5dbc0862f70fc115e6` (PR #15 merge)

Supersedes the separate-home assumptions in:
[`VERIFICATION-POL13-HERMES-PROFILE.md`](VERIFICATION-POL13-HERMES-PROFILE.md) and
[`VERIFICATION-POL13-HERMES-MODEL.md`](VERIFICATION-POL13-HERMES-MODEL.md).

## Owner clarification and root cause

The owner clarified that Polymarket must be a new profile in the already configured Hermes
installation, not another Hermes home or authentication domain. Hermes 0.18.2 confirms the native
behavior: a named profile under `/root/.hermes/profiles` uses the root provider store as a read-only
fallback when the profile has no local provider override.

The earlier stopped deployment put the profile under `/var/lib/polybot-hermes`, which changed the
Hermes root and therefore could not see the existing OpenAI credential. That unnecessary boundary
caused the second device-login prompt.

## Repository and stopped-unit correction

PR #15 changed the deployment contract, installer, unit, verifier, tests, design, plan, and runbook:

- `polymarket` lives at `/root/.hermes/profiles/polymarket`;
- the installer creates no second Hermes user or home;
- the Hermes unit uses the existing installation/root profile scope and joins only the proposal
  socket group through systemd;
- systemd hides production config/data, root SSH/Codex/config homes, and unrelated Hermes profiles;
- the exact-five effective-inventory preflight remains mandatory;
- another device login and local credential copies are explicitly forbidden.

Focused deployment/profile tests passed 18/18 and the canonical suite passed 2,275 tests. Both
systemd unit files passed `systemd-analyze verify`.

## Host reconciliation

The service checkout fast-forwarded to the PR #15 merge and the stopped-only installer replaced the
unit without starting or enabling either service. The obsolete `polybot-hermes` system user and
`/var/lib/polybot-hermes` home were then removed. There was no running process, credential, cron job,
or production state in that obsolete home.

The native profile was created without clone, alias, or bundled skills. Its reviewed configuration
pins:

- model `gpt-5.6-terra`;
- provider `openai-codex`;
- base URL `https://chatgpt.com/backend-api/codex`;
- reasoning effort `high`.

The config and SOUL are root-owned mode 0600. Config SHA-256 is
`037e87f0ee1cb15b31132dafdc7560b68ddbda8e8a8dab7ed416d76d8dfff362`.
The profile has no `.env` and no local `auth.json`. `hermes --profile polymarket auth list` observes
exactly one existing `openai-codex` device-code credential through native fallback; no token value
was read or copied.

## Final stopped proof

- Installed-profile preflight passed with exactly `propose_trade`, `get_market`, `get_book`,
  `get_ledger`, and `get_flags`, and no cron job.
- `polymarket-ingestion.service` and `polymarket-hermes.service` are loaded, inactive, dead, and
  disabled.
- No proposal socket, production database, status file, cron job, start, enablement, or activation
  exists.
- The production data root still contains only `heartbeat` and the preserved
  `raw-firehose-20260714T155112Z` directory. All four raw-evidence checksum entries pass.
- Existing default, coder, memecoin-trader, and optionsbot gateways/profiles were not restarted or
  reconfigured.

## Remaining separate gates

Authentication is complete through the existing Hermes store. Cron creation is the next stopped
gate. POL-17 first start, POL-18 first start, enablement, and shadow activation remain separate
explicit gates. Nothing in this document authorizes them.
