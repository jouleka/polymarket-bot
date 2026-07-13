"""Pure authority models for resolution and settlement."""

from dataclasses import dataclass
import re


_BYTES32 = re.compile(r"0x[0-9a-f]{64}\Z")
_TOKEN_ID = re.compile(r"[1-9][0-9]*\Z")
_UINT256_MAX = 2**256 - 1


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
