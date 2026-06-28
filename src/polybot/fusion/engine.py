"""FusionEngine (S6 / POL-8) -- the §4.1 weighted log-odds fold with the market mid as prior.

Hermes's posterior enters ONLY as ``p_news``, hard-capped at ``w_news <= 0.25`` and gated on
corroboration (>=2 independent allowlisted primaries, verified by the truth-gate). The Bot folds
it with ``p_base`` (base-rate prior), ``p_micro`` and ``p_flow`` (0-weight in v1, logged) against
``logit(mid)``:

    L = logit(mid) + w_news_eff*clip(logit(p_news)-logit(mid))
                   + w_base   *clip(logit(p_base)-logit(mid))
                   + w_micro  *clip(logit(p_micro)-logit(mid))
                   + w_flow   *clip(logit(p_flow)-logit(mid))
    w_news_eff = w_news if corroborated else 0.0
    p_final    = recalibrate(sigmoid(L))

Each per-signal delta is clipped to +/- ``clip_logodds`` so a confident-wrong signal cannot run
away. ``recalibrate`` is a typed IDENTITY stub behind a seam (the deferred adaptive isotonic
recalibrator replaces it). Fail-closed: any ``p_i`` not strictly in (0,1) contributes a 0 delta
(no nudge); a degenerate ``mid`` (<=0 or >=1) raises ``FusionError`` (the caller already guards
``midpoint() is None`` upstream).

Probabilities are Decimal at the boundary; the logit/sigmoid math is float internally (the one
log/exp boundary), re-quantized to a 6dp Decimal -- the same pattern as calibration/anchor.py and
ers/comove.py.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

_QUANT = Decimal("0.000001")  # 6dp, matching anchor.py / comove.py
_EPS = 1e-9                   # internal logit clamp so sigmoid/logit never see 0 or 1


class FusionError(Exception):
    """Raised when fusion cannot proceed (a degenerate mid the caller failed to guard)."""


@dataclass(frozen=True)
class FusionConfig:
    """Fixed bootstrap weights; consistency-checked at construction, fails LOUD.

    HARD invariants (DESIGN-S6 §4.1):
      * 0.0 <= w_news <= 0.25      -- the spec cap on Hermes's signal
      * w_base, w_micro, w_flow >= 0.0
      * clip_logodds > 0.0          -- a non-positive clamp would erase every signal delta
    """

    w_news: float
    w_base: float
    w_micro: float
    w_flow: float
    clip_logodds: float

    def __post_init__(self):
        self._verify()

    def _verify(self):
        if not (0.0 <= self.w_news <= 0.25):
            raise ValueError(f"w_news must be in [0.0, 0.25] (the spec cap), got {self.w_news}")
        for name in ("w_base", "w_micro", "w_flow"):
            value = getattr(self, name)
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0.0, got {value}")
        if not (self.clip_logodds > 0.0):
            raise ValueError(f"clip_logodds must be > 0.0, got {self.clip_logodds}")


def recalibrate(x: Decimal) -> Decimal:
    """IDENTITY stub behind a seam. The deferred adaptive slice (isotonic recalibrator, needs a
    warm ForecastLedger) replaces this; v1 returns p_final unchanged. Documented as identity so a
    future swap is a one-function change and the fold stays untouched."""
    return x


@dataclass(frozen=True)
class FusionResult:
    p_final: Decimal
    components: Mapping[str, Decimal]   # raw {p_news,p_base,p_micro,p_flow} for the ComponentLog
    w_news_effective: float


def _logit(x: float) -> float:
    x = min(max(x, _EPS), 1.0 - _EPS)
    return math.log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _clip(delta: float, bound: float) -> float:
    return min(max(delta, -bound), bound)


def _to_decimal(x: float) -> Decimal:
    return Decimal(str(x)).quantize(_QUANT)


def _in_unit(p: Decimal) -> bool:
    """A signal probability is usable iff it is finite and strictly inside (0, 1)."""
    return p.is_finite() and Decimal(0) < p < Decimal(1)


def fuse(mid: Decimal, *, p_news: Decimal, p_base: Decimal, p_micro: Decimal,
         p_flow: Decimal, corroborated: bool, config: FusionConfig) -> FusionResult:
    """Fold the four signals around ``logit(mid)`` and return the recalibrated posterior.

    Fail-closed: a degenerate ``mid`` (not finite, or not strictly in (0,1)) raises
    ``FusionError`` (the caller guards ``midpoint() is None`` upstream). Any individual signal
    ``p_i`` outside (0,1) contributes a 0 delta -- it cannot crash or nudge. ``w_news`` is applied
    only when ``corroborated`` (the corroboration key); otherwise its effective weight is 0.
    """
    if not (mid.is_finite() and Decimal(0) < mid < Decimal(1)):
        raise FusionError(f"degenerate mid: {mid}")

    mid_logit = _logit(float(mid))
    bound = config.clip_logodds
    w_news_eff = config.w_news if corroborated else 0.0

    L = mid_logit
    for weight, p in (
        (w_news_eff, p_news),
        (config.w_base, p_base),
        (config.w_micro, p_micro),
        (config.w_flow, p_flow),
    ):
        if weight == 0.0 or not _in_unit(p):
            continue  # fail-closed: no weight or a degenerate signal -> 0 delta (no nudge)
        delta = _clip(_logit(float(p)) - mid_logit, bound)
        L += weight * delta

    p_final = recalibrate(_to_decimal(_sigmoid(L)))
    components = {
        "p_news": p_news,
        "p_base": p_base,
        "p_micro": p_micro,
        "p_flow": p_flow,
    }
    return FusionResult(p_final=p_final, components=components, w_news_effective=w_news_eff)
