"""S8 / POL-10 — maker tracker + gate (binary GO/NO-GO over the honest net-of-cost sample)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerTracker
from polybot.maker.ledger import MakerLedger


def _ledger(path):
    return MakerLedger(str(path), MonotonicStamper())


def _cfg(**kw):
    kw.setdefault("fee_schedule", DEFAULT_FEE_SCHEDULE)
    return MakerConfig(**kw)


_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


def _fill(ledger, fill_id, *, category, side, shares, price, mid, reward, status=None):
    ledger.record_fill(fill_id, token_id=f"tok-{fill_id}", condition_id="c", category=category,
                       side=side, shares=Decimal(shares), price_exec=Decimal(price),
                       fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    if status is not None:
        ledger.record_settlement(fill_id, status=status, resolution_value=_VALUE[status])


def test_cold_category_reports_none_and_no_go(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        r = MakerTracker(l, _cfg()).report_for("sports")
    assert r.category == "sports"
    assert r.n_settled == 0 and r.n_disputed == 0 and r.n_void == 0
    assert r.reward is None and r.rebate is None and r.spread_capture is None
    assert r.adverse_selection is None and r.fees is None
    assert r.lockup_cost is None and r.dispute_haircut is None
    assert r.net is None and r.go is False


def test_full_report_hand_computed_on_active_sports(tmp_path):
    """Every leg of the pinned derivation, hand-computed over 3 settled sports fills.

    Fills (sports: active, fee_rate 0.03, exponent 1):
      f1 BUY  10 @ 0.40, mid 0.42, reward 0.05, WON  (mark 1)
      f2 BUY  20 @ 0.60, mid 0.61, reward 0.07, LOST (mark 0)
      f3 SELL 10 @ 0.50, mid 0.48, reward 0.03, WON  (mark 1)
    Config: rebate_fraction 0.20 (default), forced_taker_exit_p 0.10, lockup_rate 0.01,
            dispute_p 0.02.
    Arithmetic (each checked by hand, twice):
      reward  = 0.05 + 0.07 + 0.03                                   = 0.15
      cf_1    = 10*0.03*0.40*(1-0.40) = 0.12*0.60                    = 0.072
      cf_2    = 20*0.03*0.60*(1-0.60) = 0.36*0.40                    = 0.144
      cf_3    = 10*0.03*0.50*(1-0.50) = 0.15*0.50                    = 0.075
      sum cf  = 0.072 + 0.144 + 0.075                                = 0.291
      rebate  = 0.20 * 0.291                                         = 0.0582
      spread  = +10*(0.42-0.40) + 20*(0.61-0.60) - 10*(0.48-0.50)
              = 0.20 + 0.20 + 0.20                                   = 0.60
      adverse = +10*(0.40-1) + 20*(0.60-0) - 10*(0.50-1)
              = -6.00 + 12.00 + 5.00                                 = 11.00
      fees    = 0.10 * 0.291                                         = 0.0291
      notional= 10*0.40 + 20*0.60 + 10*0.50 = 4 + 12 + 5             = 21.00
      lockup  = 0.01 * 21.00                                         = 0.21
      dispute = 0.02 * 21.00                                         = 0.42
      net     = 0.15 + 0.0582 + 0.60 - 11.00 - 0.0291 - 0.21 - 0.42
              = 0.8082 - 11.6591                                     = -10.8509
    """
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "f1", category="sports", side="BUY", shares="10", price="0.40", mid="0.42",
              reward="0.05", status="WON")
        _fill(l, "f2", category="sports", side="BUY", shares="20", price="0.60", mid="0.61",
              reward="0.07", status="LOST")
        _fill(l, "f3", category="sports", side="SELL", shares="10", price="0.50", mid="0.48",
              reward="0.03", status="WON")
        cfg = _cfg(min_samples=3, forced_taker_exit_p=Decimal("0.10"),
                   lockup_rate=Decimal("0.01"), dispute_p=Decimal("0.02"))
        r = MakerTracker(l, cfg).report_for("sports")
    assert r.n_settled == 3 and r.n_disputed == 0 and r.n_void == 0
    assert r.reward == Decimal("0.15")
    assert r.rebate == Decimal("0.0582")
    assert r.spread_capture == Decimal("0.60")
    assert r.adverse_selection == Decimal("11.00")
    assert r.fees == Decimal("0.0291")
    assert r.lockup_cost == Decimal("0.21")
    assert r.dispute_haircut == Decimal("0.42")
    assert r.net == Decimal("-10.8509")
    # the identity is structural -- re-assert it over the report's own legs:
    assert r.net == (r.reward + r.rebate + r.spread_capture - r.adverse_selection
                     - r.fees - r.lockup_cost - r.dispute_haircut)
    assert r.go is False
