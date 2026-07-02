"""L5 AnomalyMonitor -- the anomaly kill-switch spine (S4.4a / POL-6).

Sub-slice A: the l5_* reason constants, the AnomalyState/AnomalyMonitor skeleton driven by a
duck-typed skew-sentinel double (the real ClockSkewSentinel is S4.4b), the fail-closed
raising-seam rule, all-seams-None inertness, and the ERSController anomaly= seam --
edge-triggered halt-first one-shot cancel_all with exact op-audit rows, sticky semantics, and
a raising signer that never unwinds the halt. Clocks are injected; helpers are copied per
file per convention (no conftest)."""


def test_s4_4_l5_reason_constants_exist_with_exact_strings():
    # S4.4a defines the five NET-NEW l5_* reason codes (l5_recon_mismatch already exists from
    # S4.5). Free-form Decision.reason / op-audit strings, NO validator/schema change -- the
    # existing REASON_* convention (mirrors test_s4_5_reason_constants_exist).
    # MUTATION KILLED: renaming any constant or typo-ing its string (the controller reports
    # these verbatim as the halt reason).
    from polybot.ers import safety as _s
    assert _s.REASON_L5_CLOCK_SKEW == "l5_clock_skew"
    assert _s.REASON_L5_ABNORMAL_BOOK == "l5_abnormal_book"
    assert _s.REASON_L5_API_STORM == "l5_api_storm"
    assert _s.REASON_L5_WS_DOWN == "l5_ws_down"
    assert _s.REASON_L5_CANARY_FAIL == "l5_canary_fail"
    # The pre-existing S4.5 constant this slice consumes (guards accidental removal).
    assert _s.REASON_L5_RECON_MISMATCH == "l5_recon_mismatch"
