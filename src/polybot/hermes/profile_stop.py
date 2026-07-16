"""Mark the dedicated Hermes profile stop as planned before signalling it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import time


_PROFILE_HOME = Path("/root/.hermes/profiles/polymarket")
_STOP_TIMEOUT_SECONDS = 50.0


def _wait_for_gateway_exit(pid, expected_start_time, *, pid_exists,
                           get_start_time, monotonic=time.monotonic,
                           sleep=time.sleep,
                           timeout_seconds=_STOP_TIMEOUT_SECONDS):
    deadline = monotonic() + timeout_seconds
    while True:
        if not pid_exists(pid):
            return
        current_start = get_start_time(pid)
        if (expected_start_time is not None and current_start is not None
                and current_start != expected_start_time):
            return
        if monotonic() >= deadline:
            raise RuntimeError(
                f"Hermes gateway PID {pid} did not stop within "
                f"{timeout_seconds:.0f}s"
            )
        sleep(0.1)


def stop_installed_profile(profile_home: str | Path) -> bool:
    """Signal only the gateway identified by the reviewed profile PID state."""
    home = Path(profile_home)
    if home != _PROFILE_HOME or not (home / "config.yaml").is_file():
        raise RuntimeError("Hermes profile path is not the reviewed isolated path")
    os.environ["HERMES_HOME"] = str(home)

    # Late import is required because Hermes caches HERMES_HOME in module globals.
    from gateway.status import (
        _pid_exists, get_process_start_time, get_running_pid,
        write_planned_stop_marker,
    )

    pid = get_running_pid(cleanup_stale=False)
    if pid is None:
        return False
    if pid <= 1 or pid == os.getpid():
        raise RuntimeError("refusing unsafe Hermes gateway PID")
    expected_start_time = get_process_start_time(pid)
    if not write_planned_stop_marker(pid):
        raise RuntimeError("could not mark Hermes gateway stop as planned")
    os.kill(pid, signal.SIGTERM)
    _wait_for_gateway_exit(
        pid,
        expected_start_time,
        pid_exists=_pid_exists,
        get_start_time=get_process_start_time,
    )
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stop-polymarket-hermes")
    parser.add_argument("--profile-home", required=True)
    args = parser.parse_args(argv)
    stop_installed_profile(args.profile_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
