"""S4.7b (POL-6) -- the tighten-only caps ratchet.

TIGHTEN_DIRECTION over all 38 RiskCaps fields, the assert_tighten_only guard, the two
operator-signed step factories (daily 9/45, weekly 6/30), SafetyController.swap_caps
(audit-before-mutate, no-op-safe), and the run_cycle active_caps() re-plumb so a swap
bites the NEXT cycle's validator. DESIGN-S4.7-BREAKERS.md SS4/SS6.1/SS6.7.
"""

import dataclasses
from pathlib import Path

from polybot.ers import ramp
from polybot.ers.caps import RiskCaps


def test_tighten_direction_covers_exactly_the_riskcaps_fields():
    # Kills: a TIGHTEN_DIRECTION key dropped/renamed, or a future RiskCaps field added unclassified
    assert set(ramp.TIGHTEN_DIRECTION) == {f.name for f in dataclasses.fields(RiskCaps)}
    assert len(ramp.TIGHTEN_DIRECTION) == 38


def test_tighten_direction_classification_is_the_pinned_one():
    # Kills: misclassifying any field (e.g. reserve_floor as "down" would let the ratchet
    # shrink the reserve; a window field as "down" would falsely permit ambiguous changes)
    assert set(ramp.TIGHTEN_DIRECTION.values()) <= {"down", "up", "fixed"}
    assert {k for k, v in ramp.TIGHTEN_DIRECTION.items() if v == "up"} == {"reserve_floor"}
    assert {k for k, v in ramp.TIGHTEN_DIRECTION.items() if v == "fixed"} == {
        "nav", "min_position_floor", "l7_velocity_window_seconds", "api_storm_window_seconds"}
    assert sum(1 for v in ramp.TIGHTEN_DIRECTION.values() if v == "down") == 33


def test_ramp_source_never_touches_op_state():
    # Kills: a future ramp.py edit that drives op-state (the no-new-auto-resume structural
    # pin, DESIGN SS6.2 -- mirrors the anomaly-module scan)
    source = Path(ramp.__file__).read_text()
    assert "set_state" not in source
    assert "RUNNING" not in source
