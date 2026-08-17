# Security policy

## Supported versions

Only the latest commit on `main` is considered for security fixes. The project is experimental and
has not received an independent security audit.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities, credentials, private infrastructure details, or exploit
steps in a public issue.

Use the repository's **Security > Report a vulnerability** form. If that button is unavailable,
private vulnerability reporting has not been configured and the public-release setup is incomplete.
Do not substitute a public issue; contact the maintainer through a private channel listed on their
GitHub profile and include:

- the affected commit and component;
- the security impact and required preconditions;
- minimal reproduction steps; and
- any suggested remediation.

Please allow reasonable time for triage and a coordinated fix before public disclosure.

## Secrets and live funds

The repository must never contain private keys, wallet seed phrases, API tokens, production
credentials, or identifying production-host information. The current project is paper-only and
does not provide a supported live-trading path.
