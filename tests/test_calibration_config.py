"""S5 / POL-7 — CalibrationConfig (the calibration knobs, consistency-checked at construction)."""

from decimal import Decimal

import pytest

from polybot.calibration.config import CalibrationConfig


def test_defaults_are_the_documented_envelope():
    c = CalibrationConfig()
    assert c.min_n == 150
    assert c.n_bins == 10
    assert c.reliability_max == Decimal("0.03")
    assert c.brier_skill_min == Decimal("0")
    assert c.longshot_lambda == Decimal("0.9")
    assert c.max_shift_uncorroborated == Decimal("1.0")
    assert c.max_shift_corroborated == Decimal("2.5")
    assert c.prior_decay_window_seconds == 86400
    assert c.epsilon == Decimal("0.001")


def test_rejects_non_positive_min_n():
    with pytest.raises(ValueError, match="min_n"):
        CalibrationConfig(min_n=0)


def test_rejects_inverted_max_shift_ordering():
    # a corroborated catalyst must allow a WIDER shift than an uncorroborated one.
    with pytest.raises(ValueError, match="max_shift"):
        CalibrationConfig(max_shift_uncorroborated=Decimal("2.5"), max_shift_corroborated=Decimal("1.0"))


def test_rejects_longshot_lambda_out_of_range():
    with pytest.raises(ValueError, match="longshot"):
        CalibrationConfig(longshot_lambda=Decimal("1.5"))


def test_rejects_reliability_max_out_of_range():
    with pytest.raises(ValueError, match="reliability"):
        CalibrationConfig(reliability_max=Decimal("0"))


def test_rejects_epsilon_out_of_range():
    with pytest.raises(ValueError, match="epsilon"):
        CalibrationConfig(epsilon=Decimal("0.6"))


def test_rejects_negative_brier_skill_min():
    with pytest.raises(ValueError, match="brier_skill_min"):
        CalibrationConfig(brier_skill_min=Decimal("-0.1"))


def test_rejects_a_gate_disabling_reliability_max(tmp_path=None):
    # review M1: reliability_max near 1 would let a grossly miscalibrated category pass the
    # calibration arm of the GO conjunction -> bound it to a sane ceiling.
    with pytest.raises(ValueError, match="reliability"):
        CalibrationConfig(reliability_max=Decimal("0.99"))


def test_rejects_an_unbounded_corroborated_shift():
    # review M2: an over-large corroborated shift removes the clamp -> the Anchor Gate is toothless.
    with pytest.raises(ValueError, match="max_shift"):
        CalibrationConfig(max_shift_corroborated=Decimal("100"))
