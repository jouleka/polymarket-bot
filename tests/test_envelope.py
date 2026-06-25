"""Tests for the canonical ingestion envelope (POL-3 / S1).

All externally-sourced content is UNTRUSTED data, never instructions. The
envelope encodes that default and stamps a monotonic observed_at at ingestion.
"""

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.envelope import make_envelope


def test_ingested_content_is_untrusted_by_default():
    stamper = MonotonicStamper(clock=lambda: 42)

    env = make_envelope(
        stamper,
        source="reuters",
        source_tier="A",
        event_id="evt-1",
        content="headline text",
    )

    assert env.trust == "UNTRUSTED"
    assert env.source == "reuters"
    assert env.source_tier == "A"
    assert env.event_id == "evt-1"
    assert env.content == "headline text"
    assert env.observed_at == 42
    assert env.published_at is None
    assert env.entities == ()
    assert env.market_links == ()


def test_successive_envelopes_carry_increasing_observed_at():
    stamper = MonotonicStamper(clock=lambda: 5)  # frozen underlying clock

    first = make_envelope(stamper, source="s", source_tier="A", event_id="1", content="a")
    second = make_envelope(stamper, source="s", source_tier="A", event_id="2", content="b")

    assert first.observed_at < second.observed_at


def test_entities_and_market_links_are_captured_as_tuples():
    stamper = MonotonicStamper(clock=lambda: 7)

    env = make_envelope(
        stamper,
        source="ap",
        source_tier="A",
        event_id="evt-2",
        content="Fed holds rates",
        published_at=1719331200000,
        entities=["FOMC", "interest-rates"],
        market_links=["0xcond1"],
    )

    assert env.published_at == 1719331200000
    assert env.entities == ("FOMC", "interest-rates")
    assert env.market_links == ("0xcond1",)
