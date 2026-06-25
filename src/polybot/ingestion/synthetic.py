"""Synthetic market events derived from the book-delta stream (POL-3 / S1).

Pure, deterministic detectors that turn raw ``price_change`` deltas into NOTABLE
structural events for downstream signals / calibration:
  - ``large_print``: a single delta removes a large absolute resting size at a
    level (a big fill or cancel went through).
  - ``liquidity_evaporation``: a top-of-book level is removed or sharply thinned
    (the resting depth at the touch vanished).

The events are OBJECTIVE observations; the thresholds are only a noise floor.
They are constructor params with documented defaults -- OPERATOR-TUNABLE once
shadow/backtest indicates the right sensitivity. Detection is pure (no book, no
network): the caller (MarketStream) supplies the before/after snapshots.
"""

from decimal import Decimal

_BUY_SIDES = {"buy", "bid"}
_SELL_SIDES = {"sell", "ask"}


def _canonical_side(side):
    """Normalize a venue side to canonical "BUY"/"SELL" so both event types report
    it uniformly; an unrecognized side is a format change -> HALT (fail loud),
    matching LocalBook._side_book."""
    s = side.strip().lower()
    if s in _BUY_SIDES:
        return "BUY"
    if s in _SELL_SIDES:
        return "SELL"
    raise ValueError(f"unknown order side: {side!r} - HALT on format change")


def _event(event_type, asset_id, side, price, size_removed, triggered_at):
    # ``triggered_at`` = observed_at of the price_change that caused this event; the
    # synthetic Observation itself gets its OWN canonical observed_at stamp (the
    # global-uniqueness contract) when the stream emits it.
    return {
        "event_type": event_type,
        "asset_id": asset_id,
        "side": _canonical_side(side),  # canonical BUY/SELL for both event types
        "price": str(price),
        "size_removed": str(size_removed),
        "triggered_at": triggered_at,
    }


def _worsened(side, price_after, price_before):
    """True if the new best price is WORSE than the old (so the old touch was
    removed): a lower bid or a higher ask."""
    if _canonical_side(side) == "BUY":
        return price_after < price_before
    return price_after > price_before


class SyntheticDetector:
    def __init__(self, *, large_print_size="5000", evaporation_fraction="0.6",
                 min_evaporation_size="1000"):
        self._large_print_size = Decimal(large_print_size)
        self._evaporation_fraction = Decimal(evaporation_fraction)
        self._min_evaporation_size = Decimal(min_evaporation_size)

    def detect(self, asset_id, level_changes, before_top, after_top, observed_at):
        """``level_changes``: list of (side, price, size_before, size_after) for the
        levels this frame touched. ``before_top`` / ``after_top``: the
        (best_bid, bid_size, best_ask, ask_size) snapshots around the apply. Returns
        a list of synthetic event dicts (possibly empty)."""
        events = []
        for side, price, size_before, size_after in level_changes:
            removed = size_before - size_after
            if removed >= self._large_print_size:
                events.append(_event("large_print", asset_id, side, price, removed, observed_at))
        events.extend(self._evaporation(asset_id, before_top, after_top, observed_at))
        return events

    def _evaporation(self, asset_id, before_top, after_top, observed_at):
        bid0, bid_sz0, ask0, ask_sz0 = before_top
        bid1, bid_sz1, ask1, ask_sz1 = after_top
        out = []
        for side, p0, s0, p1, s1 in (("BUY", bid0, bid_sz0, bid1, bid_sz1),
                                     ("SELL", ask0, ask_sz0, ask1, ask_sz1)):
            ev = self._side_evaporation(asset_id, side, p0, s0, p1, s1, observed_at)
            if ev is not None:
                out.append(ev)
        return out

    def _side_evaporation(self, asset_id, side, price0, size0, price1, size1, observed_at):
        if price0 is None:
            return None  # nothing was resting at the touch
        if price1 is None or _worsened(side, price1, price0):
            removed = size0  # the whole best level vanished
        elif price1 == price0 and size1 < size0 * (1 - self._evaporation_fraction):
            removed = size0 - size1  # same touch, sharply thinned
        else:
            return None  # improved or stable -> not evaporation
        if removed < self._min_evaporation_size:
            return None
        return _event("liquidity_evaporation", asset_id, side, price0, removed, observed_at)
