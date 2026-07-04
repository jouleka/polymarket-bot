"""Walk-forward earn-autonomy evidence evaluator (S9 / POL-11).

The honesty spine: a category is Stage-0 ready ONLY on net-of-everything shadow PnL that is
positive-WITH-margin AND out-of-sample -- never gross edge, never the full in-sample net. The OOS
gate reads net_OOS (the most-recent ceil(oos_holdout_fraction*n) honest rows by settled_at), and the
required margin is inflated by a multiple-comparisons family-size penalty (certifying 1-of-N
categories demands a proportionally stronger edge). DISPUTED/VOID are excluded from the honest net
sample (whale-flip immunity) but COUNTED in n_disputed. Fail CLOSED: cold / insufficient sample /
None stats -> ready False, never a phantom GO. Fail LOUD only on an unknown shadow status (mirrors
MakerTracker's exhaustive-status raise).
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from polybot.calibration.scoring import brier, brier_skill, murphy
from polybot.harness import pnl

_HONEST_SHADOW = ("WON", "LOST")
_HONEST_FORECAST = {"WON": 1, "LOST": 0}


@dataclass(frozen=True)
class EvidenceReport:
    category: str
    n_resolved: int
    n_oos: int
    n_disputed: int
    net_full: Decimal | None
    net_oos: Decimal | None
    brier_skill: Decimal | None
    reliability: Decimal | None
    k: Decimal
    maker_go: bool
    required_margin: Decimal
    oos_positive: bool
    calibration_ok: bool
    maker_ok: bool
    ready: bool


def _ceil_frac(n, fraction):
    """ceil(fraction * n) via exact Decimal rounding -> int (>= 0)."""
    return int((Decimal(n) * fraction).to_integral_value(rounding=ROUND_CEILING))


def evaluate_category(category, *, shadow_ledger, forecast_ledger, calibration_gate, maker_gate,
                      ramp_config, maker_config, family_size):
    rc = ramp_config
    # family_size is the multiple-comparisons family (>= 1 -- certifying 1-of-N categories). A
    # family_size < 1 makes required_margin = net_margin_min + mc_penalty*(family_size-1) NEGATIVE,
    # which would let a net-NEGATIVE OOS window clear the positive-with-margin gate. Fail LOUD.
    if family_size < 1:
        raise ValueError(f"family_size must be >= 1, got {family_size}")
    # --- SHADOW side: honest WON/LOST kept; DISPUTED/VOID counted; net over the OOS window ---
    honest = []
    n_disputed = 0
    for r in shadow_ledger.settled(category):
        if r.status in _HONEST_SHADOW:
            honest.append(r)
        elif r.status in ("DISPUTED", "VOID"):
            n_disputed += 1
        else:
            # Exhaustive: a status outside VALID_STATUSES (DB corruption / an untaught 5th status)
            # must fail loud, never silently vanish from the accounting (mirrors MakerTracker).
            raise ValueError(f"unhandled shadow status {r.status!r}")

    n_resolved = len(honest)
    required_margin = rc.net_margin_min + rc.mc_penalty * (Decimal(family_size) - Decimal(1))

    if n_resolved == 0:  # cold -> fail-closed, None stats
        n_oos = 0
        net_full = net_oos = None
        oos_positive = False
    else:
        n_oos = _ceil_frac(n_resolved, rc.oos_holdout_fraction)
        oos_rows = honest[-n_oos:]
        net_oos = pnl.window_net(oos_rows, maker_config=maker_config)
        net_full = pnl.window_net(honest, maker_config=maker_config)
        oos_positive = (n_oos >= rc.min_oos_resolved) and (net_oos > required_margin)

    # --- CALIBRATION side: Brier-beats-mid + reliability over the OOS forecast window ---
    fhonest = [f for f in forecast_ledger.resolved(category)
               if f.resolution_status in _HONEST_FORECAST]
    n_f = len(fhonest)
    brier_skill_v = reliability_v = None
    if n_f > 0:
        f_oos = fhonest[-_ceil_frac(n_f, rc.oos_holdout_fraction):]
        if f_oos:
            bot_pairs = [(f.p, _HONEST_FORECAST[f.resolution_status]) for f in f_oos]
            market_pairs = [(f.market_mid, _HONEST_FORECAST[f.resolution_status]) for f in f_oos]
            brier_skill_v = brier_skill(brier(bot_pairs), brier(market_pairs))
            reliability_v = murphy(bot_pairs, rc.oos_n_bins).reliability

    k = calibration_gate.k_for(category)
    calibration_ok = ((k == Decimal(1))
                      and (brier_skill_v is not None and brier_skill_v > Decimal(0))
                      and (reliability_v is not None and reliability_v <= rc.reliability_max))

    maker_go = maker_gate.go_for(category)
    ready = (n_resolved >= rc.min_resolved) and oos_positive and calibration_ok and maker_go

    return EvidenceReport(category=category, n_resolved=n_resolved, n_oos=n_oos,
                          n_disputed=n_disputed, net_full=net_full, net_oos=net_oos,
                          brier_skill=brier_skill_v, reliability=reliability_v, k=k,
                          maker_go=maker_go, required_margin=required_margin,
                          oos_positive=oos_positive, calibration_ok=calibration_ok,
                          maker_ok=maker_go, ready=ready)
