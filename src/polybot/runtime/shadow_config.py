"""Self-verifying configuration for the paper-only POL-17 composition root."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import tomllib
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

    @property
    def authority_host(self):
        return urlsplit(self.url).hostname.lower()


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
    news_poll_seconds: float = 60.0
    rpc_timeout_seconds: float = 15.0
    readiness_timeout_seconds: float = 60.0
    outbox_batch_limit: int = 100
    status_path: str = "/run/polybot/shadow-status.json"
    paper_only: bool = field(default=True, init=False)

    def __post_init__(self):
        if not isinstance(self.ingestion, IngestionConfig):
            raise TypeError("ingestion must be an IngestionConfig")
        paths = self.database_paths
        if any(not isinstance(path, str) or not path or path != path.strip()
               for path in paths):
            raise ValueError("database paths must be non-empty exact strings")
        resolved_paths = tuple(Path(path).resolve(strict=False) for path in paths)
        if len(set(resolved_paths)) != len(resolved_paths):
            raise ValueError("every logical store requires a distinct database path")
        existing = [path for path in resolved_paths if path.exists()]
        for index, first in enumerate(existing):
            for second in existing[index + 1:]:
                if os.path.samefile(first, second):
                    raise ValueError(
                        "every logical store requires a distinct database path"
                    )
        if (not isinstance(self.status_path, str) or not self.status_path
                or self.status_path != self.status_path.strip()):
            raise ValueError("status_path must be a non-empty exact string")
        if Path(self.status_path).resolve(strict=False) in resolved_paths:
            raise ValueError("status_path must not alias a database path")
        providers = self.polygon_providers
        if (not isinstance(providers, tuple) or len(providers) != 2
                or any(not isinstance(provider, ReadOnlyPolygonProviderConfig)
                       for provider in providers)):
            raise ValueError("exactly two read-only Polygon providers are required")
        if providers[0].provider_id == providers[1].provider_id:
            raise ValueError("Polygon provider IDs must be distinct")
        if providers[0].url == providers[1].url:
            raise ValueError("Polygon provider URLs must be distinct")
        if providers[0].authority_host == providers[1].authority_host:
            raise ValueError("Polygon provider authorities must use distinct hosts")
        for name in (
            "cycle_interval_seconds",
            "registry_refresh_seconds",
            "registry_max_age_seconds",
            "resolution_poll_seconds",
            "news_poll_seconds",
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


def load_shadow_config(toml_path, *, env: Mapping[str, str] = os.environ):
    """Load flat D4a keys plus one strict ``[shadow]`` composition table."""
    with open(toml_path, "rb") as handle:
        loaded = tomllib.load(handle)
    ingestion_names = {item.name for item in dataclasses.fields(IngestionConfig)}
    unknown_top = set(loaded) - ingestion_names - {"shadow"}
    if unknown_top:
        raise ValueError(f"unknown runtime config keys: {sorted(unknown_top)!r}")
    shadow = loaded.get("shadow")
    if not isinstance(shadow, dict):
        raise ValueError("runtime config requires a [shadow] table")

    ingestion_values = {name: loaded[name] for name in ingestion_names if name in loaded}
    for name in ingestion_names:
        key = "POLYBOT_INGEST_" + name.upper()
        if key in env:
            ingestion_values[name] = _coerce_ingestion(name, env[key])
    ingestion = IngestionConfig(**ingestion_values)

    shadow_names = {
        item.name for item in dataclasses.fields(ShadowRuntimeConfig)
        if item.init
    } - {"ingestion", "polygon_providers"}
    unknown_shadow = set(shadow) - shadow_names - {"polygon_providers"}
    if unknown_shadow:
        raise ValueError(f"unknown shadow config keys: {sorted(unknown_shadow)!r}")
    provider_rows = shadow.get("polygon_providers")
    if not isinstance(provider_rows, list):
        raise ValueError("shadow.polygon_providers must be an array of tables")
    providers = []
    for row in provider_rows:
        if not isinstance(row, dict) or set(row) != {"provider_id", "url"}:
            raise ValueError("each Polygon provider requires only provider_id and url")
        providers.append(ReadOnlyPolygonProviderConfig(**row))

    values = {name: shadow[name] for name in shadow_names if name in shadow}
    for name in shadow_names:
        key = "POLYBOT_SHADOW_" + name.upper()
        if key in env:
            values[name] = _coerce_shadow(name, env[key])
    for index, label in enumerate(("A", "B")):
        id_key = f"POLYBOT_SHADOW_PROVIDER_{label}_ID"
        url_key = f"POLYBOT_SHADOW_PROVIDER_{label}_URL"
        if id_key in env or url_key in env:
            if len(providers) != 2:
                raise ValueError("provider env overrides require two configured providers")
            providers[index] = ReadOnlyPolygonProviderConfig(
                env.get(id_key, providers[index].provider_id),
                env.get(url_key, providers[index].url),
            )
    return ShadowRuntimeConfig(
        ingestion=ingestion,
        polygon_providers=tuple(providers),
        **values,
    )


_INGEST_INT = {"universe_max_markets", "max_assets_per_shard", "data_api_limit"}
_INGEST_FLOAT = {
    "data_api_interval_seconds", "snapshot_interval_seconds",
    "heartbeat_interval_seconds",
}
_SHADOW_FLOAT = {
    "cycle_interval_seconds", "registry_refresh_seconds",
    "registry_max_age_seconds", "resolution_poll_seconds",
    "news_poll_seconds",
    "rpc_timeout_seconds", "readiness_timeout_seconds",
}


def _coerce_ingestion(name, raw):
    if name in _INGEST_INT:
        return int(raw)
    if name in _INGEST_FLOAT:
        return float(raw)
    if name == "data_api_enabled":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw


def _coerce_shadow(name, raw):
    if name == "outbox_batch_limit":
        return int(raw)
    if name in _SHADOW_FLOAT:
        return float(raw)
    return raw
