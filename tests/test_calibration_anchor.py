"""S5 / POL-7 — the Anchor Gate (clamp Hermes's p by a max log-odds shift from prior + market)."""

from decimal import Decimal

import pytest

from polybot.calibration.anchor import anchor_gate
from polybot.calibration.config import CalibrationConfig

CFG = CalibrationConfig()
_FAR = 10 ** 9  # seconds_to_resolution well outside the prior-decay window -> prior is in play


def test_overconfident_p_is_clamped_toward_the_market():
    # market 0.5 (logit 0), max_shift 1.0 -> band [-1, 1]; logit(0.99)=4.6 -> clamp to sigmoid(1).
    r = anchor_gate(Decimal("0.99"), Decimal("0.5"), None,
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
    assert r.shrunk is True and r.reason == "clamped_high"
    assert r.p_clamped < Decimal("0.99")
    assert abs(r.p_clamped - Decimal("0.731059")) < Decimal("0.0001")  # sigmoid(1.0)


def test_corroboration_widens_the_allowed_shift():
    base = anchor_gate(Decimal("0.99"), Decimal("0.5"), None,
                       seconds_to_resolution=_FAR, corroborated=False, config=CFG).p_clamped
    wide = anchor_gate(Decimal("0.99"), Decimal("0.5"), None,
                       seconds_to_resolution=_FAR, corroborated=True, config=CFG).p_clamped
    assert wide > base
    assert abs(wide - Decimal("0.924142")) < Decimal("0.0001")  # sigmoid(2.5)


def test_p_within_the_band_is_unchanged():
    r = anchor_gate(Decimal("0.6"), Decimal("0.5"), None,
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
    assert r.shrunk is False and r.reason == "within_band"
    assert abs(r.p_clamped - Decimal("0.6")) < Decimal("0.00001")


def test_empty_intersection_falls_back_to_the_midpoint_anchor():
    # prior 0.1 vs market 0.9 disagree by > 2*max_shift(1.0) -> conflict -> sigmoid(midpoint)=0.5.
    r = anchor_gate(Decimal("0.5"), Decimal("0.9"), Decimal("0.1"),
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
    assert r.reason == "anchor_conflict" and r.shrunk is True
    assert abs(r.p_clamped - Decimal("0.5")) < Decimal("0.00001")


def test_prior_anchor_is_dropped_near_resolution():
    # same prior/market, but WITHIN the decay window the prior is dropped -> market-only, so a p
    # near the market (0.85 vs 0.9) is NOT a conflict and passes through.
    r = anchor_gate(Decimal("0.85"), Decimal("0.9"), Decimal("0.1"),
                    seconds_to_resolution=0, corroborated=False, config=CFG)
    assert r.reason != "anchor_conflict"
    assert abs(r.p_clamped - Decimal("0.85")) < Decimal("0.0001")


def test_extreme_anchor_is_epsilon_guarded_without_crashing():
    # market mid 1.0 would be logit(+inf) without the epsilon clamp.
    r = anchor_gate(Decimal("0.5"), Decimal("1.0"), None,
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
    assert Decimal("0") < r.p_clamped < Decimal("1")
    assert r.p_clamped > Decimal("0.99")  # pulled hard toward the ~1.0 market


def test_non_finite_p_fails_loud_not_through_the_band():
    # review H1: a NaN/Inf p must NEVER pass as 'within_band' -- the gate that exists to distrust
    # a confident-wrong p must fail closed (raise -> the ERS loop turns it into a REJECT).
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(ValueError):
            anchor_gate(bad, Decimal("0.5"), None,
                        seconds_to_resolution=_FAR, corroborated=False, config=CFG)


def test_non_finite_anchor_fails_loud():
    with pytest.raises(ValueError):
        anchor_gate(Decimal("0.5"), Decimal("NaN"), None,
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
    with pytest.raises(ValueError):
        anchor_gate(Decimal("0.5"), Decimal("0.6"), Decimal("NaN"),
                    seconds_to_resolution=_FAR, corroborated=False, config=CFG)
