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


# --- B3: step_daily ---------------------------------------------------------------------------


def test_step_daily_pins_the_exact_operator_signed_values():
    # Kills: any wrong step constant (fork 1 signed: per_trade 9, total 45, reserve 255, gtd 45)
    stepped = ramp.step_daily(RiskCaps())
    assert stepped.per_trade == Decimal("9")
    assert stepped.total_open_risk == Decimal("45")
    assert stepped.reserve_floor == Decimal("255")
    assert stepped.gtd_bracket_aggregate == Decimal("45")


def test_step_daily_touches_only_the_four_ratchet_fields():
    # Kills: a step that silently changes a construction-captured field (the stale-copy
    # boundary of DESIGN SS2 -- v1 steps must never touch L7/anomaly sentinel inputs)
    base = dataclasses.asdict(RiskCaps())
    stepped = dataclasses.asdict(ramp.step_daily(RiskCaps()))
    changed = {name for name in base if base[name] != stepped[name]}
    assert changed == {"per_trade", "total_open_risk", "reserve_floor", "gtd_bracket_aggregate"}


def test_step_daily_reconstructs_a_verified_riskcaps_with_a_fresh_hash():
    # dataclasses.replace re-runs __post_init__/_verify, so returning at all proves
    # constructibility. Kills: returning a non-RiskCaps bag / a hash that does not change
    # (the caps_swap audit detail would show old==new)
    stepped = ramp.step_daily(RiskCaps())
    assert isinstance(stepped, RiskCaps)
    assert stepped.content_hash() != RiskCaps().content_hash()


def test_step_daily_passes_the_tighten_only_guard():
    # Kills: a step constant drifting loose -- swap_caps would refuse its own ramp step
    ramp.assert_tighten_only(RiskCaps(), ramp.step_daily(RiskCaps()))  # must not raise


def test_step_daily_is_idempotent_by_hash():
    # Kills: a subtractive step (per_trade - 3 style) that keeps tightening on re-application
    # (run_cycle re-applies steps every cycle while the trigger holds -- must be a no-op)
    once = ramp.step_daily(RiskCaps())
    assert ramp.step_daily(once).content_hash() == once.content_hash()


# --- B4: step_weekly + composition ------------------------------------------------------------


def test_step_weekly_pins_the_exact_operator_signed_values():
    # Kills: any wrong weekly constant (fork 1 signed: per_trade 6, total 30, reserve 270, gtd 30)
    stepped = ramp.step_weekly(RiskCaps())
    assert stepped.per_trade == Decimal("6")
    assert stepped.total_open_risk == Decimal("30")
    assert stepped.reserve_floor == Decimal("270")
    assert stepped.gtd_bracket_aggregate == Decimal("30")


def test_step_weekly_passes_the_tighten_only_guard():
    # Kills: the weekly constants drifting loose -- swap_caps would refuse the step
    ramp.assert_tighten_only(RiskCaps(), ramp.step_weekly(RiskCaps()))  # must not raise


def test_step_weekly_after_daily_composes_to_weekly():
    # The pinned compose law: weekly(daily(c)) == weekly(c) by content hash.
    # Kills: a step pair that cannot stack (a daily breach then a weekly halt must land
    # exactly on the deeper weekly envelope)
    assert (ramp.step_weekly(ramp.step_daily(RiskCaps())).content_hash()
            == ramp.step_weekly(RiskCaps()).content_hash())


def test_step_daily_after_weekly_never_loosens_back():
    # Kills: dropping the min() -- a later daily trigger must NOT relax the deeper weekly
    # step from 6/30 back to 9/45 (that swap would also be refused, wedging the ramp)
    assert (ramp.step_daily(ramp.step_weekly(RiskCaps())).content_hash()
            == ramp.step_weekly(RiskCaps()).content_hash())


def test_step_weekly_is_idempotent_by_hash():
    # Kills: a subtractive weekly step that keeps tightening on re-application
    once = ramp.step_weekly(RiskCaps())
    assert ramp.step_weekly(once).content_hash() == once.content_hash()
