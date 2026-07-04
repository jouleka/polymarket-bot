"""S8 / POL-10 — whole-slice e2e (design §7.3): ledger -> tracker -> gate -> quote policy."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerGate
from polybot.maker.ledger import MakerLedger
from polybot.maker.quote_policy import PULL


_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


def _fill(ledger, fill_id, *, category, side, shares, price, mid, reward, status=None):
    ledger.record_fill(fill_id, token_id=f"tok-{fill_id}", condition_id="c", category=category,
                       side=side, shares=Decimal(shares), price_exec=Decimal(price),
                       fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    if status is not None:
        ledger.record_settlement(fill_id, status=status, resolution_value=_VALUE[status])


def test_lifecycle_go_progression_and_honest_breakdown(tmp_path):
    """One MakerLedger fed a sports-category shadow session: fills accrue, settlements land
    (WON/LOST + one DISPUTED), and go flips True only once the sample clears min_samples AND
    net beats the margin -- with the full honest breakdown verified leg by leg.

    Config: min_samples 4, net_margin_min 0.05, rebate_fraction 0.20 (default),
            forced_taker_exit_p 0.10, lockup_rate/dispute_p 0 (defaults).
    Fills (sports: active, fee_rate 0.03, exponent 1):
      f1 BUY  10 @ 0.48, mid 0.50, reward 0.05, WON  (mark 1)
      f2 SELL 10 @ 0.52, mid 0.50, reward 0.05, LOST (mark 0)
      f3 BUY  10 @ 0.30, mid 0.31, reward 0.05, LOST (mark 0)
      fD BUY  10 @ 0.90, mid 0.90, reward 0.01, DISPUTED  (excluded from every leg)
      f4 SELL 10 @ 0.60, mid 0.58, reward 0.05, LOST (mark 0)
    Final breakdown (hand-computed, checked twice):
      reward  = 4 * 0.05                                              = 0.20
      cf_1 = 10*0.03*0.48*0.52 = 0.07488 ; cf_2 = 10*0.03*0.52*0.48   = 0.07488
      cf_3 = 10*0.03*0.30*0.70 = 0.063   ; cf_4 = 10*0.03*0.60*0.40   = 0.072
      sum cf  = 0.07488 + 0.07488 + 0.063 + 0.072                     = 0.28476
      rebate  = 0.20 * 0.28476                                        = 0.056952
      spread  = +10*(0.50-0.48) - 10*(0.50-0.52) + 10*(0.31-0.30) - 10*(0.58-0.60)
              = 0.20 + 0.20 + 0.10 + 0.20                             = 0.70
      adverse = +10*(0.48-1) - 10*(0.52-0) + 10*(0.30-0) - 10*(0.60-0)
              = -5.2 - 5.2 + 3.0 - 6.0                                = -13.4   (favorable)
      fees    = 0.10 * 0.28476                                        = 0.028476
      net     = 0.20 + 0.056952 + 0.70 - (-13.4) - 0.028476           = 14.328476
    Interim (f1..f3 settled, n=3): reward 0.15 ; sum cf 0.21276 ; rebate 0.042552 ;
      spread 0.50 ; adverse -7.4 ; fees 0.021276 ; net = 8.071276 > margin -- but n 3 < 4.
    """
    cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, min_samples=4,
                      net_margin_min=Decimal("0.05"), forced_taker_exit_p=Decimal("0.10"))
    with MakerLedger(str(tmp_path / "e2e.db"), MonotonicStamper()) as l:
        g = MakerGate(l, cfg)
        assert g.go_for("sports") is False  # cold: nothing settled yet

        _fill(l, "f1", category="sports", side="BUY", shares="10", price="0.48", mid="0.50",
              reward="0.05", status="WON")
        _fill(l, "f2", category="sports", side="SELL", shares="10", price="0.52", mid="0.50",
              reward="0.05", status="LOST")
        _fill(l, "f3", category="sports", side="BUY", shares="10", price="0.30", mid="0.31",
              reward="0.05", status="LOST")
        _fill(l, "fD", category="sports", side="BUY", shares="10", price="0.90", mid="0.90",
              reward="0.01", status="DISPUTED")

        r3 = g.report_for("sports")
        assert r3.n_settled == 3 and r3.n_disputed == 1
        assert r3.net == Decimal("8.071276")
        assert r3.net > cfg.net_margin_min and r3.go is False  # healthy net, sample not cleared

        _fill(l, "f4", category="sports", side="SELL", shares="10", price="0.60", mid="0.58",
              reward="0.05", status="LOST")
        r4 = g.report_for("sports")
        assert r4.n_settled == 4 and r4.n_disputed == 1 and r4.n_void == 0
        assert r4.reward == Decimal("0.20")
        assert r4.rebate == Decimal("0.056952")
        assert r4.spread_capture == Decimal("0.70")
        assert r4.adverse_selection == Decimal("-13.4")
        assert r4.fees == Decimal("0.028476")
        assert r4.lockup_cost == Decimal("0") and r4.dispute_haircut == Decimal("0")
        assert r4.net == Decimal("14.328476")
        assert r4.go is True and g.go_for("sports") is True
