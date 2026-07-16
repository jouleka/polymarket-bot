# POL-13 stopped Hermes model-selection evidence

Date: 2026-07-16 UTC

Status: owner-selected model/provider/reasoning configured and exact-five stopped preflight passed;
no credential, cron job, database, service start, enablement, or activation performed

Service checkout: `28c3dab7657e79447824d25b4f677693fc1f35b5`

Predecessor evidence:
[`VERIFICATION-POL13-HERMES-PROFILE.md`](VERIFICATION-POL13-HERMES-PROFILE.md)

## Approved boundary

The owner selected GPT Terra with high reasoning and left the authentication mechanism to the
operator. This stopped gate allowed only model/provider configuration, an isolated authentication
attempt, and local profile verification. It did not authorize copying credentials from another
profile, creating a Hermes cron job, starting or enabling either service, creating production
databases, or activating the shadow.

## Model selection

The dedicated `polybot-hermes` profile now pins:

- model `gpt-5.6-terra`;
- provider `openai-codex`;
- base URL `https://chatgpt.com/backend-api/codex`;
- agent reasoning effort `high`.

The installed Hermes 0.18.2 `DEFAULT_CODEX_MODELS` registry contains exactly one
`gpt-5.6-terra` entry. The resulting profile config is mode 0600 and owned by
`polybot-hermes:polybot-hermes`; its SHA-256 is
`037e87f0ee1cb15b31132dafdc7560b68ddbda8e8a8dab7ed416d76d8dfff362`.

## Authentication boundary

An isolated `openai-codex` OAuth device flow was invoked as `polybot-hermes` with
`HOME=/var/lib/polybot-hermes`. It was not approved in the browser and was cancelled cleanly.
No credential was created: `/var/lib/polybot-hermes/.hermes/auth.json` remains absent, as does the
profile `.env`. No root or other-profile credential was read, copied, or changed.

Authentication is therefore still a later stopped gate. The model selection itself is complete,
but Hermes cannot call the selected provider until a fresh credential owned by `polybot-hermes`
is established.

## Stopped preflight and post-state

The reviewed installed-profile verifier ran as `polybot-hermes` against the dedicated profile with
`--expect-no-cron`. It passed and observed exactly the five approved model-visible MCP methods:
`propose_trade`, `get_market`, `get_book`, `get_ledger`, and `get_flags`. No cron job exists.

Both `polymarket-ingestion.service` and `polymarket-hermes.service` remain loaded, inactive, dead,
and disabled. The production data root still contains only the heartbeat and preserved
`raw-firehose-20260714T155112Z` directory; all four raw-evidence checksum entries pass. No proposal
socket, production database, status file, service start, enablement, or activation was created.

## Remaining separate gates

The next operation is isolated provider authentication while stopped. Cron creation, POL-17 first
start, POL-18 first start, enablement, and shadow activation remain separate explicit gates. Nothing
in this document authorizes any of them.
