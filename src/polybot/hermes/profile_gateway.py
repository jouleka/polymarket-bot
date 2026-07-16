"""Launch the dedicated Hermes gateway with a minimal inherited environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")
_HERMES = "/usr/local/lib/hermes-agent/venv/bin/hermes"
_PASSTHROUGH_ENV = frozenset({
    "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "INVOCATION_ID",
    "JOURNAL_STREAM", "LANG", "LC_ALL", "LC_CTYPE", "NO_PROXY", "PATH",
    "REQUESTS_CA_BUNDLE", "SSL_CERT_DIR", "SSL_CERT_FILE", "SYSTEMD_EXEC_PID",
    "TZ", "https_proxy", "http_proxy", "no_proxy",
})


def build_gateway_environment(source):
    """Keep only transport/locale/systemd values; drop every authority secret."""
    env = {
        key: value for key, value in source.items()
        if key in _PASSTHROUGH_ENV and isinstance(value, str)
    }
    env.update({
        "HOME": "/root",
        "HERMES_HOME": str(_PROFILE_HOME),
        "HERMES_KANBAN_DISPATCH_IN_GATEWAY": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def launch_installed_profile(profile_home: str | Path) -> None:
    home = Path(profile_home)
    if home != _PROFILE_HOME or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile path is not the reviewed isolated path")
    argv = [_HERMES, "--profile", "polymarket", "gateway", "run", "--replace"]
    os.execve(_HERMES, argv, build_gateway_environment(os.environ))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    args = parser.parse_args(argv)
    launch_installed_profile(args.profile_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
