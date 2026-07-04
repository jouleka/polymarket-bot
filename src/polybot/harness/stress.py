"""Dispute-freeze stress test + tail-survival gate (S9 / POL-11).

DECISIONS-S0 §4 reserve-floor invariant: simulate a freeze of the LARGEST resolution-source cluster
under a (default 100%) adverse co-move markdown, plus the full non-frozen encumbrance, and prove the
signed reserve floor still holds. Pure over the ERS Portfolio + the signed RiskCaps. Fail CLOSED: a
non-finite worst_case_risk or adverse_fraction -> survives False (a bad field must never certify a
phantom survival). tail_survived is the earn-autonomy tail gate: you must have SURVIVED real disputes
(>= min resolved DISPUTED) AND >= min correlated-stress episodes, not merely dodged them.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StressResult:
    survives: bool
    reserve_after: Decimal
    reserve_floor: Decimal
    worst_case_markdown: Decimal


def dispute_freeze_stress(portfolio, *, caps, adverse_fraction=Decimal("1")):
    floor = caps.reserve_floor
    # Fail CLOSED on a non-finite adverse_fraction or any non-finite position risk.
    if not adverse_fraction.is_finite():
        return StressResult(False, caps.nav, floor, Decimal(0))
    for p in portfolio.positions:
        if not p.worst_case_risk.is_finite():
            return StressResult(False, caps.nav, floor, Decimal(0))

    # Group by resolution_source; the frozen cluster is the source with the MAX summed worst_case_risk
    # (ties -> the first by iteration; positions is an ordered tuple, so deterministic).
    sums = {}
    order = []
    for p in portfolio.positions:
        src = p.resolution_source
        if src not in sums:
            sums[src] = Decimal(0)
            order.append(src)
        sums[src] += p.worst_case_risk

    if not order:  # empty portfolio -> nothing frozen, nothing encumbered
        return StressResult(caps.nav >= floor, caps.nav, floor, Decimal(0))

    frozen_src = order[0]
    for src in order:
        if sums[src] > sums[frozen_src]:
            frozen_src = src

    frozen_cluster_wcr = sums[frozen_src]
    non_frozen_encumbered = sum((sums[src] for src in order if src != frozen_src), Decimal(0))
    worst_case_markdown = adverse_fraction * frozen_cluster_wcr
    reserve_after = caps.nav - non_frozen_encumbered - worst_case_markdown
    return StressResult(reserve_after >= floor, reserve_after, floor, worst_case_markdown)


def tail_survived(*, n_resolved_disputed, stress_episodes, ramp_config):
    return (n_resolved_disputed >= ramp_config.min_resolved_disputed
            and stress_episodes >= ramp_config.min_stress_episodes)
