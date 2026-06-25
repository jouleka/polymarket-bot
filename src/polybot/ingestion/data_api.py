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
        items = await self._fetch(path, params or {})
        for item in items:
            self._store.append(
                Envelope(
                    source=self._source,
                    source_tier=source_tier,
                    event_id=f"{path}:{self._item_id(item)}",
                    observed_at=self._stamper.stamp(),
                    content=json.dumps(item, sort_keys=True),
                    market_links=self._market_links(item),
                )
            )
        return len(items)

    @staticmethod
    def _item_id(item):
        for key in _ITEM_ID_KEYS:
            if item.get(key) is not None:
                return item[key]
        raise ValueError(f"Data API item has no id key {_ITEM_ID_KEYS}: {item!r}")

    @staticmethod
    def _market_links(item):
        for key in _MARKET_LINK_KEYS:
            if key in item:
                return (item[key],)
        return ()
