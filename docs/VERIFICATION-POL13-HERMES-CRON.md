# POL-13 stopped Hermes cron gate evidence

Date: 2026-07-16 UTC

Status: exactly one reviewed propose-only cron job created and fully preflighted while the gateway
remains stopped; no database, service start, enablement, or activation performed

Service checkout: `768d3b8eead0c68ef0bab422061516aee9a370f9`

Predecessor evidence:
[`VERIFICATION-POL13-MEMORY-GUARDRAILS.md`](VERIFICATION-POL13-MEMORY-GUARDRAILS.md)

## Approved boundary

The owner approved continuing from the stopped profile/model/auth/memory gates. This gate created
only the reviewed Hermes cron state and ran stopped verification. It did not start either gateway
or runtime, create a production database or proposal, enable a unit, or activate shadow execution.

## Pre-state

- Both systemd units were loaded, inactive, dead, and disabled with the reviewed memory/swap caps.
- `hermes --profile polymarket cron list` reported no scheduled jobs.
- The profile inherited exactly one existing `openai-codex` credential and had no local `auth.json`
  or `.env`.
- Profile config SHA-256 was
  `037e87f0ee1cb15b31132dafdc7560b68ddbda8e8a8dab7ed416d76d8dfff362`.
- Reviewed cron-prompt SHA-256 was
  `a0fb1c252c764d9a86709447b8b6b1889756994c22f5c4f3372981cfe5b1bd14`.

## Created contract

The runbook's direct `cron.jobs.create_job` path created one job:

- ID `ad1c2d9b8c30`;
- name `polymarket-propose-only`;
- schedule `every 5m` with unbounded repeat;
- local delivery only;
- no skills, script, workdir, alternate model/provider/base URL, session attachment, or messaging
  destination;
- exactly one enabled toolset: `polymarket`.

The profile gateway is stopped, so the active schedule cannot execute until the separately approved
Hermes first-start gate.

## Full stopped verification

The installed `polybot.hermes.profile_verify` ran with the production profile and socket-group
identity. It passed all of the following as one fail-closed contract:

- Hermes 0.18.2 and MCP 1.26.0;
- exactly one MCP server with the reviewed command, environment, timeouts, and no parallel calls;
- resources and prompts disabled;
- exactly `propose_trade`, `get_market`, `get_book`, `get_ledger`, and `get_flags` in authored,
  discovered, and final model-visible inventories;
- exact cron name, five-minute schedule, prompt, delivery, empty skills, and toolset;
- conservative approval/security settings and no native tool authority.

The verifier ended `POL-18 Hermes profile effective inventory: exact five; PASS`.

Both units remain loaded, inactive, dead, and disabled with combined hard RAM capped at 1.25 GiB
and swap at 256 MiB. No proposal socket, production database, status file, start, enablement, or
activation exists. The production data root still contains only the heartbeat and preserved
raw-firehose directory; all four checksum entries pass.

## Remaining separate gates

POL-17 first start is next. It must be started without enablement and observed for readiness, live
books, provider agreement, outbox recovery, compact persistence, zero raw rows, and cgroup memory
pressure. POL-18 first start, enablement, and the shadow observation period remain later explicit
gates. Nothing in this document authorizes them.
