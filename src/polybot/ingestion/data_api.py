"""Polymarket Data API poller (REST: /trades /positions /holders /leaderboard /activity).

The deterministic core: given an injected async ``fetch(path, params) -> list``,
stamp and persist each returned item as a canonical Envelope. The continuous
poll loop + rate limiting (positions ~150 req/10s) wrap this; the production
fetch is a thin httpx call. Idempotent on the item id so re-polling overlapping
windows does not double-record.
"""


import json

from polybot.core.models import Envelope

_MARKET_LINK_KEYS = ("conditionId", "asset", "market")
_ITEM_ID_KEYS = ("id", "transactionHash", "proxyWallet")


class DataApiPoller:
    def __init__(self, fetch, stamper, store, source="data-api"):
        self._fetch = fetch  # async (path, params) -> list[dict]
        self._stamper = stamper
        self._store = store
        self._source = source

    async def poll_once(self, path, *, params=None, source_tier="DATA"):
        """Fetch and persist ONE page. The caller (continuous loop, deferred)
        advances pagination via next_cursor; a single call is one page."""
        items = self._as_list(await self._fetch(path, params or {}))
        persisted = 0
        for item in items:
            item_id = self._item_id(item)
            if item_id is None:
                continue  # malformed item: skip so one bad row never wedges the page
            self._store.append(
                Envelope(
                    source=self._source,
                    source_tier=source_tier,
                    event_id=f"{path}:{item_id}",
                    observed_at=self._stamper.stamp(),
                    content=json.dumps(item, sort_keys=True, default=str),
                    published_at=self._published_at(item),
                    market_links=self._market_links(item),
                )
            )
            persisted += 1
        return persisted

    @staticmethod
    def _as_list(response):
        if isinstance(response, list):
            return response
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            return response["data"]  # paginated {"data": [...], "next_cursor": ...}
        raise TypeError(
            f"Data API response is not a list or {{'data': [...]}}: {type(response).__name__}"
        )

    @staticmethod
    def _item_id(item):
        for key in _ITEM_ID_KEYS:
            if item.get(key) is not None:
                return item[key]
        return None

    @staticmethod
    def _market_links(item):
        return tuple(item[key] for key in _MARKET_LINK_KEYS if key in item)

    @staticmethod
    def _published_at(item):
        ts = item.get("timestamp")
        if ts is None:
            return None
        try:
            return int(ts)
        except (TypeError, ValueError):
            return None
