"""S4.6a — the L8 auth core (telegram_auth.py) + the new safety.py reason constants."""
from polybot.ers import safety as _safety


def test_new_l8_reason_constants_exist_with_exact_values():
    # Kills: mutation deleting/renaming any of the 4 NEW S4.6 reason constants, or drifting a value.
    assert _safety.REASON_L8_RESUME == "l8_resume"
    assert _safety.REASON_L8_LOWER_CAPS == "l8_lower_caps"
    assert _safety.REASON_L8_BLACKLIST == "l8_blacklist"
    assert _safety.REASON_L8_ALERTS_DOWN == "l8_alerts_down"


def test_preexisting_l8_reason_constants_unchanged():
    # Kills: mutation that accidentally edits the S4.1 constants while adding the new ones.
    assert _safety.REASON_L8_KILL == "l8_kill"
    assert _safety.REASON_L8_PAUSED == "l8_paused"
    assert _safety.REASON_OP_FLATTEN == "op_flatten"
