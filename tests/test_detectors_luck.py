"""S7 / POL-9 — the luck filter (binomial-z + deterministic normal-CI + single-event-dominance)."""

from decimal import Decimal

from polybot.detectors.config import DetectorConfig
from polybot.detectors.luck import ResolvedBet, assess

CFG = DetectorConfig()  # min_resolved=50


def _bets(*spec):
    """spec: (entry_price, outcome, count) -> a flat list of ResolvedBet."""
    out = []
    for entry, outcome, count in spec:
        out += [ResolvedBet(Decimal(str(entry)), outcome)] * count
    return out


def test_genuinely_sharp_wallet_passes():
    # 100 bets at 0.5, 70 wins -> win_z = (70-50)/5 = 4.0; edge robustly > 0; not dominated.
    e = assess(_bets((0.5, 1, 70), (0.5, 0, 30)), CFG)
    assert e.passes is True
    assert e.win_z > 3 and e.edge_ci_low > 0


def test_small_sample_wallet_is_rejected():
    e = assess(_bets((0.5, 1, 7), (0.5, 0, 3)), CFG)  # 10 < min_resolved
    assert e.passes is False


def test_lucky_but_insignificant_wallet_is_rejected():
    # 100 bets at 0.5, 55 wins -> win_z = 1.0, below the p<0.001 threshold.
    e = assess(_bets((0.5, 1, 55), (0.5, 0, 45)), CFG)
    assert e.win_z < 3 and e.passes is False


def test_single_event_dominated_wallet_is_rejected_despite_significance():
    # 50 bets with a significant win_z, but the positive edge is one lucky 0.02-entry bet
    # (the rest are near-certain 0.999 wins with ~0 edge) -> dominance + CI gates reject it.
    e = assess(_bets((0.02, 1, 1), (0.999, 1, 49)), CFG)
    assert e.win_z > 3
    assert e.max_share > float(CFG.max_event_dominance)
    assert e.passes is False


def test_empty_history_is_rejected():
    assert assess([], CFG).passes is False


def test_single_bet_does_not_crash_and_is_rejected():
    # review L2: n=1 has no stdev -> must not crash; n < min_resolved -> fails.
    assert assess(_bets((0.5, 1, 1)), CFG).passes is False


def test_zero_edge_variance_sharp_wallet_still_passes_via_binomial():
    # review M2: all 60 bets at 0.5 won -> zero edge variance (the CI degenerates to mean>0), but
    # the binomial-z is the load-bearing gate and is extreme here -> a genuinely sharp wallet passes.
    e = assess(_bets((0.5, 1, 60)), CFG)
    assert e.passes is True and e.win_z > 3
