"""Pure authority models for resolution and settlement."""

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import re


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TOKEN_ID = re.compile(r"[1-9][0-9]*\Z")
_UINT256_MAX = 2**256 - 1
_PROJECTION_CONTEXT = Context(prec=78, rounding=ROUND_HALF_EVEN)


def _exact_nonempty(value, name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty exact string")


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
        if not isinstance(self.token_ids, tuple) or len(self.token_ids) != 2:
            raise ValueError("token_ids must be an ordered pair")
        if self.token_ids[0] == self.token_ids[1]:
            raise ValueError("token_ids must be distinct")
        for token_id in self.token_ids:
            if (not isinstance(token_id, str)
                    or _TOKEN_ID.fullmatch(token_id) is None
                    or int(token_id) > _UINT256_MAX):
                raise ValueError("token_ids must be canonical positive uint256 strings")


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
