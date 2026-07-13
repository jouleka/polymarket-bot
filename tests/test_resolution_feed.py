"""POL-15 two-provider resolution feed."""

from dataclasses import replace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.errors import (
    IntegrityHalted,
    ResolutionUnavailable,
    SettlementConflict,
)
from polybot.resolution.feed import PollDisposition, ResolutionFeed
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PUSD_ADDRESS,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionStore


class _Provider:
    def __init__(self, provider_id, *, chain=137, head=None, block_hash=None,
                 observation=None, verification_error=None):
        self.provider_id = provider_id
        self._chain = chain
        self._head = head
        self._block_hash = block_hash
        self._observation = observation
        self._verification_error = verification_error
        self.head_calls = 0
        self.hash_calls = []
        self.observe_calls = []
        self.verify_calls = []

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
        observation = (
            self._observation.get(subject.condition_id)
            if isinstance(self._observation, dict) else self._observation
        )
        if isinstance(observation, Exception):
            raise observation
        return observation

    def verify_terminal(self, terminal):
        self.verify_calls.append(terminal)
        if self._verification_error is not None:
            raise self._verification_error


def _subject(condition_byte="81"):
    return ResolutionSubject(
        "event-1", "0x" + condition_byte * 32, ("101", "202"), "politics"
    )


def _terminal(condition_byte):
    return TerminalResolution(
        subject=_subject(condition_byte), payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR, block_number=15,
        block_hash="0x" + "dd" * 32, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        ),
        provider_ids=("archive-a", "archive-b"),
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

        for malformed_chain in (137.0, "137", True):
            malformed = _Provider("malformed", chain=malformed_chain)
            valid = _Provider("valid", chain=137)
            result, = ResolutionFeed(store, (malformed, valid)).poll(
                (_subject("92"),)
            )
            assert result.disposition is PollDisposition.UNAVAILABLE
            assert malformed.head_calls == valid.head_calls == 0


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


def test_poll_persists_matching_unknown_as_excluded_assessment(tmp_path):
    subject = _subject("85")
    block_hash = "0x" + "44" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((1, 1), 2),
        dispute=DisputeState.UNKNOWN, collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
        ),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash, observation=observation
    )
    second = _Provider(
        "archive-b", head=21, block_hash=block_hash,
        observation=replace(observation, provider_id="archive-b"),
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        result, = ResolutionFeed(store, (first, second)).poll((subject,))
        assert result.disposition is PollDisposition.UNKNOWN
        assert result.dispute is DisputeState.UNKNOWN and result.terminal_id is None
        assessment = store.assessment_for(subject.condition_id)
        assert assessment.phase is LifecyclePhase.FINALIZED
        assert assessment.dispute is DisputeState.UNKNOWN
        assert assessment.payout == PayoutVector((1, 1), 2)
        assert store.terminal_for(subject.condition_id) is None
        assert store.pending_outbox(10) == ()


def test_poll_accepts_matching_clear_terminal(tmp_path):
    subject = _subject("86")
    block_hash = "0x" + "88" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR, collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        ),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash, observation=observation
    )
    second = _Provider(
        "archive-b", head=21, block_hash=block_hash,
        observation=replace(observation, provider_id="archive-b"),
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        result, = ResolutionFeed(store, (first, second)).poll((subject,))
        terminal = store.terminal_for(subject.condition_id)
        assert result.disposition is PollDisposition.ACCEPTED
        assert result.dispute is DisputeState.CLEAR
        assert result.terminal_id == terminal.terminal_id
        assert terminal.payout == PayoutVector((3, 1), 4)
        assert store.assessment_for(subject.condition_id) is None
        assert [record.role for record in store.pending_outbox(10)] == [
            "FORECAST", "MAKER", "SHADOW"
        ]


@pytest.mark.parametrize("classified", [DisputeState.DISPUTED, DisputeState.MANUAL])
def test_poll_accepts_classified_excluded_terminal(tmp_path, classified):
    subject = _subject("87" if classified is DisputeState.DISPUTED else "89")
    block_hash = "0x" + "99" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((3, 1), 4),
        dispute=classified, collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        ),
    )
    providers = (
        _Provider("archive-a", head=20, block_hash=block_hash, observation=observation),
        _Provider(
            "archive-b", head=20, block_hash=block_hash,
            observation=replace(observation, provider_id="archive-b"),
        ),
    )
    with ResolutionStore(str(tmp_path / f"{classified.value}.db"), MonotonicStamper()) as store:
        result, = ResolutionFeed(store, providers).poll((subject,))
        terminal = store.terminal_for(subject.condition_id)
        assert result.disposition is PollDisposition.ACCEPTED
        assert result.dispute is classified and result.terminal_id == terminal.terminal_id
        assert terminal.dispute is classified
        assert [record.role for record in store.pending_outbox(10)] == [
            "FORECAST", "MAKER", "SHADOW"
        ]


def test_poll_isolates_retryable_unavailability_in_input_order(tmp_path):
    unavailable_subject = _subject("8a")
    later_subject = _subject("8b")
    block_hash = "0x" + "aa" * 32
    later_observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.UNRESOLVED, payout=None, dispute=DisputeState.UNKNOWN,
        collateral_address=None, derived_token_ids=None, adapter_address=None,
        question_id=None, audit_event_ids=(),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash,
        observation={
            unavailable_subject.condition_id: ResolutionUnavailable("provider timeout"),
            later_subject.condition_id: later_observation,
        },
    )
    second = _Provider(
        "archive-b", head=20, block_hash=block_hash,
        observation={
            later_subject.condition_id: replace(later_observation, provider_id="archive-b"),
        },
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        results = ResolutionFeed(store, (first, second)).poll(
            (unavailable_subject, later_subject)
        )
        assert [result.condition_id for result in results] == [
            unavailable_subject.condition_id, later_subject.condition_id
        ]
        assert [result.disposition for result in results] == [
            PollDisposition.UNAVAILABLE, PollDisposition.UNRESOLVED
        ]
        assert store.assessment_for(unavailable_subject.condition_id) is None
        assert store.assessment_for(later_subject.condition_id) is not None


def test_repeat_poll_verifies_original_terminal_coordinate(tmp_path):
    subject = _subject("8c")
    block_hash = "0x" + "bb" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR, collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        ),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash, observation=observation
    )
    second = _Provider(
        "archive-b", head=20, block_hash=block_hash,
        observation=replace(observation, provider_id="archive-b"),
    )
    with ResolutionStore(str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        feed = ResolutionFeed(store, (first, second))
        accepted, = feed.poll((subject,))
        assert accepted.disposition is PollDisposition.ACCEPTED
        terminal = store.terminal_for(subject.condition_id)
        head_calls = (first.head_calls, second.head_calls)
        hash_calls = (tuple(first.hash_calls), tuple(second.hash_calls))
        observe_calls = (tuple(first.observe_calls), tuple(second.observe_calls))
        first._head = second._head = 100

        repeated, = feed.poll((subject,))
        assert repeated.disposition is PollDisposition.ALREADY_TERMINAL
        assert repeated.terminal_id == terminal.terminal_id
        assert first.verify_calls == second.verify_calls == [terminal]
        assert (first.head_calls, second.head_calls) == head_calls
        assert (tuple(first.hash_calls), tuple(second.hash_calls)) == hash_calls
        assert (tuple(first.observe_calls), tuple(second.observe_calls)) == observe_calls


@pytest.mark.parametrize("changed", [
    "acceptance hash", "payout", "deployment code", "collateral", "token mapping",
])
def test_verify_terminal_halts_on_any_original_authority_change(tmp_path, changed):
    subject = _subject("8d")
    block_hash = "0x" + "cc" * 32
    observation = ProviderObservation(
        provider_id="archive-a", block_number=15, block_hash=block_hash,
        phase=LifecyclePhase.FINALIZED, payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR, collateral_address=PUSD_ADDRESS,
        derived_token_ids=subject.token_ids, adapter_address="0x" + "55" * 20,
        question_id="0x" + "66" * 32,
        audit_event_ids=(
            "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        ),
    )
    first = _Provider(
        "archive-a", head=20, block_hash=block_hash, observation=observation
    )
    second = _Provider(
        "archive-b", head=20, block_hash=block_hash,
        observation=replace(observation, provider_id="archive-b"),
    )
    with ResolutionStore(str(tmp_path / f"{changed}.db"), MonotonicStamper()) as store:
        feed = ResolutionFeed(store, (first, second))
        accepted, = feed.poll((subject,))
        terminal = store.terminal_for(subject.condition_id)
        assert accepted.disposition is PollDisposition.ACCEPTED
        first._verification_error = SettlementConflict(f"{changed} changed")

        with pytest.raises(SettlementConflict, match="changed"):
            feed.verify_terminal(terminal)
        with pytest.raises(IntegrityHalted, match=changed):
            store.require_healthy()


def test_recover_pending_verifies_all_before_clearing_barrier(tmp_path):
    path = str(tmp_path / "resolution.db")
    terminals = (_terminal("8e"), _terminal("8f"))
    with ResolutionStore(path, MonotonicStamper()) as store:
        for terminal in terminals:
            store.accept_terminal(terminal)

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        assert reopened.recovery_required is True
        first = _Provider("archive-a")
        second = _Provider("archive-b")
        feed = ResolutionFeed(reopened, (first, second))
        assert feed.recover_pending() == 2
        assert reopened.recovery_required is False
        assert first.verify_calls == second.verify_calls == list(terminals)


def test_recover_pending_unavailable_keeps_barrier_and_outbox_pending(tmp_path):
    path = str(tmp_path / "resolution.db")
    terminals = (_terminal("90"), _terminal("91"))
    with ResolutionStore(path, MonotonicStamper()) as store:
        for terminal in terminals:
            store.accept_terminal(terminal)

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        pending_before = reopened.pending_outbox(100)
        unavailable = ResolutionUnavailable("archive offline")
        first = _Provider("archive-a", verification_error=unavailable)
        second = _Provider("archive-b")
        feed = ResolutionFeed(reopened, (first, second))

        with pytest.raises(ResolutionUnavailable, match="offline"):
            feed.recover_pending()

        assert reopened.recovery_required is True
        assert reopened.pending_outbox(100) == pending_before
        assert first.verify_calls == [terminals[0]]
        assert second.verify_calls == []
