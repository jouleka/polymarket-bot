"""Pure authority models for resolution and settlement."""

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from fractions import Fraction
import hashlib
import re
from typing import Protocol

from polybot.resolution.canonical import canonical_bytes
from polybot.resolution.errors import ResolutionUnavailable


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TOKEN_ID = re.compile(r"[1-9][0-9]*\Z")
_ADDRESS = re.compile(r"0x[0-9a-f]{40}\Z")
_AUDIT_EVENT = re.compile(
    r"(0|[1-9][0-9]*):(0|[1-9][0-9]*):(0x[0-9a-f]{64}):([A-Z][A-Z0-9_]*)\Z"
)
_UINT256_MAX = 2**256 - 1
_PROJECTION_CONTEXT = Context(prec=78, rounding=ROUND_HALF_EVEN)
PUSD_ADDRESS = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
CTF_ADDRESS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"


def _exact_nonempty(value, name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty exact string")


def _validate_token_pair(token_ids):
    if not isinstance(token_ids, tuple) or len(token_ids) != 2:
        raise ValueError("token_ids must be an ordered pair")
    if token_ids[0] == token_ids[1]:
        raise ValueError("token_ids must be distinct")
    for token_id in token_ids:
        if (not isinstance(token_id, str)
                or _TOKEN_ID.fullmatch(token_id) is None
                or int(token_id) > _UINT256_MAX):
            raise ValueError("token_ids must be canonical positive uint256 strings")


def _validate_audit_event_ids(audit_event_ids):
    if not isinstance(audit_event_ids, tuple):
        raise TypeError("audit_event_ids must be a tuple")
    if not audit_event_ids:
        raise ValueError("terminal evidence requires audit event IDs")
    positions = []
    for event_id in audit_event_ids:
        if not isinstance(event_id, str):
            raise TypeError("audit event IDs must be strings")
        match = _AUDIT_EVENT.fullmatch(event_id)
        if match is None:
            raise ValueError("audit event ID is not canonical")
        positions.append((int(match.group(1)), int(match.group(2))))
    if len(set(positions)) != len(positions):
        raise ValueError("audit event chain positions must be unique")
    if positions != sorted(positions):
        raise ValueError("audit event IDs must be in chain order")


class LifecyclePhase(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    FINALIZED = "FINALIZED"


class DisputeState(str, Enum):
    CLEAR = "CLEAR"
    DISPUTED = "DISPUTED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


_DISPUTE_PRECEDENCE = {
    DisputeState.CLEAR: 0,
    DisputeState.UNKNOWN: 1,
    DisputeState.DISPUTED: 2,
    DisputeState.MANUAL: 3,
}


def fold_dispute(states):
    if not isinstance(states, tuple):
        raise TypeError("dispute states must be a tuple")
    if not states:
        raise ValueError("cannot fold an empty dispute-state tuple")
    if any(not isinstance(state, DisputeState) for state in states):
        raise TypeError("every path value must be a DisputeState")
    return max(states, key=_DISPUTE_PRECEDENCE.__getitem__)


@dataclass(frozen=True)
class ResolutionSubject:
    """Gamma candidate identity whose token ordering must still be proved on chain."""

    event_id: str
    condition_id: str
    token_ids: tuple[str, str]
    category: str

    def __post_init__(self):
        _exact_nonempty(self.event_id, "event_id")
        _exact_nonempty(self.category, "category")
        if not isinstance(self.condition_id, str) or _BYTES32.fullmatch(
                self.condition_id) is None:
            raise ValueError("condition_id must be a canonical lowercase bytes32")
        _validate_token_pair(self.token_ids)


@dataclass(frozen=True)
class PayoutVector:
    """Exact CTF binary payout authority."""

    numerators: tuple[int, int]
    denominator: int

    def __post_init__(self):
        if not isinstance(self.numerators, tuple) or len(self.numerators) != 2:
            raise ValueError("numerators must be a binary tuple")
        for numerator in self.numerators:
            if (isinstance(numerator, bool) or not isinstance(numerator, int)
                    or not 0 <= numerator <= _UINT256_MAX):
                raise ValueError("payout numerators must be non-negative uint256 integers")
        if (isinstance(self.denominator, bool) or not isinstance(self.denominator, int)
                or not 0 < self.denominator <= _UINT256_MAX):
            raise ValueError("payout denominator must be a positive uint256 integer")
        if sum(self.numerators) != self.denominator:
            raise ValueError("payout denominator must equal the numerator sum")

    def fraction_for(self, slot):
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in (0, 1):
            raise IndexError(f"outcome slot must be 0 or 1, got {slot!r}")
        return Fraction(self.numerators[slot], self.denominator)

    def decimal_for(self, slot):
        fraction = self.fraction_for(slot)
        with localcontext(_PROJECTION_CONTEXT):
            return Decimal(fraction.numerator) / Decimal(fraction.denominator)


@dataclass(frozen=True)
class ProviderObservation:
    provider_id: str
    block_number: int
    block_hash: str
    phase: LifecyclePhase
    payout: PayoutVector | None
    dispute: DisputeState
    collateral_address: str | None
    derived_token_ids: tuple[str, str] | None
    adapter_address: str | None
    question_id: str | None
    audit_event_ids: tuple[str, ...]

    def __post_init__(self):
        _exact_nonempty(self.provider_id, "provider_id")
        if (isinstance(self.block_number, bool) or not isinstance(self.block_number, int)
                or self.block_number < 0):
            raise ValueError("block_number must be a non-negative integer")
        if not isinstance(self.block_hash, str) or _BYTES32.fullmatch(self.block_hash) is None:
            raise ValueError("block_hash must be a canonical lowercase bytes32")
        if not isinstance(self.phase, LifecyclePhase):
            raise TypeError("phase must be a LifecyclePhase")
        if not isinstance(self.dispute, DisputeState):
            raise TypeError("dispute must be a DisputeState")
        if not isinstance(self.audit_event_ids, tuple):
            raise TypeError("audit_event_ids must be a tuple")

        if self.phase is LifecyclePhase.UNRESOLVED:
            if (self.payout is not None or self.dispute is not DisputeState.UNKNOWN
                    or self.collateral_address is not None
                    or self.derived_token_ids is not None
                    or self.adapter_address is not None or self.question_id is not None
                    or self.audit_event_ids):
                raise ValueError("unresolved observations cannot carry terminal evidence")
            return

        if not isinstance(self.payout, PayoutVector):
            raise ValueError("finalized observations require a payout")
        if (not isinstance(self.collateral_address, str)
                or _ADDRESS.fullmatch(self.collateral_address) is None):
            raise ValueError("finalized observations require a canonical collateral address")
        _validate_token_pair(self.derived_token_ids)
        if (not isinstance(self.adapter_address, str)
                or _ADDRESS.fullmatch(self.adapter_address) is None):
            raise ValueError("finalized observations require a canonical adapter address")
        if not isinstance(self.question_id, str) or _BYTES32.fullmatch(self.question_id) is None:
            raise ValueError("finalized observations require a canonical question ID")
        _validate_audit_event_ids(self.audit_event_ids)


class ResolutionProvider(Protocol):
    provider_id: str

    def chain_id(self) -> int: ...

    def latest_block(self) -> int: ...

    def block_hash(self, block_number: int) -> str: ...

    def observe(
        self, subject: ResolutionSubject, block_number: int
    ) -> ProviderObservation: ...

    def verify_terminal(self, terminal: "TerminalResolution") -> None: ...


@dataclass(frozen=True)
class TerminalResolution:
    subject: ResolutionSubject
    payout: PayoutVector
    dispute: DisputeState
    block_number: int
    block_hash: str
    adapter_address: str
    question_id: str
    audit_event_ids: tuple[str, ...]
    provider_ids: tuple[str, str]

    def __post_init__(self):
        if not isinstance(self.subject, ResolutionSubject):
            raise TypeError("terminal subject must be a ResolutionSubject")
        if not isinstance(self.payout, PayoutVector):
            raise TypeError("terminal payout must be a PayoutVector")
        if not isinstance(self.dispute, DisputeState):
            raise TypeError("terminal path must be a DisputeState")
        if self.dispute is DisputeState.UNKNOWN:
            raise ValueError("UNKNOWN is not a terminal path")
        if (isinstance(self.block_number, bool) or not isinstance(self.block_number, int)
                or self.block_number < 0):
            raise ValueError("terminal block_number must be a non-negative integer")
        if not isinstance(self.block_hash, str) or _BYTES32.fullmatch(self.block_hash) is None:
            raise ValueError("terminal block_hash must be a canonical lowercase bytes32")
        if (not isinstance(self.adapter_address, str)
                or _ADDRESS.fullmatch(self.adapter_address) is None):
            raise ValueError("terminal adapter_address must be canonical")
        if not isinstance(self.question_id, str) or _BYTES32.fullmatch(self.question_id) is None:
            raise ValueError("terminal question_id must be a canonical lowercase bytes32")
        _validate_audit_event_ids(self.audit_event_ids)
        if not isinstance(self.provider_ids, tuple) or len(self.provider_ids) != 2:
            raise ValueError("terminal provider_ids must contain exactly two providers")
        for provider_id in self.provider_ids:
            _exact_nonempty(provider_id, "provider_id")
        if self.provider_ids[0] == self.provider_ids[1]:
            raise ValueError("terminal providers must be distinct")

    @property
    def payload(self):
        return {
            "acceptance": {
                "block_hash": self.block_hash,
                "block_number": self.block_number,
            },
            "authority": {
                "adapter_address": self.adapter_address,
                "audit_event_ids": list(self.audit_event_ids),
                "chain_id": 137,
                "collateral_address": PUSD_ADDRESS,
                "ctf_address": CTF_ADDRESS,
                "question_id": self.question_id,
            },
            "path": self.dispute.value,
            "payout": {
                "denominator": self.payout.denominator,
                "numerators": list(self.payout.numerators),
            },
            "providers": list(sorted(self.provider_ids)),
            "subject": {
                "category": self.subject.category,
                "condition_id": self.subject.condition_id,
                "event_id": self.subject.event_id,
                "token_ids": list(self.subject.token_ids),
            },
            "version": 1,
        }

    @property
    def canonical_bytes(self):
        return canonical_bytes(self.payload)

    @property
    def terminal_id(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @classmethod
    def from_observations(cls, subject, first, second):
        if not isinstance(subject, ResolutionSubject):
            raise TypeError("subject must be a ResolutionSubject")
        if not isinstance(first, ProviderObservation) or not isinstance(
                second, ProviderObservation):
            raise TypeError("terminal evidence must be ProviderObservation values")
        if first.provider_id == second.provider_id:
            raise ResolutionUnavailable("terminal providers must be distinct")
        fields = (
            "block_number", "block_hash", "phase", "payout", "dispute",
            "collateral_address", "derived_token_ids", "adapter_address",
            "question_id", "audit_event_ids",
        )
        if any(getattr(first, name) != getattr(second, name) for name in fields):
            raise ResolutionUnavailable("terminal providers disagree")
        if first.phase is not LifecyclePhase.FINALIZED:
            raise ResolutionUnavailable("condition is not finalized")
        if first.dispute is DisputeState.UNKNOWN:
            raise ResolutionUnavailable("unknown path is not terminal")
        if first.collateral_address != PUSD_ADDRESS:
            raise ResolutionUnavailable("terminal collateral is not supported pUSD")
        if first.derived_token_ids != subject.token_ids:
            raise ResolutionUnavailable("chain-derived token order disagrees with subject")
        return cls(
            subject=subject,
            payout=first.payout,
            dispute=first.dispute,
            block_number=first.block_number,
            block_hash=first.block_hash,
            adapter_address=first.adapter_address,
            question_id=first.question_id,
            audit_event_ids=first.audit_event_ids,
            provider_ids=tuple(sorted((first.provider_id, second.provider_id))),
        )
