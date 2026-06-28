"""ComponentLog sidecar (S6 / POL-8, DESIGN §4.6).

Append-only, idempotent per-signal breakdown keyed by forecast_id (= intent_id).
Preserves the un-backfillable substrate the deferred per-signal calibration needs
WITHOUT modifying POL-7's ForecastLedger. Shares the one MonotonicStamper.

Safety properties under test:
  * record() returns True on first insert, False on a duplicate forecast_id (idempotent).
  * record() fails LOUD (ValueError) on a non-finite or out-of-[0,1] probability --
    the substrate cannot be backfilled, so garbage must never enter it.
  * all() returns the recorded rows carrying the stamp + every stored field
    (p_news/p_base/p_micro/p_flow as Decimal, w_news_effective float, corroborated bool, mid Decimal).
"""
import pytest
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.fusion.component_log import ComponentLog


def _log(tmp_path):
    # MonotonicStamper with an injected deterministic clock for a predictable stamp.
    stamper = MonotonicStamper(clock=lambda: 1000)
    return ComponentLog(str(tmp_path / "components.db"), stamper=stamper)


def test_record_returns_true_on_first_insert(tmp_path):
    with _log(tmp_path) as log:
        inserted = log.record(
            "intent-1",
            p_news=Decimal("0.70"), p_base=Decimal("0.55"),
            p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
            w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
        )
    assert inserted is True


def test_record_duplicate_forecast_id_returns_false(tmp_path):
    with _log(tmp_path) as log:
        first = log.record(
            "intent-dup",
            p_news=Decimal("0.70"), p_base=Decimal("0.55"),
            p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
            w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
        )
        # Same forecast_id, DIFFERENT payload: the second call must be a no-op INSERT OR IGNORE.
        second = log.record(
            "intent-dup",
            p_news=Decimal("0.10"), p_base=Decimal("0.10"),
            p_micro=Decimal("0.10"), p_flow=Decimal("0.10"),
            w_news_effective=0.0, corroborated=False, mid=Decimal("0.10"),
        )
        rows = log.all()
    assert first is True and second is False
    # Idempotent: exactly one row survives, and it is the ORIGINAL payload (not overwritten).
    assert len(rows) == 1
    assert rows[0].p_news == Decimal("0.70") and rows[0].corroborated is True


def test_record_rejects_non_finite_prob(tmp_path):
    # A NaN p_news must never enter the substrate (it cannot be backfilled).
    with _log(tmp_path) as log:
        with pytest.raises(ValueError, match="p_news"):
            log.record(
                "intent-nan",
                p_news=Decimal("NaN"), p_base=Decimal("0.55"),
                p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
                w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
            )
        # Nothing was written.
        assert log.all() == ()


def test_record_rejects_out_of_range_prob(tmp_path):
    # mid > 1 is rejected; the field name appears in the error (loud + locatable).
    with _log(tmp_path) as log:
        with pytest.raises(ValueError, match="mid"):
            log.record(
                "intent-oob",
                p_news=Decimal("0.70"), p_base=Decimal("0.55"),
                p_micro=Decimal("0.50"), p_flow=Decimal("0.50"),
                w_news_effective=0.20, corroborated=True, mid=Decimal("1.5"),
            )
        assert log.all() == ()

    # And a negative p_flow is rejected too.
    with _log(tmp_path) as log:
        with pytest.raises(ValueError, match="p_flow"):
            log.record(
                "intent-neg",
                p_news=Decimal("0.70"), p_base=Decimal("0.55"),
                p_micro=Decimal("0.50"), p_flow=Decimal("-0.01"),
                w_news_effective=0.20, corroborated=True, mid=Decimal("0.52"),
            )


def test_all_round_trips_every_field_with_stamp(tmp_path):
    # Deterministic clock -> known monotonic stamp (1000 for the first stamp() call).
    stamper = MonotonicStamper(clock=lambda: 1000)
    with ComponentLog(str(tmp_path / "c.db"), stamper=stamper) as log:
        log.record(
            "intent-rt",
            p_news=Decimal("0.71"), p_base=Decimal("0.53"),
            p_micro=Decimal("0.49"), p_flow=Decimal("0.61"),
            w_news_effective=0.20, corroborated=False, mid=Decimal("0.52"),
        )
        rows = log.all()

    assert len(rows) == 1
    rec = rows[0]
    assert rec.forecast_id == "intent-rt"
    # Probabilities preserved EXACTLY as Decimal (string round-trip, no float drift).
    assert rec.p_news == Decimal("0.71") and isinstance(rec.p_news, Decimal)
    assert rec.p_base == Decimal("0.53")
    assert rec.p_micro == Decimal("0.49")
    assert rec.p_flow == Decimal("0.61")
    assert rec.mid == Decimal("0.52") and isinstance(rec.mid, Decimal)
    # w_news_effective is a float; corroborated round-trips as a bool (not 0/1 int).
    assert rec.w_news_effective == 0.20 and isinstance(rec.w_news_effective, float)
    assert rec.corroborated is False
    # Carries the stamper's monotonic stamp.
    assert rec.recorded_at == 1000
