# POL-13 stopped composite-configuration evidence

Date: 2026-07-16 UTC

Status: stopped POL-17/POL-18 composite configuration complete; no database creation, Hermes
profile, model authentication, cron, service start, enablement, or activation performed

Service checkout: `28c3dab7657e79447824d25b4f677693fc1f35b5`

Predecessor evidence:
[`VERIFICATION-POL13-STOPPED-INSTALL.md`](VERIFICATION-POL13-STOPPED-INSTALL.md)

## Approved boundary

The owner approved the stopped composite-configuration gate after the stopped code/identity/unit
install. This gate allowed backup and reconciliation of `/opt/polymarket-bot/config.toml`, selection
and read-only probing of two independent Polygon providers, and sterile configuration validation as
`polybot`. It did not authorize runtime construction, database creation/migration, Hermes profile or
model state, cron, service start, enablement, or activation.

## Provider selection and live proof

Polygon's [official RPC endpoint directory](https://docs.polygon.technology/pos/reference/rpc-endpoints)
lists dRPC and Allnodes/PublicNode as separate public Polygon PoS providers. PublicNode's
[Polygon gateway](https://polygon.publicnode.com/) publishes its Bor RPC endpoint. The stopped
configuration therefore pins:

| Provider ID | HTTPS endpoint | Operator |
|---|---|---|
| `polygon-publicnode` | `https://polygon-bor-rpc.publicnode.com` | Allnodes/PublicNode |
| `polygon-drpc` | `https://polygon.drpc.org` | dRPC |

Both exact endpoints first returned canonical `eth_chainId=0x89`. The production
`JsonRpcResolutionProvider` adapters then independently returned chain ID 137, latest block
90,323,104, and the same block hash
`0x341a715f0d1949fc606f3cb9bcda626b5b9b1f7ae3f4bdd4c4a00700d5afc7c2` for that height.
Only the reviewed read-only vocabulary was used. Public endpoints may rate-limit; runtime
readiness and resolution authority still require both providers and fail closed on unavailability
or disagreement.

## Config reconciliation

The original D4a file was preserved byte-for-byte as
`/opt/polymarket-bot/config.toml.pre-pol17` with SHA-256
`4d20478488130b4b95350c9ab0cc66a16229a450775ed5aa8ea3fffbbe0d346f` and mode
`0640 root:polybot`. Every original ingestion value remains unchanged, including the 60-second
midpoint cadence and full deduplicated trade-tape settings.

The only additions to production config are the reviewed `[shadow]` section:

- six additional SQLite paths, producing seven exact distinct database identities with the
  existing `market_memory.db`;
- cycle/registry/resolution/news/RPC/readiness/outbox/status settings from the reviewed example;
- `/run/polybot-proposal/proposal.sock`, group `polybot-proposal`, 20 proposals/minute, and a
  two-second request timeout; and
- the two providers above.

Reconciled config SHA-256 is
`5c085043547dcfa538e4ba9f86075eb013be3dd32c15e1f53cd3ac772c690f75`, mode
`0640 root:polybot`. `polybot-hermes` still cannot read it.

## Sterile validation evidence

`load_shadow_config` ran as `polybot` under an empty environment with only `PATH` and `PYTHONPATH`.
It proved:

- midpoint cadence exactly 60 seconds;
- seven exact, unique database paths;
- the exact ordered provider ID/URL pairs;
- exact proposal socket path/group/rate/timeout; and
- no environment override or construction side effect.

The first verification harness compared the loader's documented string path projection to
`Path` objects and stopped on that test-only type mismatch. A read-only diagnostic displayed the
correct parsed values; the corrected exact-string assertions passed. Neither attempt constructed
the runtime or created a file.

Before and after config parsing and provider probing, all seven configured database paths were
absent. The production data root still contained only the 23-byte heartbeat plus
`raw-firehose-20260714T155112Z`; its checksum manifest passed again. Neither
`/run/polybot-proposal` nor `/run/polybot/shadow-status.json` exists.

Both `polymarket-ingestion.service` and `polymarket-hermes.service` remain
`LoadState=loaded`, `ActiveState=inactive`, `SubState=dead`, and `UnitFileState=disabled`.

## Remaining separate gates

The next possible operation is isolated Hermes profile creation while stopped. Model/provider
selection and isolated model authentication, exact-five effective-inventory preflight, cron
creation, POL-17 first start, POL-18 first start, and enablement are each later explicit gates.
Nothing in this document authorizes any of them.
