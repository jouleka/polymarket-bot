"""S5 / POL-7 — the CalibrationGate facade (k_for + clamp_p) that S6 plugs into the ERS."""

from decimal import Decimal

from polybot.calibration.config import CalibrationConfig
from polybot.calibration.gate import CalibrationGate
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.prior import PriorEngine
from polybot.core.clock import MonotonicStamper

_PASSING = [(0.2, 0.5, "WON", 4), (0.2, 0.5, "LOST", 16),
            (0.8, 0.5, "WON", 16), (0.8, 0.5, "LOST", 4)]


def _ledger(path):
    return ForecastLedger(path, MonotonicStamper())


def _build(ledger, category, specs):
    i = 0
    for p, mid, status, count in specs:
        for _ in range(count):
            i += 1
            fid = f"{category}-{i}"
            ledger.record_forecast(fid, category=category, condition_id="c",
                                   p=Decimal(str(p)), market_mid=Decimal(str(mid)))
            ledger.record_resolution(fid, status)


def test_k_for_delegates_to_the_tracker(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING)
        gate = CalibrationGate(l, PriorEngine(), CalibrationConfig(min_n=20))
        assert gate.k_for("politics") == Decimal("1")


def test_clamp_p_uses_the_classified_prior_to_clamp_overconfidence(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        gate = CalibrationGate(l, PriorEngine(), CalibrationConfig())
        r = gate.clamp_p(Decimal("0.99"), Decimal("0.5"), question_text="Will the incumbent win?",
                         seconds_to_resolution=10 ** 9, corroborated=False)
        assert r.shrunk is True and r.p_clamped < Decimal("0.99")


def test_clamp_p_falls_back_to_market_only_with_no_reference_class(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        gate = CalibrationGate(l, PriorEngine(), CalibrationConfig())
        r = gate.clamp_p(Decimal("0.6"), Decimal("0.5"), question_text="weather tomorrow",
                         seconds_to_resolution=10 ** 9, corroborated=False)
        assert r.reason == "within_band"  # no class -> market-only anchor; 0.6 is within the band
