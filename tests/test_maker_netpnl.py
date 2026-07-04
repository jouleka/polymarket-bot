"""S8 / POL-10 — net-PnL identity (the honest after-all-costs maker figure)."""

from decimal import Decimal

import pytest

from polybot.maker.netpnl import MakerNetPnL, net_pnl


def _pnl(**overrides):
    legs = dict(reward=Decimal("3"), rebate=Decimal("0.5"), spread_capture=Decimal("1.25"),
                adverse_selection=Decimal("2"), fees=Decimal("0.4"),
                lockup_cost=Decimal("0.1"), dispute_haircut=Decimal("0.25"))
    legs.update(overrides)
    return net_pnl(**legs)


def test_identity_on_the_hand_computed_all_legs_case():
    # 3 + 0.5 + 1.25 = 4.75 credit; 2 + 0.4 + 0.1 + 0.25 = 2.75 debit; net = 2.00.
    r = _pnl()
    assert isinstance(r, MakerNetPnL)
    assert r.net == Decimal("2.00")
    # every leg round-trips as a named field
    assert r.reward == Decimal("3") and r.rebate == Decimal("0.5")
    assert r.spread_capture == Decimal("1.25") and r.adverse_selection == Decimal("2")
    assert r.fees == Decimal("0.4") and r.lockup_cost == Decimal("0.1")
    assert r.dispute_haircut == Decimal("0.25")


def test_negative_net_when_adverse_selection_dominates():
    # the "bleeds invisibly" number must be representable and honest.
    r = _pnl(adverse_selection=Decimal("6"))
    assert r.net == Decimal("-2.00")


def test_negative_adverse_selection_increases_net():
    # favorable marks = negative adverse cost; subtracting a negative adds.
    assert _pnl(adverse_selection=Decimal("-1")).net == Decimal("5.00")


def test_zero_legs_net_to_zero():
    zero = {name: Decimal("0") for name in ("reward", "rebate", "spread_capture",
            "adverse_selection", "fees", "lockup_cost", "dispute_haircut")}
    assert net_pnl(**zero).net == Decimal("0")


def test_net_is_computed_by_net_pnl_not_caller_supplied():
    # structural honesty: for any leg mix, .net equals the hand identity — there is no
    # public path that lets a caller supply an inconsistent net.
    cases = (
        dict(reward=Decimal("1"), rebate=Decimal("0"), spread_capture=Decimal("-0.5"),
             adverse_selection=Decimal("0.25"), fees=Decimal("0.1"),
             lockup_cost=Decimal("0"), dispute_haircut=Decimal("0")),
        dict(reward=Decimal("0"), rebate=Decimal("0.05"), spread_capture=Decimal("2"),
             adverse_selection=Decimal("-0.75"), fees=Decimal("0"),
             lockup_cost=Decimal("0.2"), dispute_haircut=Decimal("0.1")),
    )
    for legs in cases:
        expected = (legs["reward"] + legs["rebate"] + legs["spread_capture"]
                    - legs["adverse_selection"] - legs["fees"]
                    - legs["lockup_cost"] - legs["dispute_haircut"])
        assert net_pnl(**legs).net == expected


def test_rejects_a_non_finite_leg():
    for name in ("reward", "rebate", "spread_capture", "adverse_selection",
                 "fees", "lockup_cost", "dispute_haircut"):
        with pytest.raises(ValueError, match=name):
            _pnl(**{name: Decimal("NaN")})
    with pytest.raises(ValueError, match="reward"):
        _pnl(reward=Decimal("Infinity"))


def test_rejects_negative_one_signed_legs():
    for name in ("reward", "rebate", "fees", "lockup_cost", "dispute_haircut"):
        with pytest.raises(ValueError, match=name):
            _pnl(**{name: Decimal("-0.01")})


def test_allows_negative_two_signed_legs():
    # spread_capture and adverse_selection may be either sign by nature.
    assert _pnl(spread_capture=Decimal("-1")).net == Decimal("-0.25")
    assert _pnl(adverse_selection=Decimal("-2")).net == Decimal("6.00")
