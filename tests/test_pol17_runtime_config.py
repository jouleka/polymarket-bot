"""POL-17 composite runtime configuration."""

import math
import os
from pathlib import Path

import pytest

from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
    load_shadow_config,
)


def _config(**overrides):
    values = dict(
        ingestion=IngestionConfig(db_path="/data/market_memory.db"),
        intents_db_path="/data/intents.db",
        forecasts_db_path="/data/forecasts.db",
        components_db_path="/data/components.db",
        maker_db_path="/data/maker.db",
        shadow_db_path="/data/shadow.db",
        resolution_db_path="/data/resolution.db",
        polygon_providers=(
            ReadOnlyPolygonProviderConfig("polygon-a", "https://polygon-a.example"),
            ReadOnlyPolygonProviderConfig("polygon-b", "https://polygon-b.example"),
        ),
    )
    values.update(overrides)
    return ShadowRuntimeConfig(**values)


def test_shadow_runtime_config_pins_paper_only_distinct_persistence_and_providers():
    config = _config()

    assert config.paper_only is True
    assert config.database_paths == (
        "/data/market_memory.db",
        "/data/intents.db",
        "/data/forecasts.db",
        "/data/components.db",
        "/data/maker.db",
        "/data/shadow.db",
        "/data/resolution.db",
    )
    assert [provider.provider_id for provider in config.polygon_providers] == [
        "polygon-a",
        "polygon-b",
    ]
    assert config.cycle_interval_seconds == 1.0
    assert config.registry_refresh_seconds == 300.0
    assert config.registry_max_age_seconds == 900.0
    assert config.resolution_poll_seconds == 60.0
    assert config.rpc_timeout_seconds == 15.0
    assert config.readiness_timeout_seconds == 60.0
    assert config.outbox_batch_limit > 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"shadow_db_path": "/data/maker.db"},
        {"intents_db_path": ""},
        {"polygon_providers": ()},
        {"polygon_providers": (
            ReadOnlyPolygonProviderConfig("same", "https://polygon-a.example"),
            ReadOnlyPolygonProviderConfig("same", "https://polygon-b.example"),
        )},
        {"polygon_providers": (
            ReadOnlyPolygonProviderConfig("a", "https://same.example"),
            ReadOnlyPolygonProviderConfig("b", "https://same.example"),
        )},
        {"polygon_providers": (
            ReadOnlyPolygonProviderConfig("a", "https://same.example"),
            ReadOnlyPolygonProviderConfig("b", "https://SAME.example:443/"),
        )},
        {"polygon_providers": lambda: (
            ReadOnlyPolygonProviderConfig("a", "http://polygon-a.example"),
            ReadOnlyPolygonProviderConfig("b", "https://polygon-b.example"),
        )},
        {"cycle_interval_seconds": 0},
        {"resolution_poll_seconds": math.inf},
        {"registry_refresh_seconds": 901, "registry_max_age_seconds": 900},
        {"outbox_batch_limit": True},
        {"outbox_batch_limit": 0},
    ],
)
def test_shadow_runtime_config_rejects_unsafe_or_ambiguous_values(overrides):
    with pytest.raises((TypeError, ValueError)):
        _config(**{
            name: value() if callable(value) else value
            for name, value in overrides.items()
        })


def test_load_shadow_config_composes_flat_ingestion_and_strict_shadow_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
db_path = "/data/market_memory.db"
universe_max_markets = 200
snapshot_interval_seconds = 60.0
data_api_enabled = true

[shadow]
intents_db_path = "/data/intents.db"
forecasts_db_path = "/data/forecasts.db"
components_db_path = "/data/components.db"
maker_db_path = "/data/maker.db"
shadow_db_path = "/data/shadow.db"
resolution_db_path = "/data/resolution.db"
cycle_interval_seconds = 1.0
registry_refresh_seconds = 300.0
registry_max_age_seconds = 900.0
resolution_poll_seconds = 60.0
rpc_timeout_seconds = 15.0
readiness_timeout_seconds = 60.0
outbox_batch_limit = 100

[[shadow.polygon_providers]]
provider_id = "polygon-a"
url = "https://polygon-a.example"

[[shadow.polygon_providers]]
provider_id = "polygon-b"
url = "https://polygon-b.example"
""".strip()
    )

    config = load_shadow_config(str(path), env={})

    assert config.ingestion.db_path == "/data/market_memory.db"
    assert config.ingestion.universe_max_markets == 200
    assert config.ingestion.snapshot_interval_seconds == 60.0
    assert config.intents_db_path == "/data/intents.db"
    assert config.polygon_providers[1].provider_id == "polygon-b"


def test_deploy_example_is_a_complete_paper_shadow_configuration():
    path = Path(__file__).parents[1] / "deploy" / "config.example.toml"

    config = load_shadow_config(str(path), env={})

    assert config.paper_only is True
    assert len(set(config.database_paths)) == 7
    assert len(config.polygon_providers) == 2


def test_shadow_config_rejects_normalized_symlink_and_hardlink_database_aliases(tmp_path):
    target = tmp_path / "role.db"
    target.touch()
    symlink = tmp_path / "role-symlink.db"
    symlink.symlink_to(target)
    hardlink = tmp_path / "role-hardlink.db"
    os.link(target, hardlink)
    aliases = (
        str(tmp_path) + "/./role.db",
        str(symlink),
        str(hardlink),
    )

    for alias in aliases:
        with pytest.raises(ValueError, match="distinct database"):
            _config(
                maker_db_path=str(target),
                shadow_db_path=alias,
            )
