# POL-13 stopped code/identity/unit installation evidence

Date: 2026-07-15 UTC

Status: stopped installation gate complete; composite configuration, Hermes profile, model/provider,
cron, service start, enablement, and paper/shadow activation not performed

Installed checkout: `28c3dab7657e79447824d25b4f677693fc1f35b5`

POL-18 implementation merge: `09a3a6b18d9e393ce535c7612789586399d37feb`

Repository reconciliation merge: `28c3dab7657e79447824d25b4f677693fc1f35b5`

## Approved boundary

The owner explicitly approved the first POL-13 operational gate: update the GitHub-linked service
checkout and run the reviewed code/isolated-identity/systemd-unit installer while leaving both
services stopped and disabled. The approval did not include composite configuration, provider
selection, Hermes profile/model/cron creation, service start, enablement, database migration, or
activation.

## Pre-install evidence

- `/opt/polymarket-bot` was at `65a6d7e392a9e5885a5d198a1eb5a1d9f8c4a270` with only the
  expected untracked production `config.toml`; origin already pointed to GitHub.
- `polymarket-ingestion.service` was loaded, inactive, and disabled;
  `polymarket-hermes.service` was not found.
- Production config SHA-256 was
  `4d20478488130b4b95350c9ab0cc66a16229a450775ed5aa8ea3fffbbe0d346f`.
- The preserved `data/raw-firehose-20260714T155112Z/SHA256SUMS` manifest passed for `SIZES.txt`,
  `market_memory.db`, its SHM, and its zero-byte WAL. The evidence database remained 169,467,904
  bytes.
- The production data root contained only the 23-byte heartbeat plus the preserved raw-firehose
  directory. No compact or composite runtime database existed.

## Installation performed

1. Reasserted the ingestion unit stopped/disabled state.
2. Fast-forwarded `/opt/polymarket-bot` from `65a6d7e` to GitHub `main` at `28c3dab` with
   `git pull --ff-only`; the untracked production config was preserved.
3. Ran `/opt/polymarket-bot/deploy/install.sh` once.
4. The installer created `polybot-hermes`, created `polybot-proposal`, added only `polybot` and
   `polybot-hermes` to that socket group, installed MCP 1.26.0 and runtime dependencies into the
   existing service venv, copied both reviewed unit files, reloaded systemd, and left both units
   stopped and disabled.

## Post-install proof

- Service checkout is exactly `28c3dab`, tracks GitHub `main`, and still has only the expected
  untracked `config.toml`.
- Both `polymarket-ingestion.service` and `polymarket-hermes.service` are
  `LoadState=loaded`, `ActiveState=inactive`, `SubState=dead`, and `UnitFileState=disabled`.
- Both installed `/etc/systemd/system` unit files byte-match the reviewed checkout.
- `polybot` is a nologin system user in groups `polybot polybot-proposal`.
- `polybot-hermes` is a nologin system user with home `/var/lib/polybot-hermes` and groups exactly
  `polybot-hermes polybot-proposal`; it is not a member of `polybot`.
- `/var/lib/polybot-hermes` is `0700 polybot-hermes:polybot-hermes`; no `.hermes` directory or
  profile exists.
- Production config bytes are unchanged at the same SHA-256 and now have the reviewed
  `0640 root:polybot` ownership/mode. Data is `0750 polybot:polybot`.
- `polybot-hermes` cannot read `config.toml` or `.env` and cannot traverse `data`.
- MCP package version is exactly 1.26.0; reviewed runtime imports load successfully.
- The raw-firehose checksum manifest still passes. The data root still contains only heartbeat plus
  the preserved evidence directory; no production database was created or opened.
- `/run/polybot-proposal` and `/run/polybot/shadow-status.json` do not exist because neither service
  has started.

## Remaining separate gates

The preserved config is still the old D4a ingestion-only file. It cannot pass the POL-17 stopped
configuration preflight until it is backed up and reconciled with seven distinct database paths,
the proposal socket settings, and exactly two real independently operated read-only Polygon HTTPS
providers. Placeholder providers are forbidden. Configuration validation must not construct the
runtime or create a database.

After that, the still-separate gates are: isolated Hermes profile creation; owner-selected
model/provider authentication; exact-five stopped preflight; cron creation; first POL-17 start;
first POL-18 start; and enablement. None is authorized by this evidence.
