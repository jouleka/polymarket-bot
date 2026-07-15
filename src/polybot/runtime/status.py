"""Advisory atomic health evidence for the supervised paper runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time


class RuntimeStatusReporter:
    def __init__(self, path, *, readiness, clock=time.time):
        self._path = Path(path)
        self._readiness = readiness
        self._clock = clock

    def update(self, payload):
        if not isinstance(payload, dict):
            raise TypeError("runtime status payload must be a dict")
        document = dict(payload)
        document["updated_at"] = self._clock()
        fd, temporary = tempfile.mkstemp(
            prefix=self._path.name + ".", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        status = getattr(self._readiness, "status", None)
        if callable(status):
            status(
                f"{document.get('controller', 'UNKNOWN')}; "
                f"pending={document.get('pending_intents', 'UNKNOWN')}"
            )
        return document
