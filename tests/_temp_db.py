from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory


_ROOT = TemporaryDirectory(prefix="polybot-tests-")
_NEXT_ID = count()


def temporary_db_path() -> str:
    """Return a unique path inside a process-private temporary directory."""
    return str(Path(_ROOT.name) / f"events-{next(_NEXT_ID)}.db")
