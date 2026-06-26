"""The Anchor Gate (S5 / POL-7) -- anti-overconfidence clamp on Hermes's posterior p.

Clamp ``logit(p)`` into the INTERSECTION of ``[logit(anchor) ± max_shift]`` over BOTH anchors (the
base-rate prior and the market mid), so a confident-wrong narrative can't run away from where the
crowd AND the reference class sit. A corroborated catalyst (>=2 independent allowlisted primaries)
WIDENS the allowed shift but never removes the bound. Near resolution the prior anchor is dropped
(the market is the better anchor then). Fails closed: empty intersection (the anchors disagree by
more than 2*max_shift) -> never trust that p; fall back to the midpoint anchor.

Probabilities are Decimal at the boundary; the logit/sigmoid math is float internally (the one
log/exp boundary), converted back to a 6dp Decimal -- the same pattern as comove.correlation.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class AnchorResult:
    p_clamped: Decimal
    shrunk: bool        # True if the gate moved p (clamped or conflict-shrunk)
    reason: str         # within_band | clamped_low | clamped_high | anchor_conflict


def anchor_gate(p, market_mid, prior, *, seconds_to_resolution, corroborated, config):
    # Fail LOUD on a non-finite p or anchor (NaN/Inf): the eps-clamp tames +-inf but NOT NaN
    # (min/max are order-dependent on NaN), and a NaN must never slip through as "within_band" --
    # the gate exists to distrust a confident-wrong p. The ERS loop turns this into a REJECT.
    for name, value in (("p", p), ("market_mid", market_mid), ("prior", prior)):
        if value is not None and not value.is_finite():
            raise ValueError(f"anchor_gate: non-finite {name} ({value})")
    eps = float(config.epsilon)
    max_shift = float(config.max_shift_corroborated if corroborated
                      else config.max_shift_uncorroborated)

    # Anchors: the market mid always; the prior UNLESS it's absent or we're inside the decay window.
    anchors = [_logit(_clamp_unit(float(market_mid), eps))]
    if prior is not None and seconds_to_resolution >= config.prior_decay_window_seconds:
        anchors.append(_logit(_clamp_unit(float(prior), eps)))

    lo = max(a - max_shift for a in anchors)
    hi = min(a + max_shift for a in anchors)
    if lo > hi:
        # The anchors disagree by more than 2*max_shift -> never trust a p that diverges from BOTH.
        return AnchorResult(_to_decimal(_sigmoid(sum(anchors) / len(anchors))), True, "anchor_conflict")

    z = _logit(_clamp_unit(float(p), eps))
    if z < lo:
        return AnchorResult(_to_decimal(_sigmoid(lo)), True, "clamped_low")
    if z > hi:
        return AnchorResult(_to_decimal(_sigmoid(hi)), True, "clamped_high")
    return AnchorResult(_to_decimal(_sigmoid(z)), False, "within_band")


def _logit(x):
    return math.log(x / (1.0 - x))


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def _clamp_unit(x, eps):
    return min(max(x, eps), 1.0 - eps)


def _to_decimal(x):
    return Decimal(str(x)).quantize(_QUANT)
