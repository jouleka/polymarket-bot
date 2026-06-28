"""S6 / POL-8 -- FusionEngine: the weighted log-odds fold (market mid as prior).

Safety properties under test:
  * FusionConfig is consistency-checked at construction and FAILS LOUD (ValueError) on a
    spec-cap violation (w_news > 0.25), a negative weight, or a non-positive clip bound.
  * corroboration is the single key that lets w_news contribute (w_news_effective flips
    0.20 <-> 0.0); an uncorroborated proposal reduces to mid nudged toward the base-rate prior.
  * a confident-wrong p_news cannot run away: its log-odds delta is clipped to +/- clip_logodds.
  * all-mid inputs leave the posterior at the mid (no spurious nudge).
  * a degenerate mid (<=0 or >=1) raises FusionError; an out-of-(0,1) signal contributes 0 delta
    (fail-closed, not a crash).
  * recalibrate() is a typed identity stub (the deferred adaptive slice replaces it).
  * components carries the raw Decimal inputs for the ComponentLog.
All probabilities are Decimal from strings; only the internal logit/sigmoid fold is float.
"""

from decimal import Decimal

import pytest

from polybot.fusion.engine import (
    FusionConfig,
    FusionError,
    FusionResult,
    fuse,
    recalibrate,
)


# Bootstrap config used across the plan + the e2e test (see DESIGN-S6 §0 fork 1b).
def _cfg(**overrides):
    base = dict(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)
    base.update(overrides)
    return FusionConfig(**base)


def test_config_rejects_w_news_above_cap():
    # The spec cap: Hermes's signal can never earn more than 0.25 weight.
    with pytest.raises(ValueError, match="w_news"):
        FusionConfig(w_news=0.26, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)


def test_config_rejects_negative_weight():
    # A negative w_base would invert the prior pull -- nonsense; must fail loud.
    for field in ("w_base", "w_micro", "w_flow"):
        with pytest.raises(ValueError, match=field):
            _cfg(**{field: -0.01})


def test_config_rejects_nonpositive_clip():
    with pytest.raises(ValueError, match="clip_logodds"):
        _cfg(clip_logodds=0.0)
    with pytest.raises(ValueError, match="clip_logodds"):
        _cfg(clip_logodds=-1.0)


def test_config_rejects_non_finite_weights():
    # NaN slips past a bare `< 0.0` check (IEEE 754: NaN < 0.0 is False) and would yield a
    # Decimal("NaN") p_final with no exception -- a silent break of the fail-loud contract.
    import math
    for field in ("w_news", "w_base", "w_micro", "w_flow", "clip_logodds"):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                _cfg(**{field: bad})


def test_recalibrate_is_identity_stub():
    # The deferred adaptive slice replaces this; v1 is a typed Decimal-in/Decimal-out no-op.
    for x in ("0.01", "0.5", "0.73", "0.999"):
        assert recalibrate(Decimal(x)) == Decimal(x), x
    assert isinstance(recalibrate(Decimal("0.5")), Decimal)


def test_fuse_all_mid_inputs_returns_mid():
    # Every signal == mid -> every delta is 0 -> L == logit(mid) -> p_final ~= mid.
    mid = Decimal("0.40")
    r = fuse(mid, p_news=mid, p_base=mid, p_micro=mid, p_flow=mid,
             corroborated=True, config=_cfg())
    assert isinstance(r, FusionResult)
    assert isinstance(r.p_final, Decimal)
    # 6dp re-quantization round-trip: identical to the mid within one quantum.
    assert abs(r.p_final - mid) <= Decimal("0.000001"), r.p_final


def test_corroboration_flips_w_news_effective():
    mid = Decimal("0.50")
    p_news = Decimal("0.80")  # bullish Hermes signal
    common = dict(p_news=p_news, p_base=mid, p_micro=mid, p_flow=mid, config=_cfg())

    corr = fuse(mid, corroborated=True, **common)
    uncorr = fuse(mid, corroborated=False, **common)

    # The corroboration key: w_news contributes only when corroborated.
    assert corr.w_news_effective == 0.20
    assert uncorr.w_news_effective == 0.0

    # Uncorroborated -> p_news earns 0 weight -> with p_base=p_micro=p_flow=mid the posterior
    # collapses to the mid (informational-only, exactly the DESIGN-S6 §0 fork-1b contract).
    assert abs(uncorr.p_final - mid) <= Decimal("0.000001"), uncorr.p_final
    # Corroborated -> the bullish signal pulls the posterior strictly above the mid.
    assert corr.p_final > mid, corr.p_final


def test_huge_p_news_delta_is_clipped():
    # A confident-wrong p_news near 1 cannot run away: its log-odds delta is clamped to
    # +/- clip_logodds, so the contribution is bounded regardless of how extreme p_news is.
    mid = Decimal("0.50")
    cfg = _cfg(clip_logodds=2.0)
    common = dict(p_base=mid, p_micro=mid, p_flow=mid, corroborated=True, config=cfg)

    extreme = fuse(mid, p_news=Decimal("0.999999"), **common)
    very_extreme = fuse(mid, p_news=Decimal("0.99999999"), **common)

    # Both clip to the SAME bounded L = logit(0.5)=0 + 0.20*clip(2.0) = 0.40 -> sigmoid(0.40).
    import math as _m
    expected = Decimal(str(1.0 / (1.0 + _m.exp(-0.20 * 2.0)))).quantize(Decimal("0.000001"))
    assert extreme.p_final == expected, extreme.p_final
    assert very_extreme.p_final == expected, very_extreme.p_final
    # Bounded: nowhere near p_news -- the clamp held.
    assert extreme.p_final < Decimal("0.60"), extreme.p_final


def test_p_base_pulls_toward_prior():
    # With p_news held at the mid (no news pull), a base-rate prior below the mid drags the
    # posterior strictly below the mid -- and a prior above drags it above. The prior is the
    # only mover here, so direction is unambiguous.
    mid = Decimal("0.50")
    common = dict(p_micro=mid, p_flow=mid, corroborated=True, config=_cfg())

    low = fuse(mid, p_news=mid, p_base=Decimal("0.20"), **common)
    high = fuse(mid, p_news=mid, p_base=Decimal("0.80"), **common)

    assert low.p_final < mid, low.p_final
    assert high.p_final > mid, high.p_final
    # Symmetric around the mid in log-odds (p_base symmetric, equal weight).
    assert abs((mid - low.p_final) - (high.p_final - mid)) <= Decimal("0.000002")


def test_degenerate_mid_raises_fusion_error():
    cfg = _cfg()
    common = dict(p_news=Decimal("0.6"), p_base=Decimal("0.5"),
                  p_micro=Decimal("0.5"), p_flow=Decimal("0.5"),
                  corroborated=True, config=cfg)
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.1"), Decimal("1.5")):
        with pytest.raises(FusionError, match="degenerate mid"):
            fuse(bad, **common)


def test_out_of_unit_signal_contributes_zero_delta():
    # A degenerate p_news (0, 1, or out of range) must NOT crash and must NOT nudge -- it is
    # dropped to a 0 delta. With every other signal at the mid, the posterior stays at the mid.
    mid = Decimal("0.50")
    common = dict(p_base=mid, p_micro=mid, p_flow=mid, corroborated=True, config=_cfg())
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.2"), Decimal("1.4")):
        r = fuse(mid, p_news=bad, **common)
        assert abs(r.p_final - mid) <= Decimal("0.000001"), (bad, r.p_final)
        # The raw (even degenerate) value is still recorded for the ComponentLog audit.
        assert r.components["p_news"] == bad, bad


def test_components_returns_raw_decimal_inputs():
    # The ComponentLog (§4.6) needs the raw per-signal Decimals to preserve the un-backfillable
    # substrate the deferred per-signal calibration grades. fuse() must surface exactly the four.
    mid = Decimal("0.50")
    r = fuse(mid, p_news=Decimal("0.7"), p_base=Decimal("0.4"),
             p_micro=Decimal("0.55"), p_flow=Decimal("0.45"),
             corroborated=True, config=_cfg())
    assert set(r.components) == {"p_news", "p_base", "p_micro", "p_flow"}
    assert r.components["p_news"] == Decimal("0.7")
    assert r.components["p_base"] == Decimal("0.4")
    assert r.components["p_micro"] == Decimal("0.55")
    assert r.components["p_flow"] == Decimal("0.45")
    for v in r.components.values():
        assert isinstance(v, Decimal)
