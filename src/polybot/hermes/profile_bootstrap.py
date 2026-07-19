"""Start the reviewed Hermes profile without unrelated auth maintenance."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


_HERMES_ARGV = [
    "/usr/local/lib/hermes-agent/venv/bin/hermes",
    "--profile",
    "polymarket",
    "gateway",
    "run",
    "--replace",
]
_HERMES_PYTHON = "/usr/local/lib/hermes-agent/venv/bin/python"
_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")
_ROOT_AUTH_FILE = Path("/root/.hermes/auth.json")


def _use_native_root_auth_store() -> None:
    """Keep the proposal profile on Hermes's one native root auth store."""
    from hermes_cli import auth

    active_path = auth._auth_file_path()
    global_path = auth._global_auth_file_path()
    if active_path != _PROFILE_HOME / "auth.json" or global_path != _ROOT_AUTH_FILE:
        raise RuntimeError("Hermes auth paths violate the reviewed profile contract")
    if any(
        path.exists() or path.is_symlink()
        for path in (
            _PROFILE_HOME / ".env",
            _PROFILE_HOME / ".op.env",
            _PROFILE_HOME / "auth.json",
        )
    ):
        raise RuntimeError("Hermes profile must use only the native root auth store")

    root_stat = _ROOT_AUTH_FILE.lstat()
    if (
        not stat.S_ISREG(root_stat.st_mode)
        or stat.S_IMODE(root_stat.st_mode) != 0o600
        or root_stat.st_uid != os.geteuid()
        or root_stat.st_gid != os.getegid()
    ):
        raise RuntimeError("Hermes native root auth store is unsafe")

    auth._auth_file_path = lambda: _ROOT_AUTH_FILE


def _disable_unselected_auth_maintenance() -> None:
    # Hermes 0.18.2 starts its global Nous keepalive even when this profile's
    # selected provider is openai-codex. After 60 seconds that thread persists
    # a borrowed Nous credential into the profile-local auth.json. POL-18 uses
    # only the native root auth fallback and must not copy unrelated providers.
    from hermes_cli import nous_auth_keepalive

    nous_auth_keepalive.start_nous_auth_keepalive = lambda *args, **kwargs: None


def main() -> int:
    _use_native_root_auth_store()
    _disable_unselected_auth_maintenance()
    # The supervised process deliberately uses the Hermes CLI path as argv[0]
    # so its native planned-stop verifier can identify the gateway.  CPython
    # consequently reports that script as sys.executable; restore the actual
    # pinned interpreter before Hermes spawns Python helpers such as the MCP
    # stdio watchdog.
    sys.executable = _HERMES_PYTHON
    sys.argv = list(_HERMES_ARGV)

    from hermes_cli.main import main as hermes_main

    return int(hermes_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
