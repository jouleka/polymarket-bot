"""The tighten-only caps ratchet (S4.7 / POL-6 -- DESIGN-S4.7-BREAKERS.md SS4/SS6.1).

TIGHTEN_DIRECTION classifies EVERY RiskCaps field: "down" (a tighter value is lower),
"up" (reserve_floor: a tighter value is higher), or "fixed" (any change refused in v1 --
nav, the dust floor, and the two counting windows whose direction is genuinely ambiguous).
A structural test pins the map keys against dataclasses.fields(RiskCaps), so adding a caps
field without classifying it here fails loudly.

This module is PURE over caps values: it never touches op-state, the store, or a clock.
"""

import dataclasses

TIGHTEN_DIRECTION = {
    # Capital band.
    "nav": "fixed",
    "total_open_risk": "down",
    "reserve_floor": "up",
    # Per-intent caps.
    "per_trade": "down",
    "per_market": "down",
    "per_event_union": "down",
    "per_negrisk_event": "down",
    "per_source_open": "down",
    "per_source_locked_effective": "down",
    "max_locked_to_resolution": "down",
    # Concurrency.
    "max_concurrent": "down",
    "matrix_cold_concurrent": "down",
    # Breakers / sizing.
    "daily_pending_ceiling": "down",
    "kelly_fraction": "down",
    "min_position_floor": "fixed",
    "liquidity_depth_frac": "down",
    "liquidity_impact_cents": "down",
    # L7 drawdown breaker.
    "l7_freeze_floor": "down",
    "l7_flatten_floor": "down",
    "l7_velocity_delta": "down",
    "l7_velocity_window_seconds": "fixed",
    # S4 safety envelope.
    "weekly_loss_halt": "down",
    "consecutive_loss": "down",
    "new_positions_per_hour": "down",
    "new_positions_per_day": "down",
    "gtd_bracket_aggregate": "down",
    "clock_skew_tolerance_seconds": "down",
    "signing_canary_interval_seconds": "down",
    "dead_man_switch_timeout_seconds": "down",
    "reconcile_tolerance": "down",
    "reconcile_settle_window_seconds": "down",
    # S4.4 L5 anomaly thresholds.
    "midpoint_jump_halt": "down",
    "depth_collapse_fraction": "down",
    "depth_collapse_min_prev_shares": "down",
    "ws_staleness_halt_seconds": "down",
    "api_5xx_storm_count": "down",
    "api_auth_storm_count": "down",
    "api_storm_window_seconds": "fixed",
}


def assert_tighten_only(old, new):
    """Raise ValueError naming the first field (declaration order) whose old->new change
    violates its TIGHTEN_DIRECTION class: "down" requires new <= old, "up" requires
    new >= old, "fixed" requires new == old. Equal is always acceptable. Pure comparison
    over getattr -- the caller (swap_caps) owns construction/_verify of the new caps."""
    for field in dataclasses.fields(old):
        direction = TIGHTEN_DIRECTION[field.name]
        old_value = getattr(old, field.name)
        new_value = getattr(new, field.name)
        if new_value == old_value:
            continue
        if direction == "fixed" or (direction == "down" and new_value > old_value) \
                or (direction == "up" and new_value < old_value):
            raise ValueError(
                f"tighten-only violation on {field.name} ({direction}): "
                f"{old_value} -> {new_value}")
