"""S5 / POL-7 — Brier / Murphy decomposition / Brier-skill (pure, Decimal)."""

from decimal import Decimal

import pytest

from polybot.calibration.scoring import brier, brier_skill, murphy


def _pairs(*spec):
    """spec: (p, outcome, count) tuples -> a flat list of (Decimal p, int outcome) pairs."""
    out = []
    for p, o, n in spec:
        out += [(Decimal(str(p)), o)] * n
    return out


def test_brier_basic():
    assert brier([(Decimal("0.9"), 1), (Decimal("0.1"), 0)]) == Decimal("0.01")


def test_brier_perfect_is_zero():
    assert brier([(Decimal("1"), 1), (Decimal("0"), 0)]) == Decimal("0")


def test_brier_worst_is_one():
    assert brier([(Decimal("0"), 1), (Decimal("1"), 0)]) == Decimal("1")


def test_brier_raises_on_empty():
    with pytest.raises(ValueError):
        brier([])


def test_murphy_identity_holds_on_discrete_forecasts():
    # 0.2-forecasts: 2 won / 3 lost (o_k=0.4); 0.8-forecasts: 3 won / 2 lost (o_k=0.6).
    pairs = _pairs((0.2, 1, 2), (0.2, 0, 3), (0.8, 1, 3), (0.8, 0, 2))
    m = murphy(pairs, 10)
    assert m.reliability == Decimal("0.04")
    assert m.resolution == Decimal("0.01")
    assert m.uncertainty == Decimal("0.25")
    # Brier = Reliability - Resolution + Uncertainty (exact for homogeneous bins).
    assert abs(brier(pairs) - (m.reliability - m.resolution + m.uncertainty)) < Decimal("1e-9")


def test_murphy_perfect_calibration_has_zero_reliability():
    # forecasts of 0.5 with exactly half the outcomes 1 -> p_bar == o_bar -> reliability 0.
    m = murphy(_pairs((0.5, 1, 5), (0.5, 0, 5)), 10)
    assert m.reliability == Decimal("0")


def test_murphy_discriminating_forecaster_has_positive_resolution():
    assert murphy(_pairs((0.2, 1, 2), (0.2, 0, 3), (0.8, 1, 3), (0.8, 0, 2)), 10).resolution > 0


def test_brier_skill_positive_when_beating_baseline():
    assert brier_skill(Decimal("0.1"), Decimal("0.2")) == Decimal("0.5")


def test_brier_skill_negative_when_worse_than_baseline():
    assert brier_skill(Decimal("0.25"), Decimal("0.2")) < 0


def test_brier_skill_zero_when_baseline_is_perfect():
    # baseline Brier 0 = the market already predicts perfectly -> no positive skill possible.
    assert brier_skill(Decimal("0.1"), Decimal("0")) == Decimal("0")
