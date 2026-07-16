# POL-13 isolated Hermes profile creation evidence

Date: 2026-07-16 UTC

Status: dedicated stopped profile created; no model/provider authentication, effective-inventory
preflight, cron job, service start, enablement, database creation, or activation performed

Service checkout: `28c3dab7657e79447824d25b4f677693fc1f35b5`

Predecessor evidence:
[`VERIFICATION-POL13-STOPPED-CONFIG.md`](VERIFICATION-POL13-STOPPED-CONFIG.md)

## Approved boundary

The owner explicitly approved only isolated Hermes profile creation while both POL-17 and POL-18
remained stopped. This gate allowed creating the dedicated `polymarket` profile as
`polybot-hermes`, with no clone, alias, or bundled skills, and installing the reviewed profile
config and SOUL. It did not authorize model credentials, provider selection, tool execution,
effective-inventory activation preflight, cron, service start, enablement, or activation.

## Pre-creation state

- Hermes Agent is exactly 0.18.2 and MCP is exactly 1.26.0.
- `/var/lib/polybot-hermes` contained no `.hermes` directory or profile.
- Existing root profile directories were exactly `coder`, `memecoin-trader`, and `optionsbot`.
  Their tree contained 15,195 entries; a content/path aggregate was recorded before the gate.
- Both systemd units were loaded, inactive, dead, and disabled.
- Production config hashes matched the stopped-config evidence; the data root contained only the
  heartbeat plus preserved raw-firehose evidence; no runtime socket or status file existed.

## Creation performed

The reviewed runbook command ran as `polybot-hermes` with
`HOME=/var/lib/polybot-hermes`:

```text
hermes profile create polymarket --no-alias --no-skills
```

The description is `Isolated propose-only Polymarket paper analyst`. No `--clone`, `--clone-all`,
or `--clone-from` path was used. The reviewed `config.yaml` and `SOUL.md` from the service checkout
were then installed byte-for-byte as `0600 polybot-hermes:polybot-hermes`.

## Generated `.env` finding

Hermes 0.18.2 generated a 165-byte mode-0600 `.env` template even though profile creation reported
that no API keys existed. A key-name/value-length-only inspection found zero non-comment,
non-blank assignments; no credential value was read or logged. The reviewed runbook requires the
profile `.env` to be absent, so this newly generated comment-only template was removed within the
approved profile gate. It did not reappear during validation.

## Post-creation proof

- The dedicated profile owns separate `cron`, `home`, `logs`, `memories`, `plans`, `sessions`,
  `skills`, `skins`, and `workspace` directories beneath
  `/var/lib/polybot-hermes/.hermes/profiles/polymarket`.
- `.no-bundled-skills` exists; the profile `skills` directory is empty.
- The profile `cron` directory is empty.
- No profile `.env` exists.
- `config.yaml` and `SOUL.md` byte-match the reviewed templates and are both mode 0600.
- The config still has `OWNER_CONFIG_REQUIRED` for both model and provider, exactly one
  `polymarket` MCP server, exactly the five approved tools, and resources/prompts disabled.
- `profile.yaml` contains only the reviewed description and `description_auto: false`.
- `polybot-hermes` remains unable to read production config or traverse production data.

The root profile directory list and tree-entry count remained unchanged. The aggregate root profile
hash advanced only because already-running `coder` and `optionsbot` cron heartbeat/database/log
files changed during the observation window; no root profile config, SOUL, MEMORY, USER, directory,
or other static profile file changed. No existing profile was cloned, edited, stopped, or restarted.

Both POL-17 and POL-18 units remain loaded, inactive, dead, and disabled. Production config hashes,
data-root contents, and raw-firehose checksums remain unchanged. No DB, proposal socket, status
file, cron job, model credential, start, enablement, or activation was created.

## Remaining separate gates

The next possible operation is owner-selected model/provider authentication while stopped. The
credential must be isolated to this profile and must not be copied from root or written to POL-17
config, `.env`, source, prompt, SOUL, skill, memory, or cron. Exact-five effective-inventory
preflight, cron creation, POL-17 first start, POL-18 first start, and enablement remain later
explicit gates. Nothing in this document authorizes them.
