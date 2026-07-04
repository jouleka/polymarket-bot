"""S9 / POL-11 — evidence evaluator (walk-forward OOS split + MC-penalized margin + Brier/reliability)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.calibration.ledger import ForecastLedger
from polybot.maker.config import MakerConfig, DEFAULT_FEE_SCHEDULE
from polybot.harness.config import RampConfig
from polybot.harness.ledger import ShadowLedger


# ------------------------------- fixtures / factories -------------------------------

def _maker_config():
    # politics is INACTIVE -> taker_fee 0 -> rebate/fees 0; defaults zero lockup/taker-exit/dispute.
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _ramp_config(**over):
    # small, hand-computable windows: min_resolved=4, OOS window ceil(0.30*n), min_oos_resolved=2.
    base = dict(min_resolved=4, min_oos_resolved=2, oos_holdout_fraction=Decimal("0.30"),
                net_margin_min=Decimal("0"), mc_penalty=Decimal("0"))
    base.update(over)
    return RampConfig(**base)


def _shadow(tmp_path, name="s.db"):
    return ShadowLedger(str(tmp_path / name), MonotonicStamper())


def _forecast(tmp_path, name="f.db"):
    return ForecastLedger(str(tmp_path / name), MonotonicStamper())


class _FakeCalGate:
    """Tiny fake exposing only what evaluate_category consumes: k_for(cat) -> Decimal 0/1."""
    def __init__(self, k):
        self._k = k

    def k_for(self, category):
        return self._k


class _FakeMakerGate:
    """Tiny fake exposing only go_for(cat) -> bool."""
    def __init__(self, go):
        self._go = go

    def go_for(self, category):
        return self._go


def _win(ledger, tid, *, token, category="politics"):
    ledger.record_trade(tid, token_id=token, condition_id="c", category=category, side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    ledger.record_settlement(tid, status="WON", resolution_value=Decimal("1"))   # net +7.25


def _loss(ledger, tid, *, token, category="politics"):
    ledger.record_trade(tid, token_id=token, condition_id="c", category=category, side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    ledger.record_settlement(tid, status="LOST", resolution_value=Decimal("0"))  # net -2.75


def _evaluate(shadow, forecast, *, k=Decimal("1"), go=True, ramp=None, family_size=1):
    from polybot.harness.evidence import evaluate_category
    return evaluate_category("politics", shadow_ledger=shadow, forecast_ledger=forecast,
                             calibration_gate=_FakeCalGate(k), maker_gate=_FakeMakerGate(go),
                             ramp_config=ramp or _ramp_config(), maker_config=_maker_config(),
                             family_size=family_size)


def test_oos_reads_the_recent_window_not_the_full_sample(tmp_path):
    # HONESTY PIN. 4 WINS recorded FIRST (older by settled_at), then 2 LOSSES (most recent).
    # n_resolved=6 -> n_oos=ceil(0.30*6)=2 -> the OOS window is the two LOSSES.
    #   net_full = 4*7.25 + 2*(-2.75) = 29.00 - 5.50 = 23.50  (POSITIVE)
    #   net_oos  = 2*(-2.75) = -5.50                            (NEGATIVE — the recent rows bleed)
    # required_margin = 0 (net_margin_min 0, mc_penalty 0, family_size 1).
    # oos_positive = (n_oos>=2) and (net_oos > 0) = (True) and (-5.50>0 -> False) = False -> ready False.
    # A mutation reading net_full (23.50) instead of net_oos (-5.50) would flip oos_positive True and,
    # with k=1/go=True and n_resolved(6)>=min_resolved(4), flip ready True -> THIS TEST KILLS THAT MUTATION.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(4):
        _win(shadow, f"w{i}", token=f"tw{i}")
    for i in range(2):
        _loss(shadow, f"l{i}", token=f"tl{i}")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 6
    assert rep.n_oos == 2
    assert rep.net_full == Decimal("23.50")
    assert rep.net_oos == Decimal("-5.50")
    assert rep.oos_positive is False   # reads net_oos, not net_full
    assert rep.ready is False


def test_all_gates_cleared_yields_ready_true(tmp_path):
    # 6 WINS -> n_resolved=6 (>=min_resolved 4), n_oos=2.
    #   net_full = 6*7.25 = 43.50 ; net_oos = 2*7.25 = 14.50 (> required_margin 0).
    # Forecast OOS: 2 well-calibrated market-beating forecasts (see below) -> brier_skill 0.9375 (>0),
    #   reliability 0.01 (<= reliability_max 0.03). k=1, go=True. -> ready True.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    # forecast honest window: f1 WON bot p 0.90 vs mid 0.60 ; f2 LOST bot p 0.10 vs mid 0.40.
    #   bot_brier = ((0.90-1)^2+(0.10-0)^2)/2 = (0.01+0.01)/2 = 0.01
    #   mkt_brier = ((0.60-1)^2+(0.40-0)^2)/2 = (0.16+0.16)/2 = 0.16
    #   brier_skill = 1 - 0.01/0.16 = 0.9375 ; reliability: bin9 (0.9-1)^2 + bin1 (0.1-0)^2, each wt 1/2 = 0.01
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 6 and rep.n_oos == 2
    assert rep.net_full == Decimal("43.50")
    assert rep.net_oos == Decimal("14.50")
    assert rep.brier_skill == Decimal("0.9375")
    assert rep.reliability == Decimal("0.01000")
    assert rep.oos_positive is True
    assert rep.calibration_ok is True
    assert rep.maker_ok is True
    assert rep.ready is True


def test_mc_penalty_inflates_required_margin_by_family_size(tmp_path):
    # required_margin = net_margin_min + mc_penalty*(family_size - 1).
    # mc_penalty=10, net_margin_min=0. net_oos = 14.50 (2 wins in the OOS window of a 6-win sample).
    #   family_size=1 -> required 0  -> 14.50 > 0  True  -> oos_positive True.
    #   family_size=3 -> required 20 -> 14.50 > 20 False -> oos_positive False (MC discipline bites).
    def build():
        shadow, forecast = _shadow(tmp_path, f"s{build.n}.db"), _forecast(tmp_path, f"f{build.n}.db")
        build.n += 1
        for i in range(6):
            _win(shadow, f"w{i}", token=f"tw{i}")
        return shadow, forecast
    build.n = 0
    ramp = _ramp_config(mc_penalty=Decimal("10"))

    s1, f1 = build()
    rep1 = _evaluate(s1, f1, ramp=ramp, family_size=1)
    assert rep1.required_margin == Decimal("0")
    assert rep1.net_oos == Decimal("14.50")
    assert rep1.oos_positive is True

    s3, f3 = build()
    rep3 = _evaluate(s3, f3, ramp=ramp, family_size=3)
    assert rep3.required_margin == Decimal("20")
    assert rep3.net_oos == Decimal("14.50")
    assert rep3.oos_positive is False
    assert rep3.ready is False


def test_below_min_resolved_is_not_ready(tmp_path):
    # 3 WINS -> n_resolved=3 < min_resolved 4. (n_oos=ceil(0.30*3)=1 < min_oos_resolved 2 too.)
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(3):
        _win(shadow, f"w{i}", token=f"tw{i}")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 3
    assert rep.ready is False


def test_k_zero_makes_calibration_not_ok_and_not_ready(tmp_path):
    # ready-shaped 6-win sample + a passing forecast window, but k=0 (calibration gate NO-GO).
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("0"), go=True)
    assert rep.k == Decimal("0")
    assert rep.oos_positive is True         # the OOS net still clears
    assert rep.calibration_ok is False      # k==0 zeroes it
    assert rep.ready is False


def test_go_false_makes_maker_not_ok_and_not_ready(tmp_path):
    # ready-shaped sample + passing forecasts + k=1, but the maker gate says NO-GO.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=False)
    assert rep.maker_go is False
    assert rep.maker_ok is False
    assert rep.calibration_ok is True
    assert rep.ready is False


def test_net_oos_exactly_at_required_margin_is_not_positive_strict(tmp_path):
    # STRICT >. net_oos = 14.50 (2 wins); set net_margin_min = 14.50 so required_margin == 14.50.
    #   oos_positive = 14.50 > 14.50 = False -> ready False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    ramp = _ramp_config(net_margin_min=Decimal("14.50"))
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True, ramp=ramp)
    assert rep.net_oos == Decimal("14.50")
    assert rep.required_margin == Decimal("14.50")
    assert rep.oos_positive is False
    assert rep.ready is False
