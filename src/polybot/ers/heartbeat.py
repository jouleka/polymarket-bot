"""Fate-isolated file heartbeat (S4.3 / POL-6).

The trading loop's ERSController calls ``beat()`` each cycle, writing a
monotonically-increasing counter + a timestamp to a FILE. The out-of-band
supervisor (a SEPARATE OS process) reads that file to decide liveness. A file is
used deliberately: it survives a wedged interpreter and is readable out-of-process,
unlike the in-process EventStore / MonotonicStamper which die with the process.

Staleness is computed against an injected ``now`` (``last_beat_age(now)`` =
``now - stored_time``), so the unit tests are deterministic with no real sleep; the
integration gate passes the real ``time.monotonic`` consistently to both sides.

Fail-closed: a missing/never-written file reads as +inf age (NOT alive). The write is
best-effort-atomic (write to a temp sibling + ``os.replace``) so a reader never sees a
half-written line.
"""

import math
import os
import time


class Heartbeat:
    def __init__(self, path, *, clock=None):
        self._path = path
        self._clock = clock or time.time
        self._counter = 0

    def beat(self):
        self._counter += 1
        line = f"{self._counter} {self._clock()!r}\n"
        tmp = f"{self._path}.tmp"
        with open(tmp, "w") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)   # atomic on POSIX -> reader never sees a partial line

    def _read(self):
        """(counter, timestamp) or None if the file is missing / unreadable / partial."""
        try:
            with open(self._path) as fh:
                raw = fh.read().strip()
        except (FileNotFoundError, OSError):
            return None
        if not raw:
            return None
        parts = raw.split()
        if len(parts) != 2:
            return None
        try:
            counter, ts = int(parts[0]), float(parts[1])
        except ValueError:
            return None
        if not math.isfinite(ts):
            # A corrupt / injected non-finite stamp (inf/-inf/nan) must NOT defeat the dead-man:
            # a -inf stamp would yield a -inf age (always "alive"). Treat it as unreadable ->
            # +inf age -> fail closed (the switch fires).
            return None
        return counter, ts

    def read_counter(self):
        rec = self._read()
        return None if rec is None else rec[0]

    def last_beat_age(self, now):
        rec = self._read()
        if rec is None:
            return math.inf       # never beaten / unreadable -> infinitely stale (fail closed)
        return now - rec[1]

    def is_alive(self, now, *, timeout):
        return self.last_beat_age(now) <= timeout
