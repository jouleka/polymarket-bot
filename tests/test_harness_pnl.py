"""S9 / POL-11 — windowed net-of-everything PnL (the S8 identity over a settled-row window)."""

from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.harness.ledger import ShadowTradeRecord
from polybot.harness.pnl import window_net


def _cfg(**over):
    # ACTIVE-fee config: rebate/fees/lockup/dispute all nonzero so every leg is exercised.
    base = dict(fee_schedule=DEFAULT_FEE_SCHEDULE, rebate_fraction=Decimal("0.20"),
                forced_taker_exit_p=Decimal("0.10"), lockup_rate=Decimal("0.01"),
                dispute_p=Decimal("0.02"))
    base.update(over)
    return MakerConfig(**base)


def _row(trade_id, *, token, category, side, shares, fill_price, fill_mid, reward,
         status, resolution_value):
    # settled ShadowTradeRecord (created_at/settled_at are irrelevant to window_net).
    return ShadowTradeRecord(
        trade_id=trade_id, token_id=token, condition_id="c", category=category, side=side,
        shares=Decimal(shares), fill_price=Decimal(fill_price), fill_mid=Decimal(fill_mid),
        reward_accrued=Decimal(reward), created_at=1, status=status,
        resolution_value=None if resolution_value is None else Decimal(resolution_value),
        settled_at=1)


_SPORTS_WINDOW = [
    _row("tA", token="tA", category="sports", side="BUY", shares="10", fill_price="0.40",
         fill_mid="0.50", reward="0.25", status="WON", resolution_value="1"),
    _row("tB", token="tB", category="sports", side="SELL", shares="20", fill_price="0.60",
         fill_mid="0.55", reward="0.30", status="LOST", resolution_value="0"),
    _row("tC", token="tC", category="sports", side="BUY", shares="8", fill_price="0.30",
         fill_mid="0.33", reward="0.10", status="LOST", resolution_value="0"),
]


def test_window_net_equals_the_s8_identity_over_an_active_category_window():
    # hand-computed above: reward .65 + rebate .05328 + spread 2.24 - adverse(-15.60)
    #   - fees .02664 - lockup .184 - dispute .368 = 17.96464
    assert window_net(_SPORTS_WINDOW, maker_config=_cfg()) == Decimal("17.96464000")


def test_window_net_over_a_free_category_zeroes_rebate_and_fees():
    # same rows on the FREE geopolitics category -> taker_fee 0 -> rebate 0, fees 0;
    # lockup/dispute (keyed off notional) unchanged. net = 17.9380.
    free_window = [
        _row("gA", token="gA", category="geopolitics", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WON",
             resolution_value="1"),
        _row("gB", token="gB", category="geopolitics", side="SELL", shares="20",
             fill_price="0.60", fill_mid="0.55", reward="0.30", status="LOST",
             resolution_value="0"),
        _row("gC", token="gC", category="geopolitics", side="BUY", shares="8",
             fill_price="0.30", fill_mid="0.33", reward="0.10", status="LOST",
             resolution_value="0"),
    ]
    assert window_net(free_window, maker_config=_cfg()) == Decimal("17.9380")


def test_window_net_uses_settled_fractional_mark():
    fractional = _row(
        "fractional", token="fractional", category="geopolitics", side="BUY",
        shares="10", fill_price="0.4", fill_mid="0.4", reward="0", status="SETTLED",
        resolution_value="0.5",
    )
    assert window_net(
        [fractional], maker_config=_cfg(lockup_rate=Decimal(0), dispute_p=Decimal(0))
    ) == Decimal("1.0")


def test_window_net_excludes_disputed_and_void_rows():
    # a DISPUTED/VOID row whose inclusion WOULD change the net (big reward) must be skipped:
    # the net must equal the honest-only 17.96464000, not the naive-include -6.18536000.
    disputed = _row("tD", token="tD", category="sports", side="BUY", shares="50",
                    fill_price="0.50", fill_mid="0.50", reward="9.99", status="DISPUTED",
                    resolution_value=None)
    void = _row("tE", token="tE", category="sports", side="SELL", shares="30",
                fill_price="0.50", fill_mid="0.50", reward="7.00", status="VOID",
                resolution_value=None)
    window = _SPORTS_WINDOW + [disputed, void]
    assert window_net(window, maker_config=_cfg()) == Decimal("17.96464000")


def test_window_net_of_an_empty_window_is_zero():
    assert window_net([], maker_config=_cfg()) == Decimal(0)


def test_window_net_of_an_all_disputed_window_is_zero():
    only_disputed = [
        _row("tD", token="tD", category="sports", side="BUY", shares="50",
             fill_price="0.50", fill_mid="0.50", reward="9.99", status="DISPUTED",
             resolution_value=None),
        _row("tE", token="tE", category="sports", side="SELL", shares="30",
             fill_price="0.50", fill_mid="0.50", reward="7.00", status="VOID",
             resolution_value=None),
    ]
    assert window_net(only_disputed, maker_config=_cfg()) == Decimal(0)


def test_window_net_raises_on_divergent_resolution_marks_for_one_token():
    # two honest rows on the SAME token with DISTINCT resolution values is ledger
    # corruption -- fail loud, never silently last-wins (mirrors the S8d pin).
    diverging = [
        _row("x1", token="tSAME", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WON",
             resolution_value="1"),
        _row("x2", token="tSAME", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="LOST",
             resolution_value="0"),
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        window_net(diverging, maker_config=_cfg())


def test_window_net_fails_loud_on_an_unhandled_status():
    # a status outside {WON,LOST,DISPUTED,VOID} is corruption -- fail loud, never silently
    # drop it from the accounting (exact MakerTracker.report_for parity).
    window = [
        _row("h1", token="h1", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WON",
             resolution_value="1"),
        _row("w1", token="w1", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WEIRD",
             resolution_value=None),
    ]
    with pytest.raises(ValueError, match="unhandled"):
        window_net(window, maker_config=_cfg())


def test_window_net_is_negative_when_adverse_selection_dominates():
    # the "safe strategy bleeds invisibly" shape: a BUY at 0.40 that resolves LOST (mark 0)
    # books a large adverse cost that swamps reward+spread -> net-NEGATIVE.
    bleed = [
        _row("nA", token="nA", category="sports", side="BUY", shares="100",
             fill_price="0.40", fill_mid="0.41", reward="0.05", status="LOST",
             resolution_value="0"),
    ]
    net = window_net(bleed, maker_config=_cfg())
    assert net == Decimal("-40.07800000")
    assert net < 0
