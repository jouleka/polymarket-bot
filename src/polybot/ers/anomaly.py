"""L5 AnomalyMonitor -- the anomaly kill-switch (S4.4 / POL-6).

DrawdownBreaker-shaped: constructed with caps + an injected 0-arg ``clock`` returning float
monotonic SECONDS + one seam per trigger; every seam defaults to None == that trigger is
dormant (the data-gated pattern), so a bare monitor never fires. FAIL CLOSED: a wired seam
that RAISES inside evaluate fires its own trigger -- it never masks and never propagates.
STICKY (Fork 1): this module only ever REPORTS anomalies; recovery is operator-owned, so
nothing here touches the op-state machine.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from polybot.ers.safety import (
    REASON_L5_ABNORMAL_BOOK,
    REASON_L5_API_STORM,
    REASON_L5_CLOCK_SKEW,
)

NONE = "NONE"
HALT = "HALT"


@dataclass(frozen=True)
class AnomalyState:
    action: str      # NONE | HALT
    triggers: tuple  # the l5_* reason strings that fired, severity order; () when NONE

    def __post_init__(self):
        if self.action == HALT and not self.triggers:
            raise ValueError(
                "HALT requires at least one trigger (the controller reports triggers[0] as "
                "the halt reason)")


class ClockSkewSentinel:
    """L5 clock-skew seam (design §3 #4): pure compare of two injected 0-arg refs, both
    returning float unix-seconds (real NTP/chrony ref is deploy-time wiring). Strictly
    GREATER than ``caps.clock_skew_tolerance_seconds`` trips; symmetric via abs()."""

    def __init__(self, *, wall_clock, ntp_ref, caps):
        self._wall_clock = wall_clock
        self._ntp_ref = ntp_ref
        self._caps = caps

    def skewed(self):
        return abs(self._wall_clock() - self._ntp_ref()) > self._caps.clock_skew_tolerance_seconds


class ApiStormSentinel:
    """L5 API error-storm seam (design §3 #3): the (deploy-time) API caller records every
    response status via ``record``; the monitor polls ``storming(now)``. Windowed deque of
    ``(now_s, int(status))`` in the monitor's monotonic-seconds clock domain."""

    def __init__(self, caps):
        self._caps = caps
        self._events = deque()  # (now_s, int(status))

    def record(self, status, *, now):
        """``status`` must be int-coercible (real HTTP status codes); a non-numeric status
        fails loud HERE at the injection seam, by design."""
        self._events.append((now, int(status)))

    def storming(self, now):
        window = self._caps.api_storm_window_seconds
        while self._events and now - self._events[0][0] > window:
            self._events.popleft()
        fivexx = sum(1 for _, s in self._events if s >= 500)
        auth = sum(1 for _, s in self._events if s in (401, 403))
        return (fivexx >= self._caps.api_5xx_storm_count
                or auth >= self._caps.api_auth_storm_count)


class AnomalyMonitor:
    """evaluate(positions, book_for) -> AnomalyState, once per controller cycle. Consults the
    wired seams in pinned severity order and collects ALL firing triggers; triggers[0] is the
    halt reason the consumer reports. S4.4a wires the skew seam; S4.4b-e add the rest."""

    def __init__(self, caps, *, clock, ws_last_frame_at=None, api_sentinel=None,
                 skew_sentinel=None, recon_provider=None, canary=None, dispute_flagger=None):
        self._caps = caps
        self._clock = clock                        # 0-arg -> float monotonic SECONDS
        self._ws_last_frame_at = ws_last_frame_at  # 0-arg -> stamper-domain ns | None (S4.4d)
        self._api_sentinel = api_sentinel          # ApiStormSentinel (S4.4b)
        self._skew_sentinel = skew_sentinel        # duck-typed .skewed() -> bool
        self._recon_provider = recon_provider      # 0-arg -> ReconResult | None (S4.4e)
        self._canary = canary                      # 0-arg -> bool (S4.4e scheduler)
        # DEFERRED seam (UMA dispute watch, design §3): stored + documented, NOT consulted
        # in S4.4 -- no dispute-ingestion source exists yet.
        self._dispute_flagger = dispute_flagger
        self._canary_last_run = None               # float | None: the canary scheduler's memory
        self._prev_mid = {}   # token_id -> last VALID (non-stale) midpoint observed (S4.4c)
        self._prev_depth = {}  # token_id -> top-of-book depth at that same valid observation

    def evaluate(self, positions, book_for):
        now = self._clock()
        triggers = []
        # Severity slot 1 of the pinned order (skew, recon, canary, book, api, ws): clock
        # skew. Slots 2-4 land in S4.4c/S4.4e; slot 6 (ws) lands in S4.4d.
        if self._skew_sentinel is not None:
            try:
                if self._skew_sentinel.skewed():
                    triggers.append(REASON_L5_CLOCK_SKEW)
            except Exception:
                # FAIL-CLOSED SEAM RULE: a raising sentinel IS the anomaly -- fire this
                # seam's trigger and continue to the next seam; never mask, never propagate.
                triggers.append(REASON_L5_CLOCK_SKEW)
        # Severity slot 4: abnormal book -- internal check over positions + book_for, no
        # seam kwarg, but fail-closed wrapped all the same: a raising book/book_for IS an
        # abnormal-book anomaly, and an unwrapped raise here would VOID the triggers already
        # collected this cycle (e.g. a skew halt lost to a run_cycle crash -> L6 SIGKILL
        # instead of a clean audited halt). Never mask, never propagate.
        try:
            self._check_abnormal_book(positions, book_for, triggers)
        except Exception:
            # _check_abnormal_book appends its trigger as its FINAL statement, so a raise
            # means it has not fired this cycle; the not-in guard keeps the once-per-cycle
            # invariant robust regardless of how the method evolves.
            if REASON_L5_ABNORMAL_BOOK not in triggers:
                triggers.append(REASON_L5_ABNORMAL_BOOK)
        # Severity slot 5 of the pinned order: API 5xx/auth storm.
        # FAIL-CLOSED: a raising seam IS the anomaly -- fire and move to the next seam.
        if self._api_sentinel is not None:
            try:
                if self._api_sentinel.storming(now):
                    triggers.append(REASON_L5_API_STORM)
            except Exception:
                triggers.append(REASON_L5_API_STORM)
        if not triggers:
            return AnomalyState(NONE, ())
        return AnomalyState(HALT, tuple(triggers))

    def _check_abnormal_book(self, positions, book_for, triggers):
        """L5 trigger 1 (DESIGN-S4.4 §3): crossed/locked/empty-side, depth-collapse and
        midpoint-jump on HELD tokens with NON-stale books. Tokens are DEDUPED (many
        positions can share one token -- its book is checked once per cycle) and all three
        checks fire the SAME reason string at most ONCE per cycle (the count==1 tests pin
        both). Frozen positions are NOT skipped (book structure, not P&L). Per-token
        prev-mid/prev-depth memory updates AFTER comparisons and ONLY on a valid non-stale
        mid, so first observation never fires jump/collapse and a stale interlude preserves
        the last VALID baseline. Stale books -> breaker/validator domain; absent books ->
        validator no_book domain."""
        abnormal = False
        seen = set()
        for pos in positions:
            token = pos.token_id
            if token in seen:
                continue  # dedupe: one structural check per token per cycle
            seen.add(token)
            book = book_for(token)
            if book is None:
                continue
            if book.is_stale():
                continue
            mid = book.midpoint()
            if mid is None:
                abnormal = True  # non-stale yet mid-less = crossed/locked/empty side
                continue
            _bid, bid_size, _ask, ask_size = book.top_of_book()
            depth = ((bid_size if bid_size is not None else Decimal("0"))
                     + (ask_size if ask_size is not None else Decimal("0")))
            prev_depth = self._prev_depth.get(token)
            if (prev_depth is not None
                    and prev_depth >= self._caps.depth_collapse_min_prev_shares
                    and depth <= prev_depth * (Decimal(1) - self._caps.depth_collapse_fraction)):
                abnormal = True
            prev_mid = self._prev_mid.get(token)
            if prev_mid is not None and abs(mid - prev_mid) >= self._caps.midpoint_jump_halt:
                abnormal = True
            self._prev_mid[token] = mid      # AFTER comparisons; valid non-stale mids only
            self._prev_depth[token] = depth
        if abnormal:
            triggers.append(REASON_L5_ABNORMAL_BOOK)  # once per cycle, never per check
