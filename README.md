# polymarket-bot

[![CI](https://github.com/jouleka/polymarket-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/jouleka/polymarket-bot/actions/workflows/ci.yml)
[![CodeQL](https://github.com/jouleka/polymarket-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/jouleka/polymarket-bot/actions/workflows/codeql.yml)

An experimental, paper-only research system for evaluating automated strategies on Polymarket.
It combines market and news ingestion, deterministic execution and risk controls, resolution and
settlement tracking, simulated execution, and a propose-only reasoning-agent bridge.

> [!WARNING]
> This project is unaudited research software. It does not currently include a live signer, wallet
> integration, or live order-submission path. It makes no claim of profitability and must not be
> used with real funds.

## Current status

| Capability | Status |
| --- | --- |
| Market, order-book, news, and resolution ingestion | Implemented |
| Immutable market registry and deterministic ERS checks | Implemented |
| Paper execution, accounting, and shadow evidence | Implemented |
| Propose-only reasoning-agent bridge | Implemented |
| Live signing and order submission | Not implemented |
| Production readiness or independent security audit | Not complete |

The repository is under active development. The full runtime and some integration tests rely on
Linux-specific process and Unix-socket security features.

## Safety and authority model

The reasoning layer can read curated market context and submit a proposed trade. It cannot approve,
sign, or submit an order. A separate deterministic Execution and Risk Service (ERS) validates and
re-prices proposals, applies sizing and risk limits, and records simulated outcomes.

The current signer implementation is paper-only. No private key or wallet credential should ever be
stored in this repository.

## Architecture

```text
market/news sources
        |
        v
ingestion + market registry ---> curated read views
        |                              |
        v                              v
resolution tracking            propose-only agent
        |                              |
        +-------------> ERS <----------+
                         |
                         v
                 paper execution
                         |
                         v
              shadow evidence + P&L
```

The main packages live under `src/polybot/`:

- `ingestion`, `runtime`, and `resolution` collect and normalize external state.
- `ers` owns deterministic validation, safety controls, and lifecycle supervision.
- `harness` records paper fills, evidence, and simulated P&L.
- `hermes` exposes curated reads and a propose-only bridge.
- `calibration`, `detectors`, `fusion`, `maker`, and `truthgate` contain research components used
  to assess signals and simulated decisions.

## Local setup

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required. Linux is the supported
platform for the complete runtime and test contract.

```bash
git clone https://github.com/jouleka/polymarket-bot.git
cd polymarket-bot
uv sync --locked --extra dev
```

Run the tests with:

```bash
uv run --locked pytest
```

On macOS, Linux-only supervision and peer-credential tests are expected not to run successfully.
Do not treat a partial macOS result as release verification.

The generic configuration in [`deploy/config.example.toml`](deploy/config.example.toml) uses local
paper-mode data paths and public, unauthenticated market sources. Copy it before changing values:

```bash
cp deploy/config.example.toml config.local.toml
mkdir -p data /tmp/polybot
chmod 700 data /tmp/polybot
uv run --locked polybot-shadow --config config.local.toml
```

This command performs network reads and simulated accounting only. It has no signer, wallet, or live
order client.

## Project boundaries

- Paper and shadow evaluation only.
- No financial, investment, or legal advice.
- No guarantee that upstream APIs, market rules, fee schedules, or venue availability remain
  unchanged.
- No deployment credential, private key, wallet seed, token, or production host detail belongs in
  source control.

## Security

Please do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md)
for supported versions and private reporting instructions.

## Contributing

This repository is not yet accepting production-use claims or live-trading integrations. Focused
bug reports and reproducible paper-mode improvements are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
