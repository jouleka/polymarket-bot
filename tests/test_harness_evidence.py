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
    # Forecast: a market-beating, well-calibrated OOS window -> brier_skill 0.9375 (>0),
    #   reliability 0.01 (<= reliability_max 0.03). k=1, go=True. -> ready True.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    # Two honest forecasts recorded (g1 WON p0.90/mid0.60 ; g2 LOST p0.10/mid0.40), but the OOS
    # forecast window is the MOST-RECENT ceil(0.30*2)=1 forecast (g2 only); g1, its symmetric partner,
    # is NOT scored. The asserted values are the SINGLE-ROW Brier over g2 (they equal the 2-row average
    # only because the pair is symmetric):
    #   bot_brier = (0.10-0)^2 = 0.01 ; mkt_brier = (0.40-0)^2 = 0.16
    #   brier_skill = 1 - 0.01/0.16 = 0.9375 ; reliability: bin1 (0.10-0)^2, weight 1 = 0.01
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


def test_reliability_over_ceiling_makes_calibration_not_ok(tmp_path):
    # ready-shaped shadow sample + k=1/go=True, but a MIS-calibrated forecast window. Two honest
    # forecasts recorded (b1 LOST p0.90 ; b2 WON p0.10), but the OOS forecast window is the MOST-RECENT
    # ceil(0.30*2)=1 forecast (b2 only); b1 is NOT scored. The asserted reliability is the SINGLE-ROW
    # value over b2 (equals the 2-row average only because the pair is symmetric):
    #   reliability = bin1 (0.10-1)^2, weight 1 = 0.81, which exceeds reliability_max 0.03
    #   -> calibration_ok False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("b1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("b1", "LOST")
    forecast.record_forecast("b2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("b2", "WON")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.reliability == Decimal("0.81000")
    assert rep.reliability > Decimal("0.03")  # genuinely over the reliability_max ceiling
    assert rep.calibration_ok is False
    assert rep.ready is False


def test_non_positive_brier_skill_makes_calibration_not_ok(tmp_path):
    # bot WORSE than the market baseline -> brier_skill <= 0. Two honest forecasts recorded
    # (s1 WON p0.20/mid0.80 ; s2 LOST p0.80/mid0.20), but the OOS forecast window is the MOST-RECENT
    # ceil(0.30*2)=1 forecast (s2 only); s1 is NOT scored. The asserted brier_skill is the SINGLE-ROW
    # value over s2 (equals the 2-row average only because the pair is symmetric):
    #   bot_brier = (0.80-0)^2 = 0.64 ; mkt_brier = (0.20-0)^2 = 0.04
    #   brier_skill = 1 - 0.64/0.04 = -15  (<= 0) -> calibration_ok False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("s1", category="politics", condition_id="c", p=Decimal("0.20"),
                             market_mid=Decimal("0.80"))
    forecast.record_resolution("s1", "WON")
    forecast.record_forecast("s2", category="politics", condition_id="c", p=Decimal("0.80"),
                             market_mid=Decimal("0.20"))
    forecast.record_resolution("s2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.brier_skill == Decimal("-15")
    assert rep.calibration_ok is False
    assert rep.ready is False


def test_cold_ledger_is_not_ready_with_none_stats(tmp_path):
    # no settled shadow rows AND no resolved forecasts -> fail-closed.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 0
    assert rep.n_oos == 0
    assert rep.net_full is None
    assert rep.net_oos is None
    assert rep.brier_skill is None
    assert rep.reliability is None
    assert rep.oos_positive is False
    assert rep.calibration_ok is False   # None brier_skill/reliability -> not ok
    assert rep.ready is False


def test_disputed_and_void_are_counted_but_excluded_from_the_honest_sample(tmp_path):
    # 4 WINS (honest) + 1 DISPUTED + 1 VOID (shadow ledger uses "DISPUTED", NOT "DISPUTED_LOST").
    #   n_resolved counts only the 4 honest wins; n_disputed = 2 (DISPUTED + VOID).
    #   net_full = 4*7.25 = 29.00 (the DISPUTED/VOID rows contribute NOTHING to net).
    #   n_oos = ceil(0.30*4)=2 ; net_oos = 2*7.25 = 14.50.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(4):
        _win(shadow, f"w{i}", token=f"tw{i}")
    shadow.record_trade("d1", token_id="td1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    shadow.record_settlement("d1", status="DISPUTED", resolution_value=None)
    shadow.record_trade("v1", token_id="tv1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    shadow.record_settlement("v1", status="VOID", resolution_value=None)
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 4
    assert rep.n_disputed == 2
    assert rep.net_full == Decimal("29.00")
    assert rep.net_oos == Decimal("14.50")
    assert rep.n_oos == 2


def test_unknown_shadow_status_fails_loud(tmp_path):
    # Insert a corrupt status directly via raw sqlite (bypassing record_settlement's guard) to
    # simulate DB corruption / an untaught 5th status. evaluate_category must fail LOUD, mirroring
    # MakerTracker's exhaustive-status ValueError -- a status it cannot classify must never silently
    # vanish from the honest/DISPUTED accounting.
    import sqlite3

    path = str(tmp_path / "corrupt.db")
    shadow = ShadowLedger(path, MonotonicStamper())
    _win(shadow, "w0", token="tw0")   # one legitimate settled row so the table + schema exist
    shadow.record_trade("x1", token_id="tx1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE shadow_trades SET status=?, settled_at=? WHERE trade_id=?",
                 ("MAYBE", 999, "x1"))
    conn.commit()
    conn.close()

    forecast = _forecast(tmp_path)
    with pytest.raises(ValueError, match="status"):
        _evaluate(shadow, forecast, k=Decimal("1"), go=True)


def test_family_size_below_one_fails_loud(tmp_path):
    # DESIGN + PLAN annotate family_size >= 1. family_size=0 makes
    #   required_margin = net_margin_min + mc_penalty*(0-1) = NEGATIVE, so a net-NEGATIVE OOS window
    #   would clear the "positive-with-margin" oos_positive gate -- a doctrine inversion. Fail LOUD
    #   at the top of evaluate_category instead of ever computing a negative required_margin.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    with pytest.raises(ValueError, match="family_size"):
        _evaluate(shadow, forecast, k=Decimal("1"), go=True, family_size=0)


def test_oos_floor_gate_in_isolation_below_min_oos_resolved_is_not_ready(tmp_path):
    # ISOLATE the n_oos >= min_oos_resolved floor from the n_resolved gate. A config where
    # oos_holdout_fraction*min_resolved < min_oos_resolved decouples them:
    #   min_resolved=10, oos_holdout_fraction=0.10, min_oos_resolved=5.
    # 10 honest WON rows -> n_resolved=10 (>= min_resolved 10, so the n_resolved gate PASSES) but
    #   n_oos = ceil(0.10*10) = 1 < min_oos_resolved 5, so the OOS-floor gate ALONE must keep
    #   oos_positive/ready False -- even though net_oos (a lone winner) is hugely positive (+7.25).
    # Deleting the `n_oos >= min_oos_resolved` conjunct would flip oos_positive/ready to True.
    ramp = _ramp_config(min_resolved=10, oos_holdout_fraction=Decimal("0.10"), min_oos_resolved=5)
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(10):
        _win(shadow, f"w{i}", token=f"tw{i}")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True, ramp=ramp)
    assert rep.n_resolved == 10           # the n_resolved gate is satisfied
    assert rep.n_oos == 1
    assert rep.n_oos < 5                   # below min_oos_resolved
    assert rep.net_oos == Decimal("7.25")  # a hugely positive OOS net -- not why it fails
    assert rep.oos_positive is False       # the OOS-floor gate alone blocks it
    assert rep.ready is False


def test_brier_skill_subgate_in_isolation_reliability_passing(tmp_path):
    # ISOLATE the brier_skill > 0 sub-gate: the bot is UNINFORMATIVE-BUT-CALIBRATED (p=0.50 on a
    # balanced WON/LOST pair -> reliability 0, well within reliability_max) while a SHARP correct
    # market (mid 0.95 on the WON, 0.05 on the LOST) crushes it on Brier:
    #   bot_brier = ((0.50-1)^2+(0.50-0)^2)/2 = 0.25 ; mkt_brier = ((0.95-1)^2+(0.05-0)^2)/2 = 0.0025
    #   brier_skill = 1 - 0.25/0.0025 = -99  (<= 0)   ; reliability = bin5 (0.50-0.50)^2 = 0
    # oos_holdout_fraction=0.60 so the OOS forecast window is BOTH rows (ceil(0.60*2)=2), scoring the
    # balanced pair. calibration_ok must be False DRIVEN BY brier_skill (reliability 0 passes). The
    # shadow side is ready-shaped (6 wins) so calibration_ok is the ONLY failing gate.
    #   Dropping the `brier_skill > 0` conjunct would flip calibration_ok True (reliability already ok).
    ramp = _ramp_config(oos_holdout_fraction=Decimal("0.60"))
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("u1", category="politics", condition_id="c", p=Decimal("0.50"),
                             market_mid=Decimal("0.95"))
    forecast.record_resolution("u1", "WON")
    forecast.record_forecast("u2", category="politics", condition_id="c", p=Decimal("0.50"),
                             market_mid=Decimal("0.05"))
    forecast.record_resolution("u2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True, ramp=ramp)
    assert rep.brier_skill == Decimal("-99")           # <= 0: the sub-gate that fails
    assert rep.reliability == Decimal("0")             # <= reliability_max 0.03: PASSES
    assert rep.reliability <= Decimal("0.03")
    assert rep.oos_positive is True                    # shadow side clears -> not why it fails
    assert rep.calibration_ok is False                 # driven by brier_skill alone
    assert rep.ready is False
