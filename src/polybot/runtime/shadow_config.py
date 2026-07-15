"""Self-verifying configuration for the paper-only POL-17 composition root."""

from __future__ import annotations

from dataclasses import dataclass, field

from polybot.runtime.config import IngestionConfig


@dataclass(frozen=True)
class ReadOnlyPolygonProviderConfig:
    provider_id: str
    url: str


@dataclass(frozen=True)
class ShadowRuntimeConfig:
    ingestion: IngestionConfig
    intents_db_path: str
    forecasts_db_path: str
    components_db_path: str
    maker_db_path: str
    shadow_db_path: str
    resolution_db_path: str
    polygon_providers: tuple[ReadOnlyPolygonProviderConfig, ReadOnlyPolygonProviderConfig]
    cycle_interval_seconds: float = 1.0
    registry_refresh_seconds: float = 300.0
    registry_max_age_seconds: float = 900.0
    resolution_poll_seconds: float = 60.0
    rpc_timeout_seconds: float = 15.0
    readiness_timeout_seconds: float = 60.0
    outbox_batch_limit: int = 100
    paper_only: bool = field(default=True, init=False)

    @property
    def database_paths(self):
        return (
            self.ingestion.db_path,
            self.intents_db_path,
            self.forecasts_db_path,
            self.components_db_path,
            self.maker_db_path,
            self.shadow_db_path,
            self.resolution_db_path,
        )
