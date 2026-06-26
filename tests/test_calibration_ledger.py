"""S5 / POL-7 — forecast->outcome ledger (append-only, point-in-time, restart-stable)."""

from decimal import Decimal

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper


def _ledger(path):
    return ForecastLedger(path, MonotonicStamper())


def _rec(ledger, fid, *, category="politics", p="0.7", mid="0.6", cond="c1"):
    return ledger.record_forecast(fid, category=category, condition_id=cond,
                                  p=Decimal(p), market_mid=Decimal(mid))


def test_record_and_get_round_trips(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        assert _rec(l, "f1") is True
        r = l.get("f1")
        assert r.category == "politics" and r.condition_id == "c1"
        assert r.p == Decimal("0.7") and r.market_mid == Decimal("0.6")
        assert r.resolution_status is None and r.resolved_at is None
        assert r.created_at is not None


def test_record_is_idempotent_on_forecast_id(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        assert _rec(l, "f1") is True
        assert _rec(l, "f1", p="0.9") is False  # duplicate ignored
        assert l.get("f1").p == Decimal("0.7")  # original preserved


def test_resolution_sets_status_and_appears_in_resolved(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
        r = l.get("f1")
        assert r.resolution_status == "WON" and r.resolved_at is not None
        assert [x.forecast_id for x in l.resolved()] == ["f1"]


def test_unresolved_forecasts_are_excluded_from_resolved(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        _rec(l, "f2")
        l.record_resolution("f1", "LOST")
        assert [x.forecast_id for x in l.resolved()] == ["f1"]


def test_resolved_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1", category="politics")
        _rec(l, "f2", category="sports")
        l.record_resolution("f1", "WON")
        l.record_resolution("f2", "LOST")
        assert [x.forecast_id for x in l.resolved(category="sports")] == ["f2"]


def test_rejects_an_invalid_resolution_status(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        with pytest.raises(ValueError, match="status"):
            l.record_resolution("f1", "MAYBE")


def test_resolving_an_unknown_forecast_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(KeyError):
            l.record_resolution("nope", "WON")


def test_re_resolution_overwrites_on_a_dispute_flip(tmp_path):
    # a UMA dispute can flip an apparent WON to DISPUTED_LOST later.
    with _ledger(str(tmp_path / "f.db")) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
        l.record_resolution("f1", "DISPUTED_LOST")
        assert l.get("f1").resolution_status == "DISPUTED_LOST"


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "f.db")
    with _ledger(path) as l:
        _rec(l, "f1")
        l.record_resolution("f1", "WON")
    with _ledger(path) as l2:
        assert l2.get("f1").resolution_status == "WON"


def test_rejects_a_non_finite_forecast_p(tmp_path):
    # review H1: a NaN/Inf forecast must never enter the no-backfill calibration substrate.
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(ValueError, match="p"):
            l.record_forecast("f1", category="x", condition_id="c",
                              p=Decimal("NaN"), market_mid=Decimal("0.5"))


def test_rejects_an_out_of_range_market_mid(tmp_path):
    with _ledger(str(tmp_path / "f.db")) as l:
        with pytest.raises(ValueError, match="market_mid"):
            l.record_forecast("f1", category="x", condition_id="c",
                              p=Decimal("0.5"), market_mid=Decimal("1.5"))
