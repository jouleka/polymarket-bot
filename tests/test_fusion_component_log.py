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
