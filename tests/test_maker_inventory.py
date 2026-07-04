"""S8 / POL-10 — maker inventory (MakerFill validation + BUY/SELL folding + adverse-selection mark-out)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from polybot.maker.inventory import _SGN, MakerFill


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
