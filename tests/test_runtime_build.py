import asyncio
import pytest
from polybot.runtime.config import IngestionConfig
from polybot.runtime.ingestion import IngestionRuntime, build_ingestion_runtime, _supervised


def test_supervised_normal_return_becomes_halt():
    async def returns_immediately():
        return
    async def scenario():
        await _supervised("x", returns_immediately)()
    with pytest.raises(RuntimeError, match="returned unexpectedly"):
        asyncio.run(scenario())


class _FakeWriter:
    def __init__(self): self.close_calls = 0
    def close(self): self.close_calls += 1


def test_supervised_forever_service_stops_cleanly_under_runtime():
    # a _supervised-wrapped forever service must be cancellable on stop WITHOUT firing the HALT guard
    async def forever():
        await asyncio.sleep(3600)
    w = _FakeWriter()
    async def scenario():
        rt = IngestionRuntime(services=[_supervised("fv", forever)], writer=w)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)   # clean stop, NO RuntimeError from the guard
    asyncio.run(scenario())
    assert w.close_calls == 1


def _rows():
    return [{"conditionId": "c1", "acceptingOrders": True, "volume24hr": 9.0,
             "active": True, "closed": False,
             "clobTokenIds": '["t1", "t2"]', "outcomes": '["Yes", "No"]',
             "outcomePrices": '["0.5", "0.5"]'}]


def test_build_wires_services_and_store(tmp_path):
    cfg = IngestionConfig(db_path=str(tmp_path / "m.db"), universe_max_markets=5)
    rt = build_ingestion_runtime(cfg, gamma_fetch=lambda params: _rows(),
                                 ws_connect=object(), data_fetch=object())
    assert isinstance(rt, IngestionRuntime)
    assert len(rt._services) == 2                       # ws + data-api (both _supervised-wrapped)
    assert (tmp_path / "m.db").exists()                 # EventStore created at db_path


def test_build_omits_data_api_when_disabled(tmp_path):
    cfg = IngestionConfig(db_path=str(tmp_path / "m.db"), universe_max_markets=5, data_api_enabled=False)
    rt = build_ingestion_runtime(cfg, gamma_fetch=lambda params: _rows(),
                                 ws_connect=object(), data_fetch=object())
    assert len(rt._services) == 1                       # ws only


def test_build_supervises_all_services(tmp_path):
    cfg = IngestionConfig(db_path=str(tmp_path / "m.db"), universe_max_markets=5)
    rt = build_ingestion_runtime(cfg, gamma_fetch=lambda params: _rows(),
                                 ws_connect=object(), data_fetch=object())
    assert rt._services                                              # non-empty
    # every service must be a _supervised-wrapped factory (the fail-loud HALT guard is actually applied)
    assert all("_supervised" in s.__qualname__ for s in rt._services)


def test_main_builds_and_runs_then_clean_exit(tmp_path, monkeypatch):
    from polybot.runtime import ingestion
    toml = tmp_path / "c.toml"
    toml.write_text(f'db_path = "{tmp_path / "m.db"}"\n')
    class _FakeRuntime:
        def __init__(self): self.ran = False
        def request_stop(self): pass
        async def run(self): self.ran = True
    fake = _FakeRuntime()
    monkeypatch.setattr(ingestion, "build_ingestion_runtime", lambda cfg: fake)
    rc = ingestion.main(["--config", str(toml)])
    assert rc == 0 and fake.ran is True

def test_main_missing_config_file_fails_loud(tmp_path):
    from polybot.runtime import ingestion
    with pytest.raises(FileNotFoundError):
        ingestion.main(["--config", str(tmp_path / "nope.toml")])


def test_main_returns_1_on_halt(tmp_path, monkeypatch):
    from polybot.runtime import ingestion
    toml = tmp_path / "c.toml"
    toml.write_text(f'db_path = "{tmp_path / "m.db"}"\n')
    class _HaltingRuntime:
        def request_stop(self): pass
        async def run(self):
            raise RuntimeError("boom")   # a venue HALT surfacing out of run()
    monkeypatch.setattr(ingestion, "build_ingestion_runtime", lambda cfg: _HaltingRuntime())
    rc = ingestion.main(["--config", str(toml)])
    assert rc == 1                       # HALT -> non-zero for systemd Restart=on-failure


from polybot.core.models import Envelope
from polybot.storage.event_writer import QueuedEventWriter
from polybot.storage.market_memory import EventStore

def test_end_to_end_durable_persist_and_close(tmp_path):
    db = str(tmp_path / "e2e.db")
    writer = QueuedEventWriter(EventStore(db, check_same_thread=False))
    async def producer():                       # stands in for a collector: append one durable row
        writer.append(Envelope(source="test", source_tier="VENUE", event_id="e1",
                               observed_at=1, content="{}", published_at=None,
                               market_links=("t1",)))
        await asyncio.sleep(3600)
    async def scenario():
        rt = IngestionRuntime(services=[producer], writer=writer)
        task = asyncio.create_task(rt.run())
        await asyncio.sleep(0.05)
        rt.request_stop()
        await asyncio.wait_for(task, timeout=1)  # graceful close drains + joins the writer
    asyncio.run(scenario())
    with EventStore(db) as store:                # fresh main-thread connection after the writer joined
        rows = store.all()
    assert len(rows) == 1 and rows[0].event_id == "e1"
