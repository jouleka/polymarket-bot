"""Persistence sink: MarketStream Observation -> Market-Memory store.

Adapts the dispatcher's Observation into a canonical Envelope and appends it to
an EventStore, so live market data is captured durably from day one (it cannot
be backfilled). Each frame is a distinct point-in-time observation keyed on the
unique observed_at — NO content dedup, so an identical book state seen twice (a
revert, or a reconnect snapshot) is preserved rather than silently dropped.
"""


import json

from polybot.core.models import Envelope


class PersistingSink:
    def __init__(self, store, source="clob-ws", source_tier="VENUE"):
        self._store = store
        self._source = source
        self._source_tier = source_tier

    def __call__(self, observation):
        message = observation.message
        self._store.append(
            Envelope(
                source=self._source,
                source_tier=self._source_tier,
                # Every streamed frame is a distinct point-in-time observation:
                # key on the unique observed_at so an identical book state seen
                # again is recorded, not silently dropped (no content dedup here).
                event_id=f"{observation.asset_id}:{observation.event_type}:{observation.observed_at}",
                observed_at=observation.observed_at,
                content=json.dumps(message, sort_keys=True, default=str),
                published_at=self._published_at(message),
                market_links=(observation.asset_id,),
            )
        )

    @staticmethod
    def _published_at(message):
        ts = message.get("timestamp")
        if ts is None:
            return None
        try:
            return int(ts)
        except (TypeError, ValueError):
            return None
