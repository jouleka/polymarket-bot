"""L7 real-time unrealized-drawdown breaker (S3 / POL-5 slice 3, §4 L7).

Stateful safety monitor: each ERS cycle, mark every NON-FROZEN open position to the live book
midpoint, sum the net unrealized drawdown, and emit a freeze / flatten / velocity / stale signal.
Marks to MID (stable; a thin-book best-bid must not fire the drastic FLATTEN). FAILS CLOSED: an
un-markable non-frozen position freezes + alerts (the stale-mark watchdog) -- never FLATTEN blind.
Unlike the pure validator it carries a bounded rolling history for the velocity trigger. No keys,
no signing; the ERS loop consumes the action (FREEZE_ADDS -> reject new; FLATTEN -> signal exit).
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

NONE = "NONE"
FREEZE_ADDS = "FREEZE_ADDS"
FLATTEN = "FLATTEN"


@dataclass(frozen=True)
class BreakerState:
    action: str         # NONE | FREEZE_ADDS | FLATTEN
    drawdown: Decimal   # net unrealized drawdown over markable, non-frozen positions (+ = losing)
    triggers: tuple     # which fired: freeze_floor / flatten_floor / velocity / stale_mark


class DrawdownBreaker:
    """Construct with the signed caps + an injected ``clock`` returning a monotonic timestamp in
    SECONDS (matching ``caps.l7_velocity_window_seconds``). ``evaluate`` is called once per cycle."""

    def __init__(self, caps, *, clock):
        self._caps = caps
        self._clock = clock
        self._history = deque()  # (ts, drawdown) within the velocity window

    def evaluate(self, positions, book_for):
        now = self._clock()
        drawdown, stale, worst_position_loss = self._mark(positions, book_for)
        self._history.append((now, drawdown))
        window = self._caps.l7_velocity_window_seconds
        while self._history and self._history[0][0] < now - window:
            self._history.popleft()

        triggers = []
        # Velocity: a fast RISE in drawdown across the window = current minus the window's min
        # (catches a rapid markdown from a recent low; the history always holds the current point).
        floor_in_window = min(d for _, d in self._history)
        if drawdown - floor_in_window > self._caps.l7_velocity_delta:
            triggers.append("velocity")
        # Per-position floor (review M1): the NET drawdown can mask one position in catastrophic
        # loss behind another's (possibly non-exitable) paper gain, exactly when correlation breaks.
        # A single non-frozen position down more than the freeze floor freezes adds regardless of
        # net. (Dormant at v1's $12 per-trade cap; load-bearing once caps scale.) Never FLATTENs on
        # this alone -- flattening stays aggregate-net-driven.
        if worst_position_loss > self._caps.l7_freeze_floor:
            triggers.append("position_loss")
        if stale:
            triggers.append("stale_mark")

        # FLATTEN supersedes FREEZE supersedes NONE. A confirmed > flatten_floor loss flattens even
        # alongside a stale position; staleness / velocity on their own only ever FREEZE (never
        # FLATTEN blind -- flattening also needs a fresh mark).
        if drawdown > self._caps.l7_flatten_floor:
            triggers.append("flatten_floor")
            action = FLATTEN
        elif drawdown > self._caps.l7_freeze_floor:
            triggers.append("freeze_floor")
            action = FREEZE_ADDS
        elif triggers:
            action = FREEZE_ADDS
        else:
            action = NONE
        return BreakerState(action, drawdown, tuple(triggers))

    def _mark(self, positions, book_for):
        """Net unrealized drawdown (+ = losing) over non-frozen positions, the WORST single
        non-frozen position loss, and a stale flag if ANY non-frozen position can't be marked.
        Frozen (disputed) positions are skipped entirely."""
        drawdown = Decimal(0)
        stale = False
        worst_position_loss = Decimal(0)
        for pos in positions:
            if pos.frozen:
                continue
            mid = self._mid(pos, book_for)
            if mid is None:
                stale = True  # un-markable -> can't confirm safety -> freeze + alert
                continue
            loss = pos.worst_case_risk / pos.entry_price * (pos.entry_price - mid)  # +loss if mid<entry
            drawdown += loss
            if loss > worst_position_loss:
                worst_position_loss = loss
        return drawdown, stale, worst_position_loss

    @staticmethod
    def _mid(pos, book_for):
        if pos.entry_price <= 0:  # placeholder / wiring error -> un-markable, fail closed
            return None
        book = book_for(pos.token_id)
        if book is None:
            return None
        return book.midpoint()  # None when stale/crossed
