"""S7 / POL-9 — composite 0..10 + Low/Med/High/Critical bands + single-Critical override."""

from polybot.detectors.composite import CRITICAL, HIGH, LOW, MED, composite
from polybot.detectors.config import DetectorConfig

CFG = DetectorConfig()  # band cutoffs 2.5 / 5.0 / 7.5; critical_subscore 0.8


def test_empty_subscores_is_low_zero():
    s = composite({}, CFG)
    assert s.value == 0.0 and s.band == LOW


def test_weighted_sum_scales_to_ten_and_bands():
    assert composite({"a": 0.0, "b": 0.0}, CFG).band == LOW
    assert composite({"a": 0.2, "b": 0.2}, CFG).band == LOW    # value 2.0
    assert composite({"a": 0.3, "b": 0.3}, CFG).band == MED    # value 3.0
    assert composite({"a": 0.6, "b": 0.6}, CFG).band == HIGH   # value 6.0
    assert composite({"a": 0.8, "b": 0.8}, CFG).value == 8.0
    assert composite({"a": 0.8, "b": 0.8}, CFG).band == CRITICAL


def test_single_critical_subscore_escalates_band_to_at_least_high():
    # mean 0.425 -> value 4.25 -> MED normally, but one sub-score 0.85 >= 0.8 -> escalate to HIGH.
    s = composite({"D1": 0.85, "D2": 0.0}, CFG)
    assert s.value < 5.0 and s.band == HIGH


def test_single_critical_override_never_demotes():
    # an already-Critical band is not lowered by the override.
    assert composite({"a": 0.9, "b": 0.9}, CFG).band == CRITICAL


def test_weights_change_the_value():
    # weight a 3x b: (3*0 + 1*0.4)/4 = 0.1 -> value 1.0 (vs equal-weight 0.2 -> 2.0).
    s = composite({"a": 0.0, "b": 0.4}, CFG, weights={"a": 3, "b": 1})
    assert abs(s.value - 1.0) < 1e-9


def test_clamps_out_of_range_subscores():
    # review H2: a producer bug emitting a sub-score outside [0,1] must not blow the 0..10 scale.
    assert composite({"a": 2.0}, CFG).value <= 10.0          # clamped to 1.0 -> 10.0
    assert composite({"a": -1.0}, CFG).value >= 0.0          # clamped to 0.0 -> 0.0
    assert composite({"a": float("nan")}, CFG).value == 0.0  # NaN -> 0.0 (fail closed)
