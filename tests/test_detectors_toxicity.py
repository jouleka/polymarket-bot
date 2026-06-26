"""S7 / POL-9 — D1 order-flow toxicity (the +EV defensive signal -> maker pull-quote seam)."""

from decimal import Decimal

import pytest

from polybot.detectors.config import DetectorConfig
from polybot.detectors.toxicity import toxicity

CFG = DetectorConfig()  # toxicity_ratio_min=0.75, toxicity_z_min=2.0


def _tox(buy, sell, mean, std):
    return toxicity(Decimal(str(buy)), Decimal(str(sell)),
                    baseline_mean=Decimal(str(mean)), baseline_std=Decimal(str(std)), config=CFG)


def test_toxic_flow_flags_and_signals_pull_quotes():
    # buy 90 / sell 10 -> ratio 0.8 (>=0.75); z = (0.8-0.2)/0.1 = 6 (>=2) -> toxic.
    t = _tox(90, 10, 0.2, 0.1)
    assert t.ratio == Decimal("0.8")
    assert t.toxic is True and t.pull_quotes is True
    assert t.subscore == 0.8


def test_balanced_flow_below_the_ratio_threshold_is_not_toxic():
    t = _tox(60, 40, 0.2, 0.1)  # ratio 0.2 < 0.75
    assert t.toxic is False and t.pull_quotes is False
    assert t.subscore == 0.0


def test_one_sided_but_unremarkable_flow_is_not_toxic():
    # ratio 0.8 but the market is ALWAYS one-sided (baseline_mean 0.79) -> z ~ 0.1 < 2 -> not toxic.
    t = _tox(90, 10, 0.79, 0.1)
    assert t.toxic is False and t.pull_quotes is False


def test_no_flow_is_not_toxic():
    t = _tox(0, 0, 0.2, 0.1)
    assert t.ratio == Decimal("0") and t.toxic is False


def test_rejects_negative_sizes():
    # review H1: a negative size (data corruption from the deferred parser) would push ratio > 1
    # and a bogus Critical sub-score -> reject it loud rather than poison the composite.
    with pytest.raises(ValueError, match="size"):
        _tox(-10, 50, 0.2, 0.1)
    with pytest.raises(ValueError, match="size"):
        _tox(100, -5, 0.2, 0.1)
