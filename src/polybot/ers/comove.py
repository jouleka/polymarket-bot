"""Learned co-move matrix + per-cluster cap (S3 / POL-5 slice 3).

PURE estimator over price-snapshot midpoint bars: pairwise Pearson correlation of midpoint
returns -> per-cluster warm/cold + representative rho -> a per-cluster DOLLAR cap that
REPLACES the fail-closed unknown-corr=+1 default once enough history accrues. Fails CLOSED
(unknown / degenerate correlation -> rho=1 -> the cluster collapses to one per_trade bet).

The cap is an ADDITIONAL min() headroom term in the validator, so it can only ever TIGHTEN
per-intent sizing; warm only ever relaxes the slice-1 <=3 count gate while every dollar cap
(incl. total_open) still binds. See docs/DESIGN-S3-ERS.md "Slice 3".
"""

import json
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.market_stream import MarketStream
from polybot.ingestion.midpoint import MIDPOINT_SOURCE, decode_midpoint_batch
from polybot.ers.validator import ClusterView

_RHO_QUANTUM = Decimal("0.000001")  # quantize rho to 6dp for deterministic, stable caps


def _returns(bars):
    """Per-bar simple returns from a token's ``{bar_index: midpoint}`` map. A return exists at
    index ``i`` only when both ``i`` and ``i-1`` are present, so gaps never fabricate a return."""
    return {i: bars[i] - bars[i - 1] for i in bars if (i - 1) in bars}


class ClusterModel:
    """Learned per-cluster co-move verdict from price-snapshot midpoint bars. Constructed from
    ``{token_id: {bar_index: midpoint}}`` (the EventStore adapter builds these; tests pass them
    directly). ``min_observations`` is the warm gate -- an estimation knob (NOT a signed risk
    cap)."""

    def __init__(self, bars, *, min_observations=30):
        self._min_obs = min_observations
        self._returns = {token: _returns(b) for token, b in bars.items()}

    def view(self, token_ids):
        """Verdict for the cluster spanning ``token_ids`` (the intent's token + its same-cluster
        positions). Cold (fail closed) if fewer than 2 distinct tokens, or ANY member pair has
        < ``min_observations`` shared paired returns. Warm -> rho = MAX pairwise correlation."""
        distinct = list(dict.fromkeys(token_ids))  # dedup, stable order
        if len(distinct) < 2:
            return ClusterView(warm=False, rho=None)
        max_rho = None
        for i in range(len(distinct)):
            for j in range(i + 1, len(distinct)):
                ra = self._returns.get(distinct[i], {})
                rb = self._returns.get(distinct[j], {})
                shared = sorted(set(ra) & set(rb))
                if len(shared) < self._min_obs:
                    return ClusterView(warm=False, rho=None)
                rho = correlation([ra[k] for k in shared], [rb[k] for k in shared])
                max_rho = rho if max_rho is None else max(max_rho, rho)
        return ClusterView(warm=True, rho=max_rho)


def correlation(returns_a, returns_b):
    """Pearson correlation of two ALIGNED, equal-length return series, quantized to 6dp and
    clamped to [-1, 1]. FAILS CLOSED to +1 (max tightening) on any degenerate input -- fewer
    than 2 paired points, mismatched lengths, or zero variance in either series (a flat book
    carries no co-move information, so it is treated as worst-case correlated, not benign).
    Decimal-only money-math boundary: a non-Decimal (float) input RAISES (fail-loud), never
    silently contaminating the estimate -- the real path always passes exact Decimal midpoints."""
    n = len(returns_a)
    if n != len(returns_b) or n < 2:
        return Decimal(1)
    mean_a = sum(returns_a, Decimal(0)) / n
    mean_b = sum(returns_b, Decimal(0)) / n
    da = [x - mean_a for x in returns_a]
    db = [x - mean_b for x in returns_b]
    cov = sum((x * y for x, y in zip(da, db)), Decimal(0))
    var_a = sum((x * x for x in da), Decimal(0))
    var_b = sum((y * y for y in db), Decimal(0))
    if var_a <= 0 or var_b <= 0:
        return Decimal(1)
    rho = (cov / (var_a * var_b).sqrt()).quantize(_RHO_QUANTUM)
    if rho > 1:
        return Decimal(1)
    if rho < -1:
        return Decimal(-1)
    return rho


def _build_midpoint_bars(envelopes, *, bar_ns):
    bars = {}
    for env in envelopes:
        if env.source != MIDPOINT_SOURCE:
            continue
        bar_index = env.observed_at // bar_ns
        for token, quote in decode_midpoint_batch(env.content).items():
            bars.setdefault(token, {})[bar_index] = quote.midpoint
    return bars


def _build_raw_bars(envelopes, *, bar_ns, source):
    stream = MarketStream(MonotonicStamper())
    bars = {}            # token -> {bar_index: midpoint}
    universe = []        # tokens seen, in first-seen order (stable, no dependence on dict order)
    seen = set()
    cur_bar = None

    def close_bar(bar_index):
        for token in universe:
            book = stream.book_for(token)
            if book is None:
                continue
            mid = book.midpoint()  # None when stale/crossed -> no sample (fail closed)
            if mid is not None:
                bars.setdefault(token, {})[bar_index] = mid

    for env in envelopes:
        if env.source != source:
            continue
        bar_index = env.observed_at // bar_ns
        if cur_bar is not None and bar_index != cur_bar:
            close_bar(cur_bar)
        cur_bar = bar_index
        for token in env.market_links:
            if token not in seen:
                seen.add(token)
                universe.append(token)
        stream.ingest(json.loads(env.content))

    if cur_bar is not None:
        close_bar(cur_bar)
    return bars


def build_bar_series(store, *, bar_ns, until=None, source=None):
    """Build exact per-token closing-midpoint bars from one bounded store view.

    Auto mode prefers midpoint batches and otherwise preserves legacy raw-frame replay.
    Explicit midpoint mode never falls back; any other explicit source uses raw replay.
    """
    envelopes = store.all() if until is None else store.replay_until(until)
    selected_source = source
    if selected_source is None:
        selected_source = (
            MIDPOINT_SOURCE
            if any(env.source == MIDPOINT_SOURCE for env in envelopes)
            else "clob-ws"
        )
    if selected_source == MIDPOINT_SOURCE:
        return _build_midpoint_bars(envelopes, bar_ns=bar_ns)
    return _build_raw_bars(envelopes, bar_ns=bar_ns, source=selected_source)
