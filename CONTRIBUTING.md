# Contributing

Thanks for helping improve `polymarket-bot`.

## Before opening a change

- Keep all examples and tests paper-only. Do not add private keys, wallet credentials, tokens,
  production endpoints, or personal infrastructure details.
- Open an issue before proposing a large architectural change.
- Keep changes focused and include regression tests for changed behavior.
- Do not present simulated results as evidence of live profitability.

## Development setup

Use Python 3.11 or newer and `uv`. Linux is required for the complete runtime and test contract.

```bash
uv sync --locked --extra dev
uv run --locked pytest
```

Before submitting a pull request, run the relevant targeted tests and the complete suite on Linux.
Describe any platform-specific exclusions in the pull request.

## Security reports

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md).
