"""Two-provider reconciliation and terminal acceptance for POL-15."""

from dataclasses import dataclass, replace
from enum import Enum
import re

from polybot.resolution.errors import ResolutionUnavailable, SettlementConflict
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PUSD_ADDRESS,
    ProviderObservation,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionAssessment, ResolutionStore


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TERMINAL_ID = re.compile(r"[0-9a-f]{64}\Z")


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

    def __post_init__(self):
        if (not isinstance(self.condition_id, str)
                or _BYTES32.fullmatch(self.condition_id) is None):
            raise ValueError("poll condition_id must be a canonical lowercase bytes32")
        if not isinstance(self.disposition, PollDisposition):
            raise TypeError("poll disposition must be a PollDisposition")
        if self.dispute is not None and not isinstance(self.dispute, DisputeState):
            raise TypeError("poll dispute must be a DisputeState or None")
        if (self.terminal_id is not None
                and (not isinstance(self.terminal_id, str)
                     or _TERMINAL_ID.fullmatch(self.terminal_id) is None)):
            raise ValueError("poll terminal_id must be a lowercase SHA-256 hex string")
        if (not isinstance(self.detail, str) or not self.detail
                or self.detail != self.detail.strip()):
            raise ValueError("poll detail must be a non-empty exact string")

        if self.disposition is PollDisposition.UNAVAILABLE:
            if self.dispute is not None or self.terminal_id is not None:
                raise ValueError("unavailable results cannot carry authority")
        elif self.disposition in (
                PollDisposition.UNRESOLVED, PollDisposition.UNKNOWN):
            if (self.dispute is not DisputeState.UNKNOWN
                    or self.terminal_id is not None):
                raise ValueError("non-terminal results require UNKNOWN without terminal")
        elif (self.dispute not in (
                DisputeState.CLEAR, DisputeState.DISPUTED, DisputeState.MANUAL)
              or self.terminal_id is None):
            raise ValueError("terminal results require classified terminal authority")


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

    def validate_providers(self):
        """Fail startup unless both configured authorities prove Polygon chain 137."""
        try:
            chain_ids = tuple(provider.chain_id() for provider in self._providers)
        except Exception as exc:
            raise ResolutionUnavailable(
                "resolution provider startup preflight unavailable"
            ) from exc
        if any(isinstance(chain_id, bool) or not isinstance(chain_id, int)
               or chain_id != 137 for chain_id in chain_ids):
            raise SettlementConflict(
                "resolution providers must prove Polygon chain 137"
            )
        return chain_ids

    def poll(self, subjects):
        self._validate_subjects(subjects)
        if not subjects:
            return ()
        self._store.require_healthy()
        try:
            chain_ids = tuple(provider.chain_id() for provider in self._providers)
        except Exception:
            self._store.require_healthy()
            return self._unavailable(subjects, "provider chain unavailable")
        if any(isinstance(chain_id, bool) or not isinstance(chain_id, int)
               or chain_id != 137 for chain_id in chain_ids):
            self._store.require_healthy()
            return self._unavailable(subjects, "provider chain is not Polygon 137")

        results = {}
        remaining = []
        for subject in subjects:
            try:
                terminal = self._store.terminal_for(subject.condition_id)
            except SettlementConflict as exc:
                self._store.halt(str(exc) or "stored terminal authority contradiction")
                raise
            if terminal is None:
                remaining.append(subject)
                continue
            if terminal.subject != subject:
                conflict = SettlementConflict(
                    "poll subject contradicts stored terminal subject"
                )
                self._store.halt(str(conflict))
                raise conflict
            try:
                self.verify_terminal(terminal)
            except SettlementConflict:
                raise
            except ResolutionUnavailable:
                self._store.require_healthy()
                results[subject.condition_id] = PollResult(
                    subject.condition_id, PollDisposition.UNAVAILABLE, None, None,
                    "stored terminal verification unavailable",
                )
            else:
                results[subject.condition_id] = PollResult(
                    subject.condition_id, PollDisposition.ALREADY_TERMINAL,
                    terminal.dispute, terminal.terminal_id,
                    "stored terminal authority verified",
                )
        if not remaining:
            return tuple(results[subject.condition_id] for subject in subjects)
        try:
            heads = tuple(provider.latest_block() for provider in self._providers)
            if any(isinstance(head, bool) or not isinstance(head, int) or head < 0
                   for head in heads):
                raise ValueError("provider head is not a non-negative integer")
            acceptance_block = min(heads) - 5
            if acceptance_block < 0:
                self._store.require_healthy()
                return self._merge_unavailable(
                    subjects, remaining, results, "five-confirmation block is unavailable"
                )
            block_hashes = tuple(
                provider.block_hash(acceptance_block) for provider in self._providers
            )
            if (any(not isinstance(value, str) or _BYTES32.fullmatch(value) is None
                    for value in block_hashes) or block_hashes[0] != block_hashes[1]):
                self._store.require_healthy()
                return self._merge_unavailable(
                    subjects, remaining, results,
                    "provider acceptance block hashes disagree",
                )
        except Exception:
            self._store.require_healthy()
            return self._merge_unavailable(
                subjects, remaining, results, "provider acceptance coordinate unavailable"
            )
        for subject in remaining:
            try:
                observations = tuple(
                    provider.observe(subject, acceptance_block)
                    for provider in self._providers
                )
                self._validate_observations(
                    observations, acceptance_block, block_hashes[0]
                )
                first = observations[0]
                if (first.phase is LifecyclePhase.FINALIZED
                        and (first.collateral_address != PUSD_ADDRESS
                             or first.derived_token_ids != subject.token_ids)):
                    raise ValueError(
                        "finalized observation identity does not match subject"
                    )
                if first.phase is LifecyclePhase.UNRESOLVED:
                    classification = PollDisposition.UNRESOLVED
                    terminal = None
                elif first.dispute is DisputeState.UNKNOWN:
                    classification = PollDisposition.UNKNOWN
                    terminal = None
                elif first.dispute in (
                        DisputeState.CLEAR, DisputeState.DISPUTED, DisputeState.MANUAL):
                    terminal = TerminalResolution.from_observations(
                        subject, observations[0], observations[1]
                    )
                    classification = PollDisposition.ACCEPTED
                else:
                    raise ValueError("classified terminal reconciliation is unavailable")
            except SettlementConflict as exc:
                self._store.halt(str(exc) or "resolution authority contradiction")
                raise
            except Exception:
                self._store.require_healthy()
                results[subject.condition_id] = PollResult(
                    subject.condition_id, PollDisposition.UNAVAILABLE, None, None,
                    "provider observation unavailable",
                )
                continue

            try:
                if classification in (
                        PollDisposition.UNRESOLVED, PollDisposition.UNKNOWN):
                    detail = (
                        "providers agree condition is unresolved"
                        if classification is PollDisposition.UNRESOLVED
                        else "providers agree finalized path is unknown"
                    )
                    self._store.record_assessment(ResolutionAssessment(
                        subject, first.phase, first.dispute, first.payout,
                        first.block_number, first.block_hash, detail,
                    ))
                    results[subject.condition_id] = PollResult(
                        subject.condition_id, classification,
                        DisputeState.UNKNOWN, None, detail,
                    )
                else:
                    created = self._store.accept_terminal(terminal)
                    disposition = (
                        PollDisposition.ACCEPTED if created
                        else PollDisposition.ALREADY_TERMINAL
                    )
                    results[subject.condition_id] = PollResult(
                        subject.condition_id, disposition, terminal.dispute,
                        terminal.terminal_id, "providers agree terminal authority",
                    )
            except SettlementConflict as exc:
                self._store.halt(str(exc) or "resolution authority contradiction")
                raise
        return tuple(results[subject.condition_id] for subject in subjects)

    def verify_terminal(self, terminal):
        if not isinstance(terminal, TerminalResolution):
            raise TypeError("terminal must be a TerminalResolution")
        self._store.require_healthy()
        try:
            try:
                chain_ids = tuple(
                    provider.chain_id() for provider in self._providers
                )
            except ResolutionUnavailable:
                self._store.require_healthy()
                raise
            except Exception as exc:
                self._store.require_healthy()
                raise ResolutionUnavailable(
                    "provider chain verification unavailable"
                ) from exc
            if any(isinstance(chain_id, bool) or not isinstance(chain_id, int)
                   or chain_id != 137 for chain_id in chain_ids):
                self._store.require_healthy()
                raise ResolutionUnavailable(
                    "provider chain verification is not Polygon 137"
                )
            provider_ids = tuple(sorted(
                provider.provider_id for provider in self._providers
            ))
            if provider_ids != tuple(sorted(terminal.provider_ids)):
                raise SettlementConflict(
                    "terminal provider authority does not match configured providers"
                )
            for provider in self._providers:
                try:
                    result = provider.verify_terminal(terminal)
                except SettlementConflict:
                    raise
                except ResolutionUnavailable:
                    self._store.require_healthy()
                    raise
                except Exception as exc:
                    self._store.require_healthy()
                    raise ResolutionUnavailable(
                        "provider terminal verification unavailable"
                    ) from exc
                if result is not None:
                    self._store.require_healthy()
                    raise ResolutionUnavailable(
                        "provider terminal verification returned a malformed result"
                    )
        except SettlementConflict as exc:
            reason = str(exc) or "provider terminal authority contradiction"
            self._store.halt(reason)
            raise

    def recover_pending(self):
        self._store.require_healthy()
        try:
            terminals = self._store.pending_terminals()
            for terminal in terminals:
                self.verify_terminal(terminal)
            self._store._complete_recovery(
                tuple(terminal.terminal_id for terminal in terminals)
            )
        except SettlementConflict as exc:
            self._store.halt(str(exc) or "stored recovery authority contradiction")
            raise
        return len(terminals)

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

    @staticmethod
    def _merge_unavailable(subjects, remaining, results, detail):
        for subject in remaining:
            results[subject.condition_id] = PollResult(
                subject.condition_id, PollDisposition.UNAVAILABLE, None, None, detail
            )
        return tuple(results[subject.condition_id] for subject in subjects)

    def _validate_observations(self, observations, block_number, block_hash):
        if len(observations) != 2:
            raise ValueError("exactly two observations are required")
        fields = (
            "block_number", "block_hash", "phase", "payout", "dispute",
            "collateral_address", "derived_token_ids", "adapter_address",
            "question_id", "audit_event_ids",
        )
        for provider, observation in zip(self._providers, observations):
            if not isinstance(observation, ProviderObservation):
                raise ValueError("provider observation authority does not match")
            replace(observation)
            if (observation.provider_id != provider.provider_id
                    or observation.block_number != block_number
                    or observation.block_hash != block_hash):
                raise ValueError("provider observation authority does not match")
        if any(getattr(observations[0], field) != getattr(observations[1], field)
               for field in fields):
            raise ValueError("provider observations disagree")
