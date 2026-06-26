"""D1 order-flow toxicity (S7 / POL-9) -- the one clearly +EV defensive signal.

A simplified VPIN: over a trade-flow window, the one-sided imbalance ratio = |buy - sell| /
(buy + sell). Flow is TOXIC (likely informed) only when the imbalance is both large (>= ratio_min)
AND anomalous vs the market's OWN baseline (z >= z_min) -- so a market that is always one-sided
doesn't false-flag. ``pull_quotes`` is the seam the S8 maker module consumes (widen/pull). The
sub-score (0 unless toxic) feeds the composite. Pure. Full volume-bucketed VPIN deferred.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Toxicity:
    ratio: Decimal
    z: float
    toxic: bool
    subscore: float       # 0 unless toxic; else the imbalance magnitude in [0,1]
    pull_quotes: bool      # the maker pull-quote seam (== toxic)


def toxicity(buy_size, sell_size, *, baseline_mean, baseline_std, config):
    # Fail LOUD on a negative size: it is data corruption (a sign-convention bug / refund in the
    # deferred /activity parser), and it would push ratio > 1 -> a bogus Critical sub-score that
    # poisons the composite, or a negative total that silently reads as non-toxic (review H1).
    if buy_size < 0 or sell_size < 0:
        raise ValueError(f"toxicity: sizes must be >= 0, got buy={buy_size}, sell={sell_size}")
    total = buy_size + sell_size
    ratio = (abs(buy_size - sell_size) / total) if total > 0 else Decimal(0)
    z = float((ratio - baseline_mean) / baseline_std) if baseline_std > 0 else 0.0
    toxic = ratio >= config.toxicity_ratio_min and z >= float(config.toxicity_z_min)
    subscore = float(ratio) if toxic else 0.0
    return Toxicity(ratio=ratio, z=z, toxic=toxic, subscore=subscore, pull_quotes=toxic)
