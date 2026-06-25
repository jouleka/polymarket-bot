"""Canonical ingestion envelope factory.

Wraps every external observation in a uniform, UNTRUSTED-by-default envelope and
stamps a strictly-increasing ``observed_at`` via the injected stamper.
"""

from polybot.core.models import Envelope


def make_envelope(
    stamper,
    *,
    source,
    source_tier,
    event_id,
    content,
    published_at=None,
    entities=(),
    market_links=(),
):
    return Envelope(
        source=source,
        source_tier=source_tier,
        event_id=event_id,
        observed_at=stamper.stamp(),
        content=content,
        published_at=published_at,
        entities=tuple(entities),
        market_links=tuple(market_links),
    )
