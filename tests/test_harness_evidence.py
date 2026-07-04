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
