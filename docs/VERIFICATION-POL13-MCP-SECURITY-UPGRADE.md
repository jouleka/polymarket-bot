# VERIFICATION — POL-13 MCP security upgrade

Date: 2026-07-17 UTC

## Security scope

GitHub Dependabot alerts 1–3 identify three high-severity MCP SDK vulnerabilities. The affected
repository pin was 1.26.0; the highest patched floor is 1.28.1. The deployed Polymarket integration
uses only a capability-minimal stdio MCP server connected to a local Unix proposal RPC. It exposes
no MCP HTTP/WebSocket listener or experimental task API, so this is dependency hardening rather
than evidence of an exploited production path.

The change pins MCP 1.28.1 in project metadata, the stopped installer, and the fail-closed Hermes
profile verifier. It does not upgrade or recreate Hermes Agent 0.18.2, any profile, model,
authentication, cron state, or tool configuration.

## TDD and compatibility evidence

The first RED asserted the patched supported version and observed the old 1.26.0 constant. The
second RED asserted the stopped installer pin and observed its old 1.26.0 requirement. Minimum
changes made each focused test green. An explicit regression proves that 1.26.0 now fails the
effective-inventory verifier.

With MCP 1.28.1 installed in the development venv, 36 focused profile, deployment, MCP bridge, and
whole-slice tests pass. A temporary MCP 1.28.1 import overlay on the actual native Hermes 0.18.2
Python environment passed the installed profile's exact-five tool discovery and imported the
patched `ClientSession`. The overlay did not modify the running production environments.

Final canonical, independent-review, mutation, landing, deployment, and live evidence follows.

