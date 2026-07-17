# DESIGN — POL-13 MCP security upgrade

Date: 2026-07-17 UTC

## 1. Problem

The repository and both deployed Python environments pin MCP 1.26.0. GitHub reports three open
high-severity advisories affecting that release. Their patched floor is MCP 1.28.1. The production
bridge uses local stdio only—not MCP HTTP, WebSocket, or experimental task transports—but retaining
a known-vulnerable SDK violates the reviewed dependency contract.

## 2. Contract

- Pin MCP exactly to 1.28.1 in `pyproject.toml` and the stopped runtime installer.
- Change the Hermes profile verifier's supported MCP version to exactly 1.28.1. Any other version
  remains a fail-closed preflight error.
- Preserve Hermes Agent 0.18.2, the existing `polymarket` profile, native authentication, cron,
  exact-five tool inventory, stdio-only bridge, Unix proposal RPC, and all no-signing boundaries.
- Upgrade the application venv and the existing Hermes venv while both services are stopped. Do
  not create or reinstall Hermes or its profile.
- Restart in dependency order only after both installed environments pass exact version/import and
  exact-five preflight checks.
- Correct the stale POL-17/POL-18 ticket summaries to reflect the already-active deployment.

## 3. Safety invariants

There is no MCP network listener, HTTP/WebSocket transport, task API, extra server, resource,
prompt, native tool, signer, wallet, or execution authority. Existing configuration/data/auth
checksums and database integrity must survive the stopped update. A version mismatch prevents
Hermes startup.

## 4. Acceptance

- A real RED proves the old supported-version contract rejects the patched target.
- Focused MCP/profile/deployment tests and the canonical suite pass on MCP 1.28.1.
- The exact-five preflight passes in an isolated Hermes 0.18.2 + MCP 1.28.1 environment.
- Independent specification and security reviews pass; mutations of either pin or fail-closed
  version check are killed.
- After reviewed landing, the stopped install preserves all production evidence and an ordered
  restart returns both paper/shadow services to zero-restart healthy operation.

