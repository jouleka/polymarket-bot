"""POL-15 two-provider resolution feed."""

from dataclasses import replace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.feed import PollDisposition, ResolutionFeed
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    ProviderObservation,
    ResolutionSubject,
)
from polybot.resolution.store import ResolutionStore


class _Provider:
    def __init__(self, provider_id, *, chain=137, head=None, block_hash=None,
                 observation=None):
        self.provider_id = provider_id
        self._chain = chain
        self._head = head
        self._block_hash = block_hash
        self._observation = observation
        self.head_calls = 0
        self.hash_calls = []
        self.observe_calls = []

    def chain_id(self):
        return self._chain

    def latest_block(self):
        self.head_calls += 1
        if self._head is None:
            raise AssertionError("head must not be read after a chain mismatch")
        return self._head

    def block_hash(self, block_number):
        self.hash_calls.append(block_number)
        return self._block_hash

    def observe(self, subject, block_number):
        self.observe_calls.append((subject, block_number))
        if isinstance(self._observation, Exception):
            raise self._observation
        return self._observation


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


def test_feed_uses_lower_head_minus_exactly_five(tmp_path):
    agreed_hash = "0x" + "11" * 32
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        low = _Provider("archive-a", head=20, block_hash=agreed_hash)
        high = _Provider("archive-b", head=22, block_hash=agreed_hash)
        result, = ResolutionFeed(store, (low, high)).poll((_subject(),))
        assert low.hash_calls == high.hash_calls == [15]
        assert result.disposition is PollDisposition.UNAVAILABLE

        negative_a = _Provider("negative-a", head=4, block_hash=agreed_hash)
        negative_b = _Provider("negative-b", head=10, block_hash=agreed_hash)
        result, = ResolutionFeed(store, (negative_a, negative_b)).poll((_subject("82"),))
        assert result.disposition is PollDisposition.UNAVAILABLE
        assert negative_a.hash_calls == negative_b.hash_calls == []

        disagree_a = _Provider("disagree-a", head=20, block_hash=agreed_hash)
        disagree_b = _Provider("disagree-b", head=20, block_hash="0x" + "22" * 32)
        result, = ResolutionFeed(store, (disagree_a, disagree_b)).poll((_subject("83"),))
        assert result.disposition is PollDisposition.UNAVAILABLE
        assert disagree_a.hash_calls == disagree_b.hash_calls == [15]
        assert store.assessment_for(_subject().condition_id) is None
        assert store.pending_outbox(10) == ()


def test_poll_persists_matching_unresolved_assessment(tmp_path):
    subject = _subject("84")
    block_hash = "0x" + "33" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.UNRESOLVED, payout=None, dispute=DisputeState.UNKNOWN,
        collateral_address=None, derived_token_ids=None, adapter_address=None,
        question_id=None, audit_event_ids=(),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash, observation=observation
    )
    second = _Provider(
        "archive-b", head=22, block_hash=block_hash,
        observation=replace(observation, provider_id="archive-b"),
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        result, = ResolutionFeed(store, (first, second)).poll((subject,))
        assert result.disposition is PollDisposition.UNRESOLVED
        assert result.dispute is DisputeState.UNKNOWN
        assert result.terminal_id is None
        assessment = store.assessment_for(subject.condition_id)
        assert assessment.subject == subject
        assert assessment.phase is LifecyclePhase.UNRESOLVED
        assert assessment.dispute is DisputeState.UNKNOWN and assessment.payout is None
        assert (assessment.block_number, assessment.block_hash) == (15, block_hash)
        assert store.terminal_for(subject.condition_id) is None
        assert store.pending_outbox(10) == ()
