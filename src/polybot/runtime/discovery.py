"""Market-universe discovery: Gamma /markets -> top-N active binary tradeable markets by 24h volume ->
the flat, de-duplicated clobTokenIds the sharded WS collector subscribes to. Pure but for the injected `fetch`."""
from __future__ import annotations

import math

import httpx

from polybot.ingestion.gamma import normalize_market
from polybot.runtime.config import IngestionConfig


def _volume(raw) -> float:
    try:
        v = float(raw.get("volume24hr"))
        return v if math.isfinite(v) else 0.0   # NaN/inf -> sorts last, per docstring + non-finite-fails-closed doctrine
    except (TypeError, ValueError):
        return 0.0


def discover_universe(fetch, config: IngestionConfig) -> list[str]:
    rows = fetch({"limit": config.universe_max_markets * 3, "closed": "false",
                  "active": "true", "order": "volume24hr", "ascending": "false"})
    if not isinstance(rows, list):
        raise TypeError(f"Gamma /markets response is not a list: {type(rows).__name__}")
    ranked = []
    for raw in rows:
        if not raw.get("acceptingOrders"):
            continue
        try:
            market = normalize_market(raw)
        except Exception:
            continue  # individually-malformed row: skip so one bad market never breaks discovery
        if market.closed or not market.active or len(market.outcomes) != 2:
            continue
        ranked.append((_volume(raw), market))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    token_ids: list[str] = []
    seen: set[str] = set()
    for _vol, market in ranked[: config.universe_max_markets]:
        for outcome in market.outcomes:
            if outcome.token_id not in seen:
                seen.add(outcome.token_id)
                token_ids.append(outcome.token_id)
    if not token_ids:
        raise RuntimeError("discover_universe found no tradeable markets — possible Gamma format change")
    return token_ids


def make_gamma_fetch(gamma_url: str, *, timeout: float = 30.0):
    """Default production SYNC fetch(params) -> list[dict] for discover_universe (Gamma /markets).
    Kept here (not in transport.py) so transport stays untouched and the fetch stays injectable."""
    def fetch(params):
        resp = httpx.get(f"{gamma_url}/markets", params=params, timeout=timeout,
                         headers={"user-agent": "polybot/0.1"})
        resp.raise_for_status()
        return resp.json()
    return fetch
