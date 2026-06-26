"""S5 / POL-7 — calibration tracker: the binary GO/NO-GO k multiplier."""

from decimal import Decimal

from polybot.calibration.config import CalibrationConfig
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.tracker import CalibrationTracker
from polybot.core.clock import MonotonicStamper


def _ledger(path):
    return ForecastLedger(path, MonotonicStamper())


def _build(ledger, category, specs):
    """specs: (p, market_mid, status, count) -> record + resolve each forecast."""
    i = 0
    for p, mid, status, count in specs:
        for _ in range(count):
            i += 1
            fid = f"{category}-{i}"
            ledger.record_forecast(fid, category=category, condition_id="c",
                                   p=Decimal(str(p)), market_mid=Decimal(str(mid)))
            ledger.record_resolution(fid, status)


# A well-calibrated + discriminating bot (p in {0.2,0.8} matching outcome rates) vs an
# uninformative market mid of 0.5 -> the bot beats the baseline. 40 forecasts.
_PASSING = [(0.2, 0.5, "WON", 4), (0.2, 0.5, "LOST", 16),
            (0.8, 0.5, "WON", 16), (0.8, 0.5, "LOST", 4)]


def test_k_is_one_when_all_go_criteria_pass(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING)
        t = CalibrationTracker(l, CalibrationConfig(min_n=20))
        assert t.k_for("politics") == Decimal("1")
        r = t.report_for("politics")
        assert r.go is True and r.n_scored == 40
        assert r.brier_skill > 0 and r.reliability <= Decimal("0.03") and r.resolution > r.reliability


def test_k_is_zero_below_min_n(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING)  # 40 resolved
        t = CalibrationTracker(l, CalibrationConfig(min_n=50))  # need 50
        assert t.k_for("politics") == Decimal("0")


def test_k_is_zero_when_bot_does_not_beat_the_market_baseline(tmp_path):
    # market mid == bot p everywhere -> the bot has no edge -> skill 0 (not > 0) -> k=0.
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", [(0.2, 0.2, "WON", 4), (0.2, 0.2, "LOST", 16),
                               (0.8, 0.8, "WON", 16), (0.8, 0.8, "LOST", 4)])
        t = CalibrationTracker(l, CalibrationConfig(min_n=20))
        assert t.k_for("politics") == Decimal("0")


def test_k_is_zero_when_overconfident_even_if_it_beats_the_market(tmp_path):
    # p in {0.99,0.01} but outcomes 0.8/0.2 -> reliability 0.036 > 0.03, though skill > 0 and
    # resolution > reliability -> the reliability gate forces k=0.
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", [(0.99, 0.5, "WON", 16), (0.99, 0.5, "LOST", 4),
                               (0.01, 0.5, "WON", 4), (0.01, 0.5, "LOST", 16)])
        t = CalibrationTracker(l, CalibrationConfig(min_n=20))
        r = t.report_for("politics")
        assert r.brier_skill > 0 and r.reliability > Decimal("0.03")
        assert t.k_for("politics") == Decimal("0")


def test_disputed_lost_is_excluded_so_it_cannot_flip_k(tmp_path):
    # the honest WON/LOST set passes; adding DISPUTED_LOST high-p forecasts (which, if scored as
    # losses, would wreck calibration) must NOT change k -- a whale-captured flip can't poison it.
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING + [(0.9, 0.5, "DISPUTED_LOST", 10)])
        t = CalibrationTracker(l, CalibrationConfig(min_n=20))
        r = t.report_for("politics")
        assert r.n_scored == 40 and r.n_disputed == 10
        assert t.k_for("politics") == Decimal("1")


def test_void_outcomes_are_excluded_from_scoring(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING + [(0.5, 0.5, "VOID", 7)])
        r = CalibrationTracker(l, CalibrationConfig(min_n=20)).report_for("politics")
        assert r.n_scored == 40 and r.n_void == 7


def test_category_with_no_honest_resolutions_is_cold(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", [(0.9, 0.5, "DISPUTED_LOST", 5)])
        t = CalibrationTracker(l, CalibrationConfig(min_n=20))
        r = t.report_for("politics")
        assert r.n_scored == 0 and r.go is False
        assert t.k_for("politics") == Decimal("0")


def test_an_unhandled_resolution_status_fails_loud(tmp_path):
    # review L1: a status that is neither honest nor disputed/void (DB corruption, or a future
    # 5th VALID_STATUSES not taught to the tracker) must NOT silently vanish from the accounting.
    import pytest
    with _ledger(str(tmp_path / "f.db")) as l:
        _build(l, "politics", _PASSING)
        l._conn.execute("UPDATE forecasts SET resolution_status='WEIRD' WHERE forecast_id='politics-1'")
        l._conn.commit()
        with pytest.raises(ValueError, match="status"):
            CalibrationTracker(l, CalibrationConfig(min_n=20)).report_for("politics")
