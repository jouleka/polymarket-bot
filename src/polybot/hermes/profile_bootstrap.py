"""Start the reviewed Hermes profile without unrelated auth maintenance."""

from __future__ import annotations

import sys


_HERMES_ARGV = [
    "/usr/local/lib/hermes-agent/venv/bin/hermes",
    "--profile",
    "polymarket",
    "gateway",
    "run",
    "--replace",
]


def _disable_unselected_auth_maintenance() -> None:
    # Hermes 0.18.2 starts its global Nous keepalive even when this profile's
    # selected provider is openai-codex. After 60 seconds that thread persists
    # a borrowed Nous credential into the profile-local auth.json. POL-18 uses
    # only the native root auth fallback and must not copy unrelated providers.
    from hermes_cli import nous_auth_keepalive

    nous_auth_keepalive.start_nous_auth_keepalive = lambda *args, **kwargs: None


def main() -> int:
    _disable_unselected_auth_maintenance()
    sys.argv = list(_HERMES_ARGV)

    from hermes_cli.main import main as hermes_main

    return int(hermes_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
