"""Ingestion runtime config — the FIRST config surface in the repo (every component is otherwise pure-DI).
Frozen + self-verifying (fail LOUD at construction); a thin TOML+env loader overlays it."""
from __future__ import annotations

import dataclasses
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from polybot.ingestion.transport import GAMMA_URL

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


@dataclass(frozen=True)
class IngestionConfig:
    db_path: str
    universe_max_markets: int = 400
    max_assets_per_shard: int = 500
    data_api_enabled: bool = True
    data_api_interval_seconds: float = 2.0
    data_api_limit: int = 500
    heartbeat_path: str | None = None
    heartbeat_interval_seconds: float = 5.0
    gamma_url: str = GAMMA_URL
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.db_path:
            raise ValueError("db_path must be non-empty")
        if self.universe_max_markets < 1:
            raise ValueError("universe_max_markets must be >= 1")
        if self.max_assets_per_shard < 1:
            raise ValueError("max_assets_per_shard must be >= 1")
        if self.data_api_limit < 1:
            raise ValueError("data_api_limit must be >= 1")
        for name in ("data_api_interval_seconds", "heartbeat_interval_seconds"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0")
        if self.log_level not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_LOG_LEVELS)}")


_ENV_PREFIX = "POLYBOT_INGEST_"
_INT_FIELDS = {"universe_max_markets", "max_assets_per_shard", "data_api_limit"}
_FLOAT_FIELDS = {"data_api_interval_seconds", "heartbeat_interval_seconds"}
_BOOL_FIELDS = {"data_api_enabled"}


def _coerce(field_name: str, raw: str):
    if field_name in _INT_FIELDS:
        return int(raw)
    if field_name in _FLOAT_FIELDS:
        return float(raw)
    if field_name in _BOOL_FIELDS:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw  # str fields: db_path, heartbeat_path, gamma_url, log_level


def load_config(toml_path: str | None = None, *, env: Mapping[str, str] = os.environ) -> IngestionConfig:
    """Overlay: defaults <- optional TOML file <- POLYBOT_INGEST_* env vars, then IngestionConfig(**merged)
    (which self-verifies). The ONLY place env/files are read — every collector stays pure-DI."""
    field_names = {f.name for f in dataclasses.fields(IngestionConfig)}
    values: dict = {}
    if toml_path:
        with open(toml_path, "rb") as fh:
            loaded = tomllib.load(fh)
        for key, val in loaded.items():
            if key not in field_names:
                raise ValueError(f"unknown ingestion config key in {toml_path}: {key!r}")
            values[key] = val
    for name in field_names:
        env_key = _ENV_PREFIX + name.upper()
        if env_key in env:
            values[name] = _coerce(name, env[env_key])
    return IngestionConfig(**values)
