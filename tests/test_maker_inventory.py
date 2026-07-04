"""S8 / POL-10 — maker inventory (MakerFill validation + BUY/SELL folding + adverse-selection mark-out)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from polybot.maker.inventory import _SGN, MakerFill, net_inventory


def _fill(**over):
    base = dict(
        token_id="tok-yes",
        condition_id="cond-1",
        category="sports",
        side="BUY",
        shares=Decimal("10"),
        price_exec=Decimal("0.50"),
        fill_mid=Decimal("0.50"),
    )
    base.update(over)
    return MakerFill(**base)


def test_valid_fill_constructs():
    fill = _fill()
    assert fill.token_id == "tok-yes"
    assert fill.side == "BUY"
    assert fill.shares == Decimal("10")
    assert fill.price_exec == Decimal("0.50")


def test_fill_is_frozen():
    fill = _fill()
    with pytest.raises(FrozenInstanceError):
        fill.shares = Decimal("1")


def test_sgn_maps_buy_plus_one_sell_minus_one():
    assert _SGN["BUY"] == Decimal(1)
    assert _SGN["SELL"] == Decimal(-1)


def test_rejects_bad_side():
    with pytest.raises(ValueError, match="side"):
        _fill(side="HOLD")


def test_rejects_zero_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("0"))


def test_rejects_negative_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("-1"))


def test_rejects_non_finite_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("NaN"))
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("Infinity"))


def test_rejects_price_exec_out_of_range():
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("1.01"))
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("-0.01"))


def test_rejects_fill_mid_out_of_range():
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("1.01"))
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("-0.01"))


def test_rejects_non_finite_prices():
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("NaN"))
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("Infinity"))


def test_buy_sell_folding_net_and_avg_cost():
    # BUY 10 @ 0.46 + SELL 4 @ 0.40 on ONE token:
    #   net = 10 - 4 = 6
    #   signed cost = 10*0.46 - 4*0.40 = 4.60 - 1.60 = 3.00
    #   avg_cost = 3.00 / 6 = 0.50   (exact Decimal)
    fills = [
        _fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.46")),
        _fill(side="SELL", shares=Decimal("4"), price_exec=Decimal("0.40")),
    ]
    assert net_inventory(fills) == {"tok-yes": (Decimal("6"), Decimal("0.5"))}


def test_two_tokens_stay_separate():
    fills = [
        _fill(token_id="tok-a", side="BUY", shares=Decimal("10"), price_exec=Decimal("0.46")),
        _fill(token_id="tok-b", side="SELL", shares=Decimal("3"), price_exec=Decimal("0.20")),
    ]
    out = net_inventory(fills)
    # tok-b: net = -3 ; cost = -3*0.20 = -0.60 ; avg = -0.60 / -3 = 0.20
    assert out["tok-a"] == (Decimal("10"), Decimal("0.46"))
    assert out["tok-b"] == (Decimal("-3"), Decimal("0.20"))
    assert len(out) == 2


def test_flattened_token_nets_zero_with_zero_avg_cost():
    # Fully flattened: net 0 -> avg_cost is Decimal(0) by definition (no division by zero).
    fills = [
        _fill(side="BUY", shares=Decimal("5"), price_exec=Decimal("0.30")),
        _fill(side="SELL", shares=Decimal("5"), price_exec=Decimal("0.35")),
    ]
    assert net_inventory(fills) == {"tok-yes": (Decimal("0"), Decimal("0"))}


def test_no_fills_empty_inventory():
    assert net_inventory([]) == {}
