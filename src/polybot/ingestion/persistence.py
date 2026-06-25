"""Persistence sink: MarketStream Observation -> Market-Memory store.

Adapts the dispatcher's Observation into a canonical Envelope and appends it to
an EventStore, so live market data is captured durably from day one (it cannot
be backfilled). Uses a stable frame hash/timestamp as the dedup key when present
so a reconnect snapshot or re-delivered frame is not double-recorded.
"""


import json

from polybot.core.models import Envelope


class PersistingSink:
    def __init__(self, store, source="clob-ws", source_tier="VENUE"):
        self._store = store
        self._source = source
        self._source_tier = source_tier

    def __call__(self, observation):
        self._store.append(
            Envelope(
                source=self._source,
                source_tier=self._source_tier,
                event_id=self._event_id(observation),
                observed_at=observation.observed_at,
                content=json.dumps(observation.message, sort_keys=True),
                market_links=(observation.asset_id,),
            )
        )

    @staticmethod
    def _event_id(observation):
        message = observation.message
        # Prefer a stable id from the frame so a re-delivered snapshot dedups;
        # fall back to the (unique) observed_at when the frame carries none.
        stable = message.get("hash") or message.get("timestamp")
        suffix = stable if stable is not None else observation.observed_at
        return f"{observation.asset_id}:{observation.event_type}:{suffix}"
