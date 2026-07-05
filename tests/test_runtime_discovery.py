import pytest
from polybot.runtime.config import IngestionConfig
from polybot.runtime.discovery import discover_universe

def _market(cid, yes, no, vol, accepting=True):
    return {"conditionId": cid, "acceptingOrders": accepting, "volume24hr": vol,
            "active": True, "closed": False,
            "clobTokenIds": f'["{yes}", "{no}"]', "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]'}

def test_ranks_by_volume_and_caps(monkeypatch):
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=2)
    rows = [_market("c1", "t1a", "t1b", 10.0), _market("c2", "t2a", "t2b", 99.0),
            _market("c3", "t3a", "t3b", 50.0)]
    tokens = discover_universe(lambda params: rows, cfg)
    # top-2 by volume are c2 (99) then c3 (50); c1 (10) dropped. Order preserved, deduped.
    assert tokens == ["t2a", "t2b", "t3a", "t3b"]

def test_filters_non_accepting_and_non_binary():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    multi = {"conditionId": "m", "acceptingOrders": True, "volume24hr": 100.0,
             "active": True, "closed": False,
             "clobTokenIds": '["a", "b", "c"]', "outcomes": '["A", "B", "C"]',
             "outcomePrices": '["0.3", "0.3", "0.4"]'}          # 3 outcomes -> not binary
    rows = [_market("ok", "t1", "t2", 5.0), _market("no", "x1", "x2", 9.0, accepting=False), multi]
    assert discover_universe(lambda p: rows, cfg) == ["t1", "t2"]

def test_skips_individually_malformed_row():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    bad = {"conditionId": "bad", "acceptingOrders": True, "volume24hr": 999.0}  # missing clobTokenIds -> normalize raises
    rows = [bad, _market("ok", "t1", "t2", 1.0)]
    assert discover_universe(lambda p: rows, cfg) == ["t1", "t2"]

def test_dedupes_shared_token_across_markets():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    rows = [_market("c1", "shared", "t1b", 10.0), _market("c2", "shared", "t2b", 9.0)]
    tokens = discover_universe(lambda p: rows, cfg)
    assert tokens == ["shared", "t1b", "t2b"]     # 'shared' appears once (collector rejects dupes)

def test_empty_result_fails_loud():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    with pytest.raises(RuntimeError):
        discover_universe(lambda p: [{"acceptingOrders": False}], cfg)   # nothing tradeable

def test_non_list_response_fails_loud():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=10)
    with pytest.raises(TypeError):
        discover_universe(lambda p: {"unexpected": "shape"}, cfg)

def test_nan_volume_sorts_last():
    cfg = IngestionConfig(db_path="/d.db", universe_max_markets=1)
    nan_row = _market("nan", "bad1", "bad2", float("nan"))    # NaN volume, arrives FIRST
    real_row = _market("real", "good1", "good2", 42.0)
    tokens = discover_universe(lambda p: [nan_row, real_row], cfg)
    assert tokens == ["good1", "good2"]      # the real 42.0-vol market wins the single top-N slot

def test_make_gamma_fetch_hits_markets_endpoint(monkeypatch):
    from polybot.runtime import discovery
    captured = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return [{"ok": 1}]
    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url; captured["params"] = params
        return _Resp()
    monkeypatch.setattr(discovery.httpx, "get", fake_get)
    fetch = discovery.make_gamma_fetch("https://gamma.example")
    assert fetch({"limit": 3}) == [{"ok": 1}]
    assert captured["url"] == "https://gamma.example/markets"
    assert captured["params"] == {"limit": 3}
