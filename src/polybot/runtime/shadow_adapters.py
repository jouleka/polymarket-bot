"""Production network adapters owned by the POL-17 composition root."""

from __future__ import annotations

import httpx

from polybot.ingestion.gamma import normalize_market
from polybot.runtime.discovery import discover_universe


class _GammaSnapshotFetcher:
    def __init__(self, config, client, *, owned):
        self._config = config
        self._client = client
        self._owned = owned
        self._condition_ids = None

    def __call__(self):
        if self._condition_ids is None:
            params = {
                "limit": self._config.universe_max_markets * 3,
                "closed": "false",
                "active": "true",
                "order": "volume24hr",
                "ascending": "false",
            }
            candidates = self._get_list("/markets", params)
            selected_tokens = frozenset(discover_universe(
                lambda _params: candidates, self._config
            ))
            markets = []
            for row in candidates:
                try:
                    tokens = {outcome.token_id for outcome in normalize_market(row).outcomes}
                except Exception:
                    continue
                if tokens and tokens <= selected_tokens:
                    markets.append(row)
            self._condition_ids = tuple(row["conditionId"] for row in markets)
        else:
            markets = self._get_list(
                "/markets", {"condition_ids": self._condition_ids}
            )
        event_ids = tuple(dict.fromkeys(
            str(event["id"])
            for market in markets
            for event in market["events"]
        ))
        events = self._get_list("/events", {"id": event_ids})
        return markets, events

    def _get_list(self, path, params):
        response = self._client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError(f"Gamma {path} response must be a list")
        return payload

    def close(self):
        if self._owned:
            self._client.close()


def make_gamma_snapshot_fetch(config, *, client=None, timeout=30.0):
    """Return a callable coherent snapshot that freezes its first selected universe."""
    owned = client is None
    if client is None:
        client = httpx.Client(
            base_url=config.gamma_url,
            timeout=timeout,
            headers={"user-agent": "polybot/0.1"},
        )
    return _GammaSnapshotFetcher(config, client, owned=owned)
