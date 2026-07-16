"""Mark the dedicated Hermes profile stop as planned before signalling it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal


_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")


def stop_installed_profile(profile_home: str | Path) -> bool:
    """Signal only the gateway identified by the reviewed profile PID state."""
    home = Path(profile_home)
    if home != _PROFILE_HOME or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile path is not the reviewed isolated path")
    os.environ["HERMES_HOME"] = str(home)

    # Late import is required because Hermes caches HERMES_HOME in module globals.
    from gateway.status import get_running_pid, write_planned_stop_marker

    pid = get_running_pid(cleanup_stale=False)
    if pid is None:
        return False
    if pid <= 1 or pid == os.getpid():
        raise RuntimeError("refusing unsafe Hermes gateway PID")
    if not write_planned_stop_marker(pid):
        raise RuntimeError("could not mark Hermes gateway stop as planned")
    os.kill(pid, signal.SIGTERM)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stop-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    args = parser.parse_args(argv)
    stop_installed_profile(args.profile_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
