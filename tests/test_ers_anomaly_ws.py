"""S4.4d / POL-6 -- L5 AnomalyMonitor: the l5_ws_down seam path.

CLOCK DOMAINS (pinned; the S4.5 lesson): the monitor's clock= is float monotonic
SECONDS (time.monotonic in prod); MarketStream frame stamps are MonotonicStamper-
domain NANOSECONDS (time.monotonic_ns) -- the SAME monotonic family, so
age_s = now_s - last_frame_at_ns / 1e9. Tests inject BOTH explicitly
(e.g. clock=lambda: 100.0 with a frame stamp of 60_000_000_000 ns = 40 s age).
"""

import types

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_CLOCK_SKEW, REASON_L5_WS_DOWN


def _no_books(_token):
    """book_for stub: the ws path never touches books (positions stay empty)."""
    return None


def _monitor(**seams):
    """A monitor at now=100.0 monotonic-SECONDS; every unlisted seam stays None (dormant)."""
    return AnomalyMonitor(RiskCaps(), clock=seams.pop("clock", lambda: 100.0), **seams)


def _skewed_sentinel():
    """Duck-typed skew sentinel (.skewed() -> bool), always skewed."""
    return types.SimpleNamespace(skewed=lambda: True)


def test_ws_seam_none_keeps_the_ws_trigger_dormant():
    """Kills: consulting the seam without the `is not None` guard -- calling a None
    seam raises TypeError, and the fail-closed except would then fire a false
    l5_ws_down on every bare monitor."""
    state = _monitor().evaluate((), _no_books)

    assert state.action == NONE
    assert state.triggers == ()


def test_ws_wired_but_silent_none_stamp_fires_ws_down():
    """A WIRED callable returning None = never saw a frame = +inf age -> down
    (mirrors the heartbeat's fail-closed stance). Kills: treating None as 'skip'
    (the recon-seam semantic) instead of 'fire'."""
    state = _monitor(ws_last_frame_at=lambda: None).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_raising_seam_fails_closed_and_fires_ws_down():
    """FAIL-CLOSED SEAM RULE: a raising seam IS the anomaly it guards. Kills:
    letting the exception propagate out of evaluate, or masking it silently."""
    def _boom():
        raise RuntimeError("socket introspection exploded")

    state = _monitor(ws_last_frame_at=_boom).evaluate((), _no_books)

    assert state.action == HALT
    assert REASON_L5_WS_DOWN in state.triggers


def test_ws_down_is_collected_after_clock_skew_in_severity_order():
    """SEVERITY ORDER: ws is the LAST consult, so triggers[0] (the set_state reason)
    must be the skew when both fire. Kills: appending ws ahead of the other seams."""
    state = _monitor(skew_sentinel=_skewed_sentinel(),
                     ws_last_frame_at=lambda: None).evaluate((), _no_books)

    assert state.action == HALT
    assert state.triggers == (REASON_L5_CLOCK_SKEW, REASON_L5_WS_DOWN)
