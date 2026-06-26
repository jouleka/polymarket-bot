"""S7 / POL-9 — DetectorConfig (knobs, consistency-checked at construction)."""

from decimal import Decimal

import pytest

from polybot.detectors.config import DetectorConfig


def test_defaults_are_the_documented_envelope():
    c = DetectorConfig()
    assert c.min_resolved == 50
    assert c.win_significance == Decimal("0.001")
    assert c.edge_ci_confidence == Decimal("0.99")
    assert c.max_event_dominance == Decimal("0.5")
    assert c.mm_min_trades == 100
    assert c.mm_balance_min == Decimal("0.4")
    assert c.toxicity_ratio_min == Decimal("0.75")
    assert c.toxicity_z_min == Decimal("2.0")
    assert (c.band_low_max, c.band_med_max, c.band_high_max) == (Decimal("2.5"), Decimal("5.0"), Decimal("7.5"))
    assert c.critical_subscore == Decimal("0.8")


def test_rejects_non_positive_min_resolved():
    with pytest.raises(ValueError, match="min_resolved"):
        DetectorConfig(min_resolved=0)


def test_rejects_unordered_band_cutoffs():
    with pytest.raises(ValueError, match="band"):
        DetectorConfig(band_low_max=Decimal("6"), band_med_max=Decimal("5"))


def test_rejects_band_cutoff_at_or_above_ten():
    with pytest.raises(ValueError, match="band"):
        DetectorConfig(band_high_max=Decimal("10"))


def test_rejects_significance_out_of_range():
    with pytest.raises(ValueError, match="win_significance"):
        DetectorConfig(win_significance=Decimal("0.6"))


def test_rejects_dominance_out_of_range():
    with pytest.raises(ValueError, match="dominance"):
        DetectorConfig(max_event_dominance=Decimal("1.5"))


def test_rejects_mm_balance_out_of_range():
    with pytest.raises(ValueError, match="mm_balance"):
        DetectorConfig(mm_balance_min=Decimal("0"))
