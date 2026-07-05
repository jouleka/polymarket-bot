"""Manual live smoke for the ingestion runtime (POL-13 / D4a). Read-only; discovers a few live markets,
runs the real runtime for ~8s, asserts rows landed. Run: ./.venv/bin/python scripts/ingestion_runtime_check.py"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import build_ingestion_runtime
from polybot.storage.market_memory import EventStore


async def main():
    db = tempfile.mktemp(suffix=".db")
    cfg = IngestionConfig(db_path=db, universe_max_markets=5, data_api_interval_seconds=2.0)
    rt = build_ingestion_runtime(cfg)
    task = asyncio.create_task(rt.run())
    await asyncio.sleep(8)
    rt.request_stop()
    await asyncio.wait_for(task, timeout=5)
    with EventStore(db) as store:
        rows = store.all()
    print(f"persisted {len(rows)} rows; sources={sorted({r.source for r in rows})}")
    assert rows, "no rows captured — check connectivity / venue"


if __name__ == "__main__":
    asyncio.run(main())
