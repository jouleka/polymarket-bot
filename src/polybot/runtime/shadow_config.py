"""Self-verifying configuration for the paper-only POL-17 composition root."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from urllib.parse import urlsplit

from polybot.runtime.config import IngestionConfig


@dataclass(frozen=True)
class ReadOnlyPolygonProviderConfig:
    provider_id: str
    url: str

    def __post_init__(self):
        if (not isinstance(self.provider_id, str) or not self.provider_id
                or self.provider_id != self.provider_id.strip()):
            raise ValueError("provider_id must be a non-empty exact string")
        if not isinstance(self.url, str) or not self.url or self.url != self.url.strip():
            raise ValueError("provider URL must be a non-empty exact string")
        parsed = urlsplit(self.url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("provider URL must be HTTPS without embedded user credentials")


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

    def __post_init__(self):
        if not isinstance(self.ingestion, IngestionConfig):
            raise TypeError("ingestion must be an IngestionConfig")
        paths = self.database_paths
        if any(not isinstance(path, str) or not path or path != path.strip()
               for path in paths):
            raise ValueError("database paths must be non-empty exact strings")
        if len(set(paths)) != len(paths):
            raise ValueError("every logical store requires a distinct database path")
        providers = self.polygon_providers
        if (not isinstance(providers, tuple) or len(providers) != 2
                or any(not isinstance(provider, ReadOnlyPolygonProviderConfig)
                       for provider in providers)):
            raise ValueError("exactly two read-only Polygon providers are required")
        if providers[0].provider_id == providers[1].provider_id:
            raise ValueError("Polygon provider IDs must be distinct")
        if providers[0].url == providers[1].url:
            raise ValueError("Polygon provider URLs must be distinct")
        for name in (
            "cycle_interval_seconds",
            "registry_refresh_seconds",
            "registry_max_age_seconds",
            "resolution_poll_seconds",
            "rpc_timeout_seconds",
            "readiness_timeout_seconds",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if self.registry_max_age_seconds < self.registry_refresh_seconds:
            raise ValueError("registry max age must not be shorter than refresh cadence")
        if (isinstance(self.outbox_batch_limit, bool)
                or not isinstance(self.outbox_batch_limit, int)
                or self.outbox_batch_limit <= 0):
            raise ValueError("outbox_batch_limit must be a positive integer")

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
