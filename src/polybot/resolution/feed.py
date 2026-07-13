"""Two-provider reconciliation and terminal acceptance for POL-15."""

from dataclasses import dataclass
from enum import Enum
import re

from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    ProviderObservation,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionAssessment, ResolutionStore


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")


class PollDisposition(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    UNKNOWN = "UNKNOWN"
    ACCEPTED = "ACCEPTED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class PollResult:
    condition_id: str
    disposition: PollDisposition
    dispute: DisputeState | None
    terminal_id: str | None
    detail: str


class ResolutionFeed:
    def __init__(self, store, providers):
        if not isinstance(store, ResolutionStore):
            raise TypeError("store must be a ResolutionStore")
        if not isinstance(providers, tuple) or len(providers) != 2:
            raise ValueError("resolution feed requires exactly two providers")
        provider_ids = tuple(getattr(provider, "provider_id", None) for provider in providers)
        if any(not isinstance(value, str) or not value or value != value.strip()
               for value in provider_ids):
            raise ValueError("provider IDs must be non-empty exact strings")
        if provider_ids[0] == provider_ids[1]:
            raise ValueError("provider IDs must be distinct")
        self._store = store
        self._providers = providers

    def poll(self, subjects):
        self._validate_subjects(subjects)
        self._store.require_healthy()
        try:
            chain_ids = tuple(provider.chain_id() for provider in self._providers)
        except Exception:
            return self._unavailable(subjects, "provider chain unavailable")
        if any(isinstance(chain_id, bool) or chain_id != 137 for chain_id in chain_ids):
            return self._unavailable(subjects, "provider chain is not Polygon 137")
        try:
            heads = tuple(provider.latest_block() for provider in self._providers)
            if any(isinstance(head, bool) or not isinstance(head, int) or head < 0
                   for head in heads):
                raise ValueError("provider head is not a non-negative integer")
            acceptance_block = min(heads) - 5
            if acceptance_block < 0:
                return self._unavailable(subjects, "five-confirmation block is unavailable")
            block_hashes = tuple(
                provider.block_hash(acceptance_block) for provider in self._providers
            )
            if (any(not isinstance(value, str) or _BYTES32.fullmatch(value) is None
                    for value in block_hashes) or block_hashes[0] != block_hashes[1]):
                return self._unavailable(subjects, "provider acceptance block hashes disagree")
        except Exception:
            return self._unavailable(subjects, "provider acceptance coordinate unavailable")
        results = []
        for subject in subjects:
            try:
                observations = tuple(
                    provider.observe(subject, acceptance_block)
                    for provider in self._providers
                )
                self._validate_observations(
                    observations, acceptance_block, block_hashes[0]
                )
                first = observations[0]
                if first.phase is LifecyclePhase.UNRESOLVED:
                    detail = "providers agree condition is unresolved"
                    self._store.record_assessment(ResolutionAssessment(
                        subject, first.phase, first.dispute, first.payout,
                        first.block_number, first.block_hash, detail,
                    ))
                    results.append(PollResult(
                        subject.condition_id, PollDisposition.UNRESOLVED,
                        DisputeState.UNKNOWN, None, detail,
                    ))
                elif first.dispute is DisputeState.UNKNOWN:
                    detail = "providers agree finalized path is unknown"
                    self._store.record_assessment(ResolutionAssessment(
                        subject, first.phase, first.dispute, first.payout,
                        first.block_number, first.block_hash, detail,
                    ))
                    results.append(PollResult(
                        subject.condition_id, PollDisposition.UNKNOWN,
                        DisputeState.UNKNOWN, None, detail,
                    ))
                else:
                    terminal = TerminalResolution.from_observations(
                        subject, observations[0], observations[1]
                    )
                    created = self._store.accept_terminal(terminal)
                    disposition = (
                        PollDisposition.ACCEPTED if created
                        else PollDisposition.ALREADY_TERMINAL
                    )
                    results.append(PollResult(
                        subject.condition_id, disposition, terminal.dispute,
                        terminal.terminal_id, "providers agree terminal authority",
                    ))
            except Exception:
                results.append(PollResult(
                    subject.condition_id, PollDisposition.UNAVAILABLE, None, None,
                    "provider observation unavailable",
                ))
        return tuple(results)

    @staticmethod
    def _validate_subjects(subjects):
        if not isinstance(subjects, tuple):
            raise TypeError("subjects must be a tuple")
        if any(not isinstance(subject, ResolutionSubject) for subject in subjects):
            raise TypeError("every subject must be a ResolutionSubject")
        condition_ids = tuple(subject.condition_id for subject in subjects)
        if len(set(condition_ids)) != len(condition_ids):
            raise ValueError("subjects must be unique by condition")

    @staticmethod
    def _unavailable(subjects, detail):
        return tuple(
            PollResult(
                subject.condition_id, PollDisposition.UNAVAILABLE, None, None, detail
            )
            for subject in subjects
        )

    def _validate_observations(self, observations, block_number, block_hash):
        if len(observations) != 2:
            raise ValueError("exactly two observations are required")
        fields = (
            "block_number", "block_hash", "phase", "payout", "dispute",
            "collateral_address", "derived_token_ids", "adapter_address",
            "question_id", "audit_event_ids",
        )
        for provider, observation in zip(self._providers, observations):
            if (not isinstance(observation, ProviderObservation)
                    or observation.provider_id != provider.provider_id
                    or observation.block_number != block_number
                    or observation.block_hash != block_hash):
                raise ValueError("provider observation authority does not match")
        if any(getattr(observations[0], field) != getattr(observations[1], field)
               for field in fields):
            raise ValueError("provider observations disagree")
