"""Insider/informed-flow sub-scores D2/D3/D4/D5/D6 (S7 / POL-9), each in [0, 1].

Pure functions over already-normalized inputs; the live wiring that computes the inputs (odds/
volume z-scores, the sybil cluster of a market's recent entries, Hermes's catalyst timeline) is
deferred. D6 is the FOLLOW-side score and is informational only -- FOLLOW is hard-off.
"""

import math


def clamp01(x):
    """Bound to [0, 1], NaN-safe: a NaN (e.g. a 0/0 normalization upstream) fails CLOSED to 0.0
    rather than propagating (NaN comparisons are all False, so a naive min/max would leak it)."""
    if math.isnan(x):
        return 0.0
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def d2_conviction(size, wallet_value, entry_price, recency):
    """(size/wallet_value clamped) * (1 - entry_price) * recency -- a fresh wallet betting a big
    fraction of its value into a longshot scores high."""
    fraction = clamp01(float(size) / float(wallet_value)) if wallet_value else 0.0
    return fraction * (1.0 - float(entry_price)) * clamp01(recency)


def d3_abnormal_move(move_strength, *, catalyst_present):
    """An abnormal odds/volume move, CANCELLED when a known public catalyst explains it (Hermes
    supplies the real catalyst timeline in S6; ``catalyst_present`` defaults to no-known-catalyst)."""
    return 0.0 if catalyst_present else clamp01(move_strength)


def d4_coordinated_entry(cluster_entries, total_entries):
    """Fraction of a market's recent entries coming from a single sybil cluster (uses ``sybil``)."""
    return clamp01(cluster_entries / total_entries) if total_entries > 0 else 0.0


def d5_lead_time(*, trade_ts, public_ts, horizon):
    """Traded BEFORE the news went public -> high. lead = public_ts - trade_ts, scaled by horizon;
    <= 0 (traded after) -> 0."""
    lead = public_ts - trade_ts
    return clamp01(lead / horizon) if lead > 0 and horizon > 0 else 0.0


def d6_smart_money(*, edge_weight, conviction):
    """The FOLLOW-side conviction = the luck-filter weight (0/1) * conviction. Informational only;
    FOLLOW is hard-off."""
    return clamp01(edge_weight * conviction)
