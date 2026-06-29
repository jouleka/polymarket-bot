"""GTD (good-til-date) protective exit brackets (S4.2 / POL-6).

A pure derivation: from an accepted Decision + the just-folded OpenPosition, produce ONE
standing protective EXIT order. These brackets are the PASSIVE BACKSTOP -- they remain standing
even after the kill path's cancel_all cancels the working ENTRY orders (DESIGN §9), so a wedged
ERS still de-risks at the venue.

Sizing (DESIGN §3 S4.2): each bracket protects the position's full worst-case risk (notional
for a long), and the AGGREGATE standing-exit across all open positions is bounded by
``caps.gtd_bracket_aggregate`` (which _verify pins == total_open_risk, $60). Fail-closed: a
bracket that would push the running aggregate past that ceiling raises -- we never stage more
protective exits than the at-risk ceiling permits.

No persistence, no network, no keys. The live POL-4 signer places the real GTD order.
"""

from dataclasses import dataclass
from decimal import Decimal


# A conservative protective floor: exit at 20% of the entry price (well below entry, inside
# (0,1)). The exact protective price is a modeling choice on the shadow signer; the live POL-4
# signer derives it from the book + the worst-case mark. Kept simple + deterministic here.
_PROTECTIVE_FRACTION = Decimal("0.20")


@dataclass(frozen=True)
class Bracket:
    token_id: str
    exit_price: Decimal
    expiry: int
    size: Decimal


def derive_bracket(decision, position, *, caps, expiry, standing_exit_total=Decimal(0)):
    """Derive the protective GTD exit bracket for a just-accepted position.

    ``standing_exit_total`` is the aggregate size of brackets already standing; this bracket's
    size (the position's worst-case risk) is added and the total must stay <=
    caps.gtd_bracket_aggregate, else we fail closed (raise).
    """
    size = position.worst_case_risk
    projected = standing_exit_total + size
    if projected > caps.gtd_bracket_aggregate:
        raise ValueError(
            f"GTD aggregate standing-exit {projected} exceeds gtd_bracket_aggregate "
            f"({caps.gtd_bracket_aggregate}); refusing to stage bracket for {position.token_id}"
        )
    # Protective exit strictly below the executed entry price (we exit to protect, not profit).
    exit_price = decision.price_exec * _PROTECTIVE_FRACTION
    return Bracket(token_id=position.token_id, exit_price=exit_price, expiry=expiry, size=size)
