"""S9 / POL-11 — whole-slice e2e (design §7.3): the REAL stack ShadowLedger -> evaluate_category
-> RampController. No mocks except the k/go gates + a controlled forecast_ledger (tiny fakes), so
the OUT-OF-SAMPLE net-of-everything shadow PnL carries the honesty assertion.

THE STRUCTURAL HONESTY PIN (§5 invariant 1): a category whose FULL sample is strongly net-positive
but whose most-recent OOS window is net-NEGATIVE must NOT be ready and must NOT promote -- the
controller advances on the OUT-OF-SAMPLE net, never the (gross-looking) full-sample net. The
net_oos-vs-net_full and net-vs-gross mutations are killed here.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig
from polybot.harness.evidence import evaluate_category
from polybot.harness.ledger import ShadowLedger
from polybot.harness.ramp_controller import SHADOW, RampController
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig

from polybot.calibration.ledger import ForecastRecord


_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


class _FakeCalibGate:
    """Tiny fake (design §7.3 allows fakes for the k/go gates): whole-sample k == 1."""
    def k_for(self, category):
        return Decimal("1")


class _FakeMakerGate:
    def go_for(self, category):
        return True


class _FakeForecastLedger:
    """A controlled forecast substrate so the calibration side (brier_skill/reliability) is pinned
    while the SHADOW-side OOS PnL stays fully real. Rows are honest WON/LOST forecasts where the
    bot beats the market mid; evaluate_category holds out the recent ceil(0.30*n_f) as f_oos.
    (Hand-verified against calibration/scoring.py: brier_skill 0.9375, reliability 0.01.)"""
    def __init__(self, rows):
        self._rows = rows

    def resolved(self, category=None):
        return [r for r in self._rows if category is None or r.category == category]


def _forecast(fid, *, p, mid, status, at, category="sports"):
    return ForecastRecord(forecast_id=fid, category=category, condition_id="c", p=Decimal(p),
                          market_mid=Decimal(mid), created_at=at,
                          resolution_status=status, resolved_at=at)


def _good_forecasts():
    # Bot sharp & correct (0.90 on WON, 0.10 on LOST); market mushy (0.60/0.40). 6 honest rows,
    # time-ordered by resolved_at so the OOS forecast slice is the recent tail.
    return [
        _forecast("g1", p="0.90", mid="0.60", status="WON", at=1),
        _forecast("g2", p="0.10", mid="0.40", status="LOST", at=2),
        _forecast("g3", p="0.90", mid="0.60", status="WON", at=3),
        _forecast("g4", p="0.10", mid="0.40", status="LOST", at=4),
        _forecast("g5", p="0.90", mid="0.60", status="WON", at=5),
        _forecast("g6", p="0.10", mid="0.40", status="LOST", at=6),
    ]


def _record(ledger, tid, *, side, shares, price, mid, reward, status):
    ledger.record_trade(tid, token_id=f"tok-{tid}", condition_id="c", category="sports",
                        side=side, shares=Decimal(shares), fill_price=Decimal(price),
                        fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    ledger.record_settlement(tid, status=status, resolution_value=_VALUE[status])


def test_full_sample_clears_but_oos_negative_stays_shadow(tmp_path):
    """6 honest shadow trades across a time span: 3 early strong winners (BUY WON at low prices ->
    large favorable mark-out) + 1 DISPUTED (excluded) + 3 recent toxic losers (BUY LOST). The FULL
    sample is strongly net-positive (favorable early adverse dominates) BUT the recent OOS window
    (the 3 losers) is net-NEGATIVE. evaluate_category reads net_OOS -> oos_positive False -> ready
    False; RampController stays SHADOW, promote False. The gross/in-sample illusion is caught.

    Config: RampConfig(min_resolved=6, oos_holdout_fraction=0.5, min_oos_resolved=3) so
            n_oos = ceil(0.5*6) = 3 = the toxic losers; the sample floor (6) IS met -- only the
            OOS net stops it. MakerConfig defaults (rebate 0.20; forced_taker_exit_p/lockup/
            dispute 0; min_samples irrelevant -- maker_go is faked True).

    Hand-computed net-of-everything (sports fee_rate 0.03 exp 1; checked twice):
      FULL honest (6 rows):
        reward = 6 * 0.05                                                       = 0.30
        cf: winners 100*0.03*p*(1-p) at 0.40/0.45/0.50 = 0.72 + 0.7425 + 0.75  = 2.2125
            losers  10*0.03*p*(1-p) at 0.60/0.65/0.55  = 0.072+0.06825+0.07425 = 0.2145
            Σcf = 2.4270 ; rebate = 0.20*2.4270                                = 0.48540000
        spread = 100*(0.01)*3 (winners, each mid-price=0.01) + 10*(0.01)*3     = 3.00 + 0.30 = 3.30
        adverse (BUY: shares*(price-mark)):
          winners mark 1: 100*(0.40-1)+100*(0.45-1)+100*(0.50-1) = -60-55-50   = -165
          losers  mark 0: 10*(0.60)+10*(0.65)+10*(0.55)          = 6+6.5+5.5   = +18
          Σadverse = -165 + 18                                                 = -147.00
        net = 0.30 + 0.48540000 + 3.30 - (-147.00) - 0 - 0 - 0                = 151.08540000  (>0)
      OOS (recent 3 losers only):
        reward = 3*0.05 = 0.15 ; Σcf = 0.2145 ; rebate = 0.04290000
        spread = 10*0.01*3 = 0.30 ; adverse = +18.00
        net_oos = 0.15 + 0.04290000 + 0.30 - 18.00                            = -17.50710000  (<0)
    """
    rc_cfg = RampConfig(min_resolved=6, oos_holdout_fraction=Decimal("0.5"), min_oos_resolved=3)
    mk_cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    with ShadowLedger(str(tmp_path / "shadow.db"), MonotonicStamper()) as sl:
        # early strong winners (in-sample)
        _record(sl, "w1", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
        _record(sl, "w2", side="BUY", shares="100", price="0.45", mid="0.46", reward="0.05", status="WON")
        _record(sl, "w3", side="BUY", shares="100", price="0.50", mid="0.51", reward="0.05", status="WON")
        # a DISPUTED in the middle -- excluded from every leg and from the honest count
        _record(sl, "wD", side="BUY", shares="10", price="0.90", mid="0.90", reward="0.01", status="DISPUTED")
        # recent toxic losers (the OOS window by settled_at)
        _record(sl, "l1", side="BUY", shares="10", price="0.60", mid="0.61", reward="0.05", status="LOST")
        _record(sl, "l2", side="BUY", shares="10", price="0.65", mid="0.66", reward="0.05", status="LOST")
        _record(sl, "l3", side="BUY", shares="10", price="0.55", mid="0.56", reward="0.05", status="LOST")

        ev = evaluate_category(
            "sports", shadow_ledger=sl, forecast_ledger=_FakeForecastLedger(_good_forecasts()),
            calibration_gate=_FakeCalibGate(), maker_gate=_FakeMakerGate(),
            ramp_config=rc_cfg, maker_config=mk_cfg, family_size=1)

        # The honest OOS breakdown: DISPUTED excluded from the honest count; the FULL sample is
        # strongly positive, but the OOS window (net_oos) is negative -> oos_positive False.
        assert ev.n_resolved == 6 and ev.n_disputed == 1 and ev.n_oos == 3
        assert ev.net_full == Decimal("151.08540000")   # gross-looking full sample (>0)
        assert ev.net_oos == Decimal("-17.50710000")    # the OUT-OF-SAMPLE truth (<0)
        assert ev.oos_positive is False                  # reads net_oos, NOT net_full
        assert ev.ready is False                         # the honesty spine: not ready

        controller = RampController(ramp_config=rc_cfg, caps=RiskCaps())
        healthy = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                         cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                         entry_price=Decimal("0.50")),))
        d = controller.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=healthy,
                              n_resolved_disputed=2, stress_episodes=1, breaker_tripped=False)
        assert d.stage == SHADOW
        assert d.promote_recommended is False            # cannot advance on gross/in-sample edge
        assert d.reason == "not_ready:oos"


def _winning_ledger(sl):
    # 6 honest winners across a time span (BUY WON at low prices -> favorable mark-out) + 1 DISPUTED
    # excluded. Recent OOS window (last 3 winners) ALSO clears margin > 0.
    _record(sl, "e1", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
    _record(sl, "e2", side="BUY", shares="100", price="0.45", mid="0.46", reward="0.05", status="WON")
    _record(sl, "e3", side="BUY", shares="100", price="0.50", mid="0.51", reward="0.05", status="WON")
    _record(sl, "eD", side="BUY", shares="10", price="0.90", mid="0.90", reward="0.01", status="DISPUTED")
    _record(sl, "e4", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
    _record(sl, "e5", side="BUY", shares="100", price="0.42", mid="0.43", reward="0.05", status="WON")
    _record(sl, "e6", side="BUY", shares="100", price="0.44", mid="0.45", reward="0.05", status="WON")


def test_ready_promotes_then_a_regression_ramps_down(tmp_path):
    """The winning path: 6 honest winners -> the OOS window (recent 3) clears margin AND k/go pass
    -> ready True. RampController.decide stays SHADOW (promotion past it is the human gate) but
    emits promote_recommended True. Then a REGRESSION (a tripped breaker, from a previously-promoted
    TINY_LIVE stage) -> ramp_down True. This is the full §7.3 arc.

    Config: RampConfig(min_resolved=6, oos_holdout_fraction=0.5, min_oos_resolved=3).
    Hand-computed OOS net (recent 3 winners e4/e5/e6, BUY WON marks 1; checked twice):
      reward = 3*0.05 = 0.15
      Σcf = 100*0.03*(0.40*0.60 + 0.42*0.58 + 0.44*0.56) = 100*0.03*(0.24+0.2436+0.2464)
          = 100*0.03*0.7300 = 2.190000 ; rebate = 0.20*2.190000 = 0.43800000
      spread = 100*0.01*3 = 3.00
      adverse (BUY mark 1) = 100*(0.40-1)+100*(0.42-1)+100*(0.44-1) = -60-58-56 = -174.00
      net_oos = 0.15 + 0.43800000 + 3.00 - (-174.00) = 177.58800000  (>0 -> clears margin 0)
    """
    rc_cfg = RampConfig(min_resolved=6, oos_holdout_fraction=Decimal("0.5"), min_oos_resolved=3)
    mk_cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    with ShadowLedger(str(tmp_path / "shadow.db"), MonotonicStamper()) as sl:
        _winning_ledger(sl)
        ev = evaluate_category(
            "sports", shadow_ledger=sl, forecast_ledger=_FakeForecastLedger(_good_forecasts()),
            calibration_gate=_FakeCalibGate(), maker_gate=_FakeMakerGate(),
            ramp_config=rc_cfg, maker_config=mk_cfg, family_size=1)
        assert ev.n_resolved == 6 and ev.n_oos == 3
        assert ev.net_oos == Decimal("177.58800000")    # OOS clears with margin
        assert ev.oos_positive is True
        assert ev.ready is True                          # every Stage-0 gate cleared

        controller = RampController(ramp_config=rc_cfg, caps=RiskCaps())
        healthy = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                         cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                         entry_price=Decimal("0.50")),))

        # Ready + tail (2 disputed >= 1, 1 episode >= 1) + stress survives + no breaker -> promote,
        # but the stage stays SHADOW (the operator's human ramp-up gate advances it, not decide()).
        d_ok = controller.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=healthy,
                                 n_resolved_disputed=2, stress_episodes=1, breaker_tripped=False)
        assert d_ok.promote_recommended is True
        assert d_ok.stage == SHADOW
        assert d_ok.ramp_down is False
        assert d_ok.reason == "promote_ok"

        # A subsequent REGRESSION: the category had been promoted to TINY_LIVE out-of-band, and now
        # a breaker trips -> ramp_down True (the automatic-tighten signal for the S4.7 ratchet).
        d_reg = controller.decide("sports", evidence=ev, current_stage="TINY_LIVE",
                                  portfolio=healthy, n_resolved_disputed=2, stress_episodes=1,
                                  breaker_tripped=True)
        assert d_reg.ramp_down is True
        assert d_reg.promote_recommended is False
        assert d_reg.reason == "ramp_down:breaker"
