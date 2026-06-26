"""The luck filter (S7 / POL-9) -- "is this wallet genuinely sharp, or just lucky?"

The whole game is the luck correction. Over a wallet's RESOLVED bets (entry_price, outcome in
{0,1}), the per-bet EDGE = outcome - entry_price (realized excess vs the price-implied baseline).
A wallet passes only if ALL hold, else weight 0:
  1. enough sample (n >= min_resolved),
  2. wins beat the price-implied baseline at p < win_significance (one-sided binomial-z; under the
     null each bet wins with prob entry_price -> Poisson-binomial mean/variance),
  3. the mean edge robustly excludes 0 (deterministic one-sided normal-CI lower bound > 0), and
  4. the edge is not dominated by a single bet (no bet > max_event_dominance of the positive edge).

Statistical (not money) -> float, via ``statistics.NormalDist`` (cf. the Anchor Gate's float logit).
"""

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist


@dataclass(frozen=True)
class ResolvedBet:
    entry_price: Decimal
    outcome: int  # 1 (won) | 0 (lost)


@dataclass(frozen=True)
class WalletEdge:
    n: int
    mean_edge: float
    win_z: float
    edge_ci_low: float
    max_share: float
    passes: bool


def assess(bets, config):
    n = len(bets)
    if n == 0:
        return WalletEdge(0, 0.0, 0.0, 0.0, 1.0, False)

    entries = [float(b.entry_price) for b in bets]
    edges = [b.outcome - e for b, e in zip(bets, entries)]
    wins = sum(b.outcome for b in bets)

    # (2) one-sided binomial-z of observed wins vs the Poisson-binomial null (p_i = entry_price).
    mu = sum(entries)
    var = sum(e * (1.0 - e) for e in entries)
    win_z = (wins - mu) / math.sqrt(var) if var > 0 else 0.0

    # (3) deterministic one-sided normal-CI lower bound on the mean edge. NOTE (review M2): under
    # ZERO edge variance (all bets identical, e.g. all-win at one price) this collapses to
    # mean_edge > 0 and adds no independent protection -- the binomial-z gate (2) is load-bearing
    # there (and is correctly extreme for a genuinely all-win wallet). It never FALSE-PASSES a
    # lucky wallet: all-loss fails on mean_edge <= 0, and a real edge needs gate (2) to fire.
    mean_edge = statistics.fmean(edges)
    if n >= 2:
        sd = statistics.stdev(edges)
        z_ci = NormalDist().inv_cdf(float(config.edge_ci_confidence))
        edge_ci_low = mean_edge - z_ci * (sd / math.sqrt(n))
    else:
        edge_ci_low = mean_edge  # a single bet has no spread estimate

    # (4) single-event dominance over the POSITIVE edge.
    positive = [e for e in edges if e > 0]
    total_pos = sum(positive)
    max_share = (max(positive) / total_pos) if total_pos > 0 else 1.0

    z_win = NormalDist().inv_cdf(1.0 - float(config.win_significance))
    passes = (n >= config.min_resolved
              and win_z > z_win
              and edge_ci_low > 0
              and max_share <= float(config.max_event_dominance))
    return WalletEdge(n, mean_edge, win_z, edge_ci_low, max_share, passes)
