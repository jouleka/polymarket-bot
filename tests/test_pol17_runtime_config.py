"""POL-17 composite runtime configuration."""

from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)


def test_shadow_runtime_config_pins_paper_only_distinct_persistence_and_providers():
    config = ShadowRuntimeConfig(
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
