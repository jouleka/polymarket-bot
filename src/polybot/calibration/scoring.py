"""Brier score, Murphy decomposition, and Brier-skill (S5 / POL-7).

Pure, exact-Decimal functions over a forecast set of ``(p, outcome)`` pairs (outcome in {0,1}).
The calibration tracker extracts these pairs from the ledger (WON=1 / LOST=0; DISPUTED_LOST and
VOID excluded) and compares the bot's Brier against the market-mid baseline on the same set.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MurphyDecomposition:
    reliability: Decimal   # calibration error (want ~0)
    resolution: Decimal    # discrimination (want large)
    uncertainty: Decimal   # base-rate variance o_bar*(1-o_bar)


def _require(pairs):
    if not pairs:
        raise ValueError("cannot score an empty forecast set")


def brier(pairs):
    """Mean squared error of the forecasts: (1/N) * sum (p - outcome)^2."""
    _require(pairs)
    total = sum(((p - o) ** 2 for p, o in pairs), Decimal(0))
    return total / len(pairs)


def murphy(pairs, n_bins):
    """Murphy decomposition (Brier = Reliability - Resolution + Uncertainty) over ``n_bins``
    equal-width forecast bins. Exact when each occupied bin is homogeneous (discrete forecasts);
    an approximation otherwise (the within-bin forecast variance is the residual)."""
    _require(pairs)
    n = len(pairs)
    o_bar = sum((Decimal(o) for _, o in pairs), Decimal(0)) / n
    bins = {}
    for p, o in pairs:
        idx = int(p * n_bins)
        idx = min(max(idx, 0), n_bins - 1)  # p == 1 (or any p>=1) -> the last bin; guard p<0 too
        bins.setdefault(idx, []).append((p, o))
    reliability = Decimal(0)
    resolution = Decimal(0)
    for members in bins.values():
        nk = len(members)
        p_bar_k = sum((p for p, _ in members), Decimal(0)) / nk
        o_bar_k = sum((Decimal(o) for _, o in members), Decimal(0)) / nk
        weight = Decimal(nk) / n
        reliability += weight * (p_bar_k - o_bar_k) ** 2
        resolution += weight * (o_bar_k - o_bar) ** 2
    uncertainty = o_bar * (Decimal(1) - o_bar)
    return MurphyDecomposition(reliability, resolution, uncertainty)


def brier_skill(bot_brier, baseline_brier):
    """1 - bot/baseline: > 0 means the bot beats the just-quote-the-market baseline (lower Brier
    is better). A non-positive baseline (a perfect market) admits no positive skill -> 0."""
    if baseline_brier <= 0:
        return Decimal(0)
    return Decimal(1) - bot_brier / baseline_brier
