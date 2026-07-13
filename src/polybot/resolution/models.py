"""Pure authority models for resolution and settlement."""

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from fractions import Fraction
import re


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TOKEN_ID = re.compile(r"[1-9][0-9]*\Z")
_ADDRESS = re.compile(r"0x[0-9a-f]{40}\Z")
_AUDIT_EVENT = re.compile(
    r"(0|[1-9][0-9]*):(0|[1-9][0-9]*):(0x[0-9a-f]{64}):([A-Z][A-Z0-9_]*)\Z"
)
_UINT256_MAX = 2**256 - 1
_PROJECTION_CONTEXT = Context(prec=78, rounding=ROUND_HALF_EVEN)


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


class LifecyclePhase(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    FINALIZED = "FINALIZED"


class DisputeState(str, Enum):
    CLEAR = "CLEAR"
    DISPUTED = "DISPUTED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


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
        if not self.audit_event_ids:
            raise ValueError("finalized observations require audit evidence")
        positions = []
        for event_id in self.audit_event_ids:
            if not isinstance(event_id, str):
                raise TypeError("audit event IDs must be strings")
            match = _AUDIT_EVENT.fullmatch(event_id)
            if match is None:
                raise ValueError("audit event ID is not canonical")
            positions.append((int(match.group(1)), int(match.group(2)), match.group(3)))
        if len(set(self.audit_event_ids)) != len(self.audit_event_ids):
            raise ValueError("audit event IDs must be unique")
        if positions != sorted(positions):
            raise ValueError("audit event IDs must be in chain order")
