"""S4.7b (POL-6) -- the tighten-only caps ratchet.

TIGHTEN_DIRECTION over all 38 RiskCaps fields, the assert_tighten_only guard, the two
operator-signed step factories (daily 9/45, weekly 6/30), SafetyController.swap_caps
(audit-before-mutate, no-op-safe), and the run_cycle active_caps() re-plumb so a swap
bites the NEXT cycle's validator. DESIGN-S4.7-BREAKERS.md SS4/SS6.1/SS6.7.
"""

import dataclasses
import types
from decimal import Decimal
from pathlib import Path

import pytest

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


# --- B2: assert_tighten_only ------------------------------------------------------------------


def _fake_caps(**overrides):
    # A RiskCaps-SHAPED attribute bag that BYPASSES _verify: lets a test loosen exactly ONE
    # field in isolation (a real RiskCaps couples nav/total_open_risk/reserve_floor via
    # _verify, so e.g. "only reserve_floor lowered" is unconstructible). assert_tighten_only
    # iterates dataclasses.fields(old) and only getattr()s new, so a namespace suffices.
    values = dataclasses.asdict(RiskCaps())
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_assert_tighten_only_accepts_byte_equal_caps():
    # Kills: an inverted comparison rejecting equality (equal is ALWAYS a legal swap input)
    ramp.assert_tighten_only(RiskCaps(), RiskCaps())  # must not raise


def test_assert_tighten_only_accepts_a_lower_down_field():
    # Kills: the "down" arm written as new >= old (a strictly lower value must pass)
    ramp.assert_tighten_only(RiskCaps(), RiskCaps(per_trade=Decimal("9")))  # must not raise


def test_assert_tighten_only_rejects_a_just_over_down_field_naming_it():
    # Boundary pair with the equal/lower accepts: per_trade 12 -> 12.01 is a loosening.
    # Kills: the "down" comparison dropped or mutated to >=
    with pytest.raises(ValueError, match="per_trade"):
        ramp.assert_tighten_only(RiskCaps(), RiskCaps(per_trade=Decimal("12.01")))


def test_assert_tighten_only_accepts_a_higher_up_field():
    # reserve_floor 240 -> 255 (the daily-step shape, built as a REAL verified RiskCaps).
    # Kills: treating "up" like "down" (a GROWN reserve would be refused)
    tightened = RiskCaps(per_trade=Decimal("9"), total_open_risk=Decimal("45"),
                         reserve_floor=Decimal("255"), gtd_bracket_aggregate=Decimal("45"))
    ramp.assert_tighten_only(RiskCaps(), tightened)  # must not raise


def test_assert_tighten_only_rejects_a_just_under_up_field_naming_it():
    # Boundary pair: reserve_floor 240 -> 239.99 shrinks the reserve. Kills: the "up" arm dropped
    with pytest.raises(ValueError, match="reserve_floor"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(reserve_floor=Decimal("239.99")))


def test_assert_tighten_only_rejects_a_raised_fixed_field_nav():
    # Kills: "fixed" degraded to "up" (a raised nav must still be refused)
    with pytest.raises(ValueError, match="nav"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(nav=Decimal("301")))


def test_assert_tighten_only_rejects_a_lowered_fixed_field_min_position_floor():
    # Kills: "fixed" degraded to "down" (a lowered dust floor must still be refused)
    with pytest.raises(ValueError, match="min_position_floor"):
        ramp.assert_tighten_only(RiskCaps(), _fake_caps(min_position_floor=Decimal("4.99")))
