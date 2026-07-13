"""POL-15 two-provider resolution feed."""

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.feed import PollDisposition, ResolutionFeed
from polybot.resolution.models import ResolutionSubject
from polybot.resolution.store import ResolutionStore


class _Provider:
    def __init__(self, provider_id, *, chain=137):
        self.provider_id = provider_id
        self._chain = chain
        self.head_calls = 0

    def chain_id(self):
        return self._chain

    def latest_block(self):
        self.head_calls += 1
        raise AssertionError("head must not be read after a chain mismatch")


def _subject(condition_byte="81"):
    return ResolutionSubject(
        "event-1", "0x" + condition_byte * 32, ("101", "202"), "politics"
    )


def test_feed_requires_exactly_two_distinct_polygon_providers(tmp_path):
    path = str(tmp_path / "resolution.db")
    with ResolutionStore(path, MonotonicStamper()) as store:
        for providers in (
            (_Provider("only"),),
            (_Provider("same"), _Provider("same")),
            (_Provider(""), _Provider("other")),
        ):
            with pytest.raises(ValueError, match="provider"):
                ResolutionFeed(store, providers)

        first = _Provider("archive-a", chain=137)
        wrong_chain = _Provider("archive-b", chain=1)
        feed = ResolutionFeed(store, (first, wrong_chain))
        result, = feed.poll((_subject(),))
        assert result.disposition is PollDisposition.UNAVAILABLE
        assert "chain" in result.detail
        assert first.head_calls == wrong_chain.head_calls == 0
        assert store.assessment_for(_subject().condition_id) is None
        assert store.terminal_for(_subject().condition_id) is None
        assert store.pending_outbox(10) == ()

