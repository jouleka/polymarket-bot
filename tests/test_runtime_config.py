import math
import pytest
from polybot.runtime.config import IngestionConfig

def test_valid_config_defaults():
    c = IngestionConfig(db_path="/data/m.db")
    assert c.db_path == "/data/m.db"
    assert c.universe_max_markets == 400
    assert c.max_assets_per_shard == 500
    assert c.data_api_enabled is True
    assert c.data_api_interval_seconds == 2.0
    assert c.snapshot_interval_seconds == 60.0
    assert c.heartbeat_path is None
    assert c.log_level == "INFO"


@pytest.mark.parametrize("kwargs", [
    {"db_path": ""},                                    # empty path
    {"db_path": "/d", "universe_max_markets": 0},       # < 1
    {"db_path": "/d", "max_assets_per_shard": 0},       # < 1
    {"db_path": "/d", "data_api_limit": 0},             # < 1
    {"db_path": "/d", "data_api_interval_seconds": 0},  # not > 0
    {"db_path": "/d", "data_api_interval_seconds": math.inf},   # not finite
    {"db_path": "/d", "snapshot_interval_seconds": 0},          # not > 0
    {"db_path": "/d", "snapshot_interval_seconds": -1},         # not > 0
    {"db_path": "/d", "snapshot_interval_seconds": math.inf},   # not finite
    {"db_path": "/d", "snapshot_interval_seconds": math.nan},   # not finite
    {"db_path": "/d", "heartbeat_interval_seconds": -1},        # not > 0
    {"db_path": "/d", "log_level": "LOUD"},             # unknown level
])
def test_invalid_config_raises(kwargs):
    with pytest.raises(ValueError):
        IngestionConfig(**kwargs)


def test_load_config_toml_then_env(tmp_path):
    from polybot.runtime.config import load_config   # local: undefined until Step 1.11 -> keeps this RED isolated
    toml = tmp_path / "ingest.toml"
    toml.write_text('db_path = "/from/toml.db"\nuniverse_max_markets = 50\n')
    cfg = load_config(str(toml), env={"POLYBOT_INGEST_UNIVERSE_MAX_MARKETS": "77",
                                      "POLYBOT_INGEST_DATA_API_ENABLED": "false"})
    assert cfg.db_path == "/from/toml.db"      # from toml
    assert cfg.universe_max_markets == 77      # env overrode toml
    assert cfg.data_api_enabled is False       # env-coerced bool
    assert cfg.max_assets_per_shard == 500     # default

def test_load_config_env_only():
    from polybot.runtime.config import load_config
    cfg = load_config(None, env={"POLYBOT_INGEST_DB_PATH": "/env.db"})
    assert cfg.db_path == "/env.db"

def test_load_config_invalid_still_fails_loud():
    from polybot.runtime.config import load_config
    with pytest.raises(ValueError):
        load_config(None, env={"POLYBOT_INGEST_DB_PATH": "/e.db",
                               "POLYBOT_INGEST_UNIVERSE_MAX_MARKETS": "0"})
