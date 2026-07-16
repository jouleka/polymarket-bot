"""Launch the dedicated Hermes gateway with a minimal inherited environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")
_HERMES_PYTHON = "/usr/local/lib/hermes-agent/venv/bin/python"
_HERMES_ARGV0 = "/usr/local/lib/hermes-agent/venv/bin/hermes"
_BOOTSTRAP_MODULE = "polybot.hermes.profile_bootstrap"
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
        "PYTHONPATH": "/opt/polymarket-bot/src",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def build_gateway_command():
    return _HERMES_PYTHON, [
        _HERMES_ARGV0, "-m", _BOOTSTRAP_MODULE,
        "--profile", "polymarket", "gateway", "run", "--replace",
    ]


def launch_installed_profile(profile_home: str | Path) -> None:
    home = Path(profile_home)
    if home != _PROFILE_HOME or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile path is not the reviewed isolated path")
    executable, argv = build_gateway_command()
    os.execve(executable, argv, build_gateway_environment(os.environ))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    args = parser.parse_args(argv)
    launch_installed_profile(args.profile_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
