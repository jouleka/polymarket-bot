"""Tests for the S4.4 / POL-6 L5 anomaly caps + pure sentinels (DESIGN-S4.4-ANOMALY §3-§5).

The 7 new RiskCaps thresholds are tighten-only, _verify-checked, content-hashed envelope
fields; ClockSkewSentinel and ApiStormSentinel are the pure, clock-injected L5 seams that
the AnomalyMonitor consults in pinned severity order. All time values here are injected
floats (monotonic seconds); no test touches a real clock.
"""

from decimal import Decimal

import pytest

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor, ApiStormSentinel, ClockSkewSentinel
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_API_STORM, REASON_L5_CLOCK_SKEW


def test_anomaly_caps_defaults_construct_and_carry_the_design_values():
    # Kills: a missing field declaration or a wrong default constant (design §5 table).
    caps = RiskCaps()
    assert caps.midpoint_jump_halt == Decimal("0.15")
    assert caps.depth_collapse_fraction == Decimal("0.8")
    assert caps.depth_collapse_min_prev_shares == Decimal("1000")
    assert caps.ws_staleness_halt_seconds == 30
    assert caps.api_5xx_storm_count == 5
    assert caps.api_auth_storm_count == 2
    assert caps.api_storm_window_seconds == 60


def test_anomaly_caps_changes_are_content_hash_tamper_evident():
    # Kills: declaring a threshold as a plain class attribute instead of a dataclass field
    # (asdict would skip it and the signed envelope's hash would NOT change on tamper).
    base = RiskCaps().content_hash()
    assert RiskCaps(ws_staleness_halt_seconds=15).content_hash() != base
    assert RiskCaps(midpoint_jump_halt=Decimal("0.10")).content_hash() != base


def test_midpoint_jump_halt_of_zero_is_rejected_and_the_default_accepted():
    # Boundary pair, lower edge of (0, 1): x == 0 must FAIL construction; 0.15 is in-range.
    # Kills: dropping the lower bound from the midpoint_jump_halt range check.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("0"))
    RiskCaps(midpoint_jump_halt=Decimal("0.15"))  # must not raise


def test_midpoint_jump_halt_of_one_is_rejected_because_a_mid_is_a_probability():
    # Boundary pair, upper edge of (0, 1): x == 1 must FAIL (a probability mid can never
    # jump a full 1.0 -> the trigger would be vacuous). Kills: writing <= 1 instead of < 1.
    with pytest.raises(ValueError, match="midpoint_jump_halt"):
        RiskCaps(midpoint_jump_halt=Decimal("1"))


def test_depth_collapse_fraction_of_zero_is_rejected_but_one_is_accepted():
    # Boundary pair for (0, 1]: 0 rejected; 1 ("all prev depth gone") is the legal tightest
    # setting and MUST construct. Kills: writing < 1 instead of <= 1, or dropping the lower bound.
    with pytest.raises(ValueError, match="depth_collapse_fraction"):
        RiskCaps(depth_collapse_fraction=Decimal("0"))
    RiskCaps(depth_collapse_fraction=Decimal("1"))  # must not raise


def test_depth_collapse_min_prev_shares_of_zero_is_rejected():
    # (> 0): a zero noise floor would arm the collapse check on dust-depth books.
    # Kills: dropping the strictly-positive check on the noise floor.
    with pytest.raises(ValueError, match="depth_collapse_min_prev_shares"):
        RiskCaps(depth_collapse_min_prev_shares=Decimal("0"))


def test_each_anomaly_int_cap_of_zero_fails_verify():
    # All four join the existing strictly-positive-int loop; a zero window/count/staleness
    # would make its check vacuous (0 events always "storm", any frame age always "stale").
    # Kills: leaving any one name out of the _verify loop tuple.
    for field in ("ws_staleness_halt_seconds", "api_5xx_storm_count",
                  "api_auth_storm_count", "api_storm_window_seconds"):
        with pytest.raises(ValueError, match=field):
            RiskCaps(**{field: 0})


def test_clock_skew_of_exactly_the_tolerance_is_not_skewed():
    # Boundary pair (strict >): |wall - ntp| == tolerance (2s default) must NOT trip.
    # Kills: mutating > to >= in ClockSkewSentinel.skewed.
    sentinel = ClockSkewSentinel(wall_clock=lambda: 1_000_002.0,
                                 ntp_ref=lambda: 1_000_000.0, caps=RiskCaps())
    assert sentinel.skewed() is False


def test_clock_skew_just_over_the_tolerance_is_skewed():
    # Boundary pair partner: 2.5s > 2s tolerance trips.
    # Kills: deleting the comparison / hardcoding skewed False.
    sentinel = ClockSkewSentinel(wall_clock=lambda: 1_000_002.5,
                                 ntp_ref=lambda: 1_000_000.0, caps=RiskCaps())
    assert sentinel.skewed() is True


def test_clock_skew_is_symmetric_when_the_wall_clock_runs_behind_ntp():
    # wall BEHIND ntp by 2.5s trips too; behind by exactly 2s does not (same strict edge).
    # Kills: dropping abs() -- a signed compare only catches one direction of skew.
    behind = ClockSkewSentinel(wall_clock=lambda: 1_000_000.0,
                               ntp_ref=lambda: 1_000_002.5, caps=RiskCaps())
    assert behind.skewed() is True
    behind_at_edge = ClockSkewSentinel(wall_clock=lambda: 1_000_000.0,
                                       ntp_ref=lambda: 1_000_002.0, caps=RiskCaps())
    assert behind_at_edge.skewed() is False


def test_four_5xx_responses_in_the_window_do_not_storm():
    # Boundary pair (fivexx >= api_5xx_storm_count == 5): FOUR is under the threshold.
    # Kills: loosening the count compare or hardcoding storming True.
    sentinel = ApiStormSentinel(RiskCaps())
    for t in (0.0, 1.0, 2.0, 3.0):
        sentinel.record(500, now=t)
    assert sentinel.storming(10.0) is False


def test_five_mixed_5xx_responses_in_the_window_storm():
    # Boundary pair partner: exactly FIVE statuses >= 500 (mixed 500/502/503/504) at the
    # threshold storms. Kills: mutating >= to > on the count, and any 5xx filter that
    # matches only the literal 500 instead of status >= 500.
    sentinel = ApiStormSentinel(RiskCaps())
    for t, status in ((0.0, 500), (1.0, 502), (2.0, 503), (3.0, 504), (4.0, 500)):
        sentinel.record(status, now=t)
    assert sentinel.storming(10.0) is True


def test_one_auth_failure_in_the_window_does_not_storm():
    # Boundary pair (auth >= api_auth_storm_count == 2): a single 401 is under.
    # Kills: loosening the auth count compare or hardcoding storming True.
    sentinel = ApiStormSentinel(RiskCaps())
    sentinel.record(401, now=0.0)
    assert sentinel.storming(5.0) is False


def test_two_auth_failures_storm_and_403_counts_like_401():
    # Boundary pair partner: 401 + 403 == exactly 2 auth fails at the threshold storms.
    # Kills: mutating >= to > on the auth count, and an auth filter matching only 401.
    sentinel = ApiStormSentinel(RiskCaps())
    sentinel.record(401, now=0.0)
    sentinel.record(403, now=1.0)
    assert sentinel.storming(5.0) is True


def test_non_auth_4xx_statuses_never_count_toward_either_storm():
    # 404/429/400 are ordinary client noise: NOT auth failures, NOT 5xx -- even eight of
    # them must not fire. Kills: widening the auth filter to any 4xx (400 <= s < 500) or
    # widening the 5xx filter to s >= 400.
    sentinel = ApiStormSentinel(RiskCaps())
    for t, status in enumerate((404, 429, 400, 404, 429, 400, 404, 429)):
        sentinel.record(status, now=float(t))
    assert sentinel.storming(8.0) is False


def test_event_at_exactly_now_minus_window_is_kept_inclusive_boundary():
    # Inclusive-boundary pin, mirroring the DrawdownBreaker deque: an event with
    # now - t == window (60s) is still IN the window, so 5 old 5xx at t=0 still storm
    # at now=60. Kills: pruning with >= (now - t >= window would drop the boundary entry).
    sentinel = ApiStormSentinel(RiskCaps())
    for _ in range(5):
        sentinel.record(500, now=0.0)
    assert sentinel.storming(60.0) is True


def test_event_just_older_than_the_window_is_pruned_and_the_storm_clears():
    # Boundary pair partner: at now=61 the t=0 events are (61 - 0) > 60 -> pruned -> no
    # storm. Kills: deleting the prune entirely (an API storm would then NEVER clear).
    sentinel = ApiStormSentinel(RiskCaps())
    for _ in range(5):
        sentinel.record(500, now=0.0)
    assert sentinel.storming(61.0) is False


def _no_books(token_id):
    """book_for stub: no book for any token (the abnormal-book check skips absent books)."""
    return None


def _burst_5xx_sentinel():
    """An ApiStormSentinel pre-loaded with a storming burst: 5x 500 at t=0..4 seconds."""
    sentinel = ApiStormSentinel(RiskCaps())
    for t in range(5):
        sentinel.record(500, now=float(t))
    return sentinel


def test_monitor_with_a_skewed_clock_sentinel_halts_with_l5_clock_skew():
    # The REAL ClockSkewSentinel wired through the skew_sentinel= seam fires the trigger.
    # Kills: dropping the skew consult from evaluate (state would be NONE).
    skew = ClockSkewSentinel(wall_clock=lambda: 100.0, ntp_ref=lambda: 0.0, caps=RiskCaps())
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 0.0, skew_sentinel=skew)
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_CLOCK_SKEW in state.triggers


def test_monitor_with_a_storming_api_sentinel_halts_with_l5_api_storm():
    # The REAL ApiStormSentinel wired through the api_sentinel= seam fires the trigger.
    # Kills: dropping the api consult from evaluate.
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 10.0, api_sentinel=_burst_5xx_sentinel())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert REASON_L5_API_STORM in state.triggers


def test_monitor_api_consult_passes_its_own_clock_now_into_the_storm_window():
    # Same burst (t=0..4), two monitor clocks: at now=30 the burst is in-window -> HALT;
    # at now=100 it has aged out (100-4 > 60) -> NONE with empty triggers.
    # Kills: consulting storming() with anything other than the monitor clock's now
    # (a hardcoded 0 would keep the aged burst "in-window" forever).
    fresh = AnomalyMonitor(RiskCaps(), clock=lambda: 30.0, api_sentinel=_burst_5xx_sentinel())
    assert fresh.evaluate((), _no_books).action == HALT
    aged = AnomalyMonitor(RiskCaps(), clock=lambda: 100.0, api_sentinel=_burst_5xx_sentinel())
    aged_state = aged.evaluate((), _no_books)
    assert aged_state.action == NONE
    assert aged_state.triggers == ()


def test_clock_skew_fires_ahead_of_api_storm_in_the_triggers_tuple():
    # SEVERITY ORDER (pinned): when BOTH fire, triggers is most-severe-first and
    # triggers[0] -- the set_state reason -- is l5_clock_skew.
    # Kills: swapping the skew/api consult order in evaluate.
    skew = ClockSkewSentinel(wall_clock=lambda: 100.0, ntp_ref=lambda: 0.0, caps=RiskCaps())
    monitor = AnomalyMonitor(RiskCaps(), clock=lambda: 10.0,
                             skew_sentinel=skew, api_sentinel=_burst_5xx_sentinel())
    state = monitor.evaluate((), _no_books)
    assert state.action == HALT
    assert state.triggers[0] == REASON_L5_CLOCK_SKEW
    assert state.triggers == (REASON_L5_CLOCK_SKEW, REASON_L5_API_STORM)
