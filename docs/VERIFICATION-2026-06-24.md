# Verification — Phase 0 (2026-06-24)

Results of the S0/S2 verification gate ([POL-2](https://mysigner.youtrack.cloud/issue/POL-2),
[POL-4](https://mysigner.youtrack.cloud/issue/POL-4)). GitHub checks done via authenticated `gh`
(account `jouleka`); local hashes via `git hash-object`.

## Hermes provenance — PASS (high confidence)
- Official repo `NousResearch/hermes-agent`: public, MIT, default branch `main`, latest release
  `v2026.6.19` (matches the install's version `0.17.0`).
- Byte-identical git-blob comparison of the local install (`C:\Users\Admin\AppData\Local\hermes\hermes-agent`)
  vs the official repo:
  - `pyproject.toml` → `d269ba84…` = official @ `v2026.6.19` **and** @ `main`. **MATCH.**
  - `hermes_cli/main.py` → `916d33bb…` = official @ `main`. **MATCH** (differs from the tag — consistent
    with an install from `main` on 2026-06-24, just ahead of the `v2026.6.19` tag).
- **Conclusion:** genuine official code, installed from `main`. No repackaged / SEO-mirror installer.
- **Caveat:** 2 core files verified byte-identically (incl. `pyproject.toml`, which encodes the whole
  dependency / supply-chain posture), not the entire tree. For a fully rigorous gate before wiring
  keys, hash every file against the matching commit.

## Signing path — RESOLVED: use the official Rust client
- **Python `py-clob-client-v2` (latest `v1.0.1`):** OPEN deposit-wallet auth bugs — `#70` (POLY_1271 L1
  auth binds the API key to the EOA, not the deposit wallet → orders rejected) and `#77`, both
  `state=open`; `#53` EOA basic flow rejected (`maker address not allowed, please use the deposit
  wallet flow`); `#56` Magic-proxy also rejected on 1.0.0/1.0.1.
- **TypeScript `clob-client-v2`:** same class of bug — `#64` `state=open`.
- **Docs** (`docs.polymarket.com/developers/CLOB/authentication`) nominally list EOA (type 0) as
  supported, but live behavior + the issue tracker show EOA/proxy rejected for **new** accounts →
  treat the docs as partly stale.
- **Official Rust client `Polymarket/rs-clob-client-v2`** (pushed 2026-06-24, actively maintained):
  threads `funder` + `signature_type` through L1 auth and produces credentials bound to the deposit
  wallet (EIP-1271). Currently the only official SDK that works for new deposit-wallet trading.
- **Decision:** build the signer + order-construction core on **Rust (`rs-clob-client-v2`)**, exposed
  to the Python ERS as a sidecar / subprocess. The Python/TS V2 SDKs are not viable for a new headless
  deposit-wallet bot until these bugs close.
- **Still required (S2 / [POL-4](https://mysigner.youtrack.cloud/issue/POL-4) acceptance):** empirically
  place + cancel ONE real min-size order via `rs-clob-client-v2` before building on it (one report
  claimed rs `0.5.1` was also affected — prove it live).

## Sources
- <https://github.com/Polymarket/py-clob-client-v2/issues/70> · `/77` · `/53` · `/56`
- <https://github.com/Polymarket/clob-client-v2/issues/64>
- <https://github.com/Polymarket/rs-clob-client-v2>
- <https://docs.polymarket.com/developers/CLOB/authentication>
- <https://github.com/NousResearch/hermes-agent> (release `v2026.6.19`)
