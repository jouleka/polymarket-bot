"""ers/gtd.py: pure GTD-bracket derivation at entry. A protective standing-exit per accepted
position; the AGGREGATE standing-exit is bounded by caps.gtd_bracket_aggregate (== total_open_risk,
$60). Fail-closed: a bracket that would push the aggregate past the ceiling raises."""
import pytest
from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.gtd import Bracket, derive_bracket
from polybot.ers.validator import Decision, OpenPosition


def _pos(token="A", entry="0.50", risk="12"):
    return OpenPosition("m", "e", "s", "c", Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry), frozen=False)


def test_derive_bracket_returns_a_protective_standing_exit():
    caps = RiskCaps()
    pos = _pos(token="A", entry="0.50", risk="12")
    decision = Decision("ACCEPT", Decimal("12"), Decimal("0.50"), "per_trade_cap")
    bracket = derive_bracket(decision, pos, caps=caps, expiry=1700, standing_exit_total=Decimal("0"))
    assert isinstance(bracket, Bracket)
    assert bracket.token_id == "A"
    assert bracket.size == Decimal("12")                 # protects the full notional
    assert bracket.expiry == 1700
    # Protective exit: strictly BELOW the entry price and inside (0,1).
    assert Decimal(0) < bracket.exit_price < Decimal("0.50")


def test_aggregate_standing_exit_within_total_open_is_allowed():
    caps = RiskCaps()  # gtd_bracket_aggregate == total_open_risk == 60
    pos = _pos(token="B", entry="0.50", risk="18")
    decision = Decision("ACCEPT", Decimal("18"), Decimal("0.50"), "per_market_cap")
    # 40 already standing + this 18 = 58 <= 60 -> OK
    bracket = derive_bracket(decision, pos, caps=caps, expiry=1800,
                             standing_exit_total=Decimal("40"))
    assert bracket.size == Decimal("18")


def test_aggregate_standing_exit_over_total_open_fails_closed():
    caps = RiskCaps()
    pos = _pos(token="C", entry="0.50", risk="18")
    decision = Decision("ACCEPT", Decimal("18"), Decimal("0.50"), "per_market_cap")
    # 50 already standing + this 18 = 68 > 60 -> must raise (fail-closed).
    with pytest.raises(ValueError, match="aggregate|gtd|total_open|standing"):
        derive_bracket(decision, pos, caps=caps, expiry=1900,
                       standing_exit_total=Decimal("50"))


def test_bracket_is_frozen():
    b = Bracket(token_id="A", exit_price=Decimal("0.10"), expiry=1, size=Decimal("12"))
    with pytest.raises(Exception):
        b.exit_price = Decimal("0.20")  # frozen dataclass -> FrozenInstanceError
