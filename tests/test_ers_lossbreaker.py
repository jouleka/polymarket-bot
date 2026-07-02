"""Realized-loss breakers (S4.7d / POL-6) -- weekly halt, consecutive-loss pause, pending pause.

LossState/LossBreakers over the durable flow_journal, the run_cycle consult (idempotent ramp
swaps in any op-state + edge-guarded sticky transitions + the weekly one-shot best-effort
cancel_all), and the DESIGN-S4.7 §8.3 whole-slice e2e. Clocks are injected 0-arg callables;
money is Decimal from string literals; helpers are copied per file per convention (no conftest).
"""

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore

_NOW = 1000000.0   # the injected wall-clock instant every direct-unit test evaluates at


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _breakers(store):
    from polybot.ers.lossbreaker import LossBreakers
    return LossBreakers(store=store, caps_provider=lambda: RiskCaps(),
                        wall_clock=lambda: _NOW)


def _realized(store, amount, *, age, token_id="t1"):
    # A realized-PnL journal row `age` seconds before _NOW (negative amount == a loss).
    store.record_flow_event(kind="realized", token_id=token_id, amount=Decimal(amount),
                            wall_at=_NOW - age)


def _accept_row(store, amount, *, age, token_id="t1"):
    # An accept-flow journal row (amount == the position's worst_case_risk).
    store.record_flow_event(kind="accept", token_id=token_id, amount=Decimal(amount),
                            wall_at=_NOW - age)


def test_lossbreaker_module_action_vocab_is_none_pause_halt_exact_strings():
    # Kills: changing any action constant's string (the controller compares by value).
    from polybot.ers import lossbreaker as _lb
    assert _lb.NONE == "NONE"
    assert _lb.PAUSE == "PAUSE"
    assert _lb.HALT == "HALT"


def test_loss_state_is_a_frozen_dataclass_with_action_triggers_and_ramp_steps():
    # Kills: dropping frozen=True or renaming action/triggers/ramp_steps.
    from polybot.ers.lossbreaker import HALT, LossState
    state = LossState(action=HALT, triggers=("weekly_loss_halt",), ramp_steps=("weekly",))
    assert state.action == "HALT"
    assert state.triggers == ("weekly_loss_halt",)
    assert state.ramp_steps == ("weekly",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.action = "NONE"


def test_loss_state_halt_with_empty_triggers_is_unrepresentable():
    # Kills: removing the __post_init__ guard -- a triggerless HALT reaches the controller's
    # triggers[0] as an IndexError at the exact moment the weekly breaker fires.
    from polybot.ers.lossbreaker import HALT, LossState
    with pytest.raises(ValueError):
        LossState(action=HALT, triggers=(), ramp_steps=())


def test_loss_state_pause_with_empty_triggers_is_unrepresentable():
    # Kills: narrowing the guard to HALT-only (the PAUSE path also indexes triggers[0]).
    from polybot.ers.lossbreaker import PAUSE, LossState
    with pytest.raises(ValueError):
        LossState(action=PAUSE, triggers=(), ramp_steps=())


def test_loss_state_none_with_empty_triggers_constructs_fine():
    # Boundary partner of the two tests above. Kills: over-widening the guard to NONE.
    from polybot.ers.lossbreaker import NONE, LossState
    state = LossState(action=NONE, triggers=(), ramp_steps=())
    assert state.triggers == ()


def test_lossbreaker_module_source_never_references_the_resume_state_or_set_state():
    # STICKY structural pin (DESIGN §6.2, mirrors the anomaly.py scan): nothing in
    # ers/lossbreaker.py may transition op-state or even NAME the resume state -- the ONLY
    # automatic HALTED->resume stays the clean boot-reconcile.
    # Kills: any auto-resume or op-state mutation creeping into the module.
    from polybot.ers import lossbreaker as _lb
    src = Path(_lb.__file__).read_text(encoding="utf-8")
    assert "set_state" not in src
    assert "RUNNING" not in src


def test_evaluate_over_an_empty_journal_returns_none_the_shadow_data_gated_state(tmp_path):
    # DESIGN §7: realized rows don't exist until POL-4/S9, so in shadow the breakers evaluate
    # an empty set and stay NONE forever. Kills: any arm firing over zero rows.
    with _store(tmp_path) as store:
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()
        assert state.ramp_steps == ()


class _RaisingFlowLogStore:
    """A store whose flow_log raises -- corruption in OUR OWN safety ledger."""

    def flow_log(self):
        raise RuntimeError("journal corrupted")


def test_a_raising_flow_log_fails_closed_to_halt_with_flow_data_error(tmp_path):
    # DESIGN §6.4: a raising/malformed flow_journal read makes the breakers HALT with
    # flow_data_error -- never silent, never propagating. ramp_steps stays () (no blind
    # tightening off unreadable data). Kills: letting the raise escape evaluate, or
    # except-ing to NONE (which would let the loop keep trading on corrupt safety data).
    from polybot.ers.lossbreaker import LossBreakers
    breakers = LossBreakers(store=_RaisingFlowLogStore(), caps_provider=lambda: RiskCaps(),
                            wall_clock=lambda: _NOW)
    state = breakers.evaluate()   # must NOT raise
    assert state.action == "HALT"
    assert state.triggers == ("flow_data_error",)
    assert state.ramp_steps == ()


def test_weekly_losses_summing_to_exactly_36_do_not_halt(tmp_path):
    # Boundary pair, at-the-cap side: DESIGN row 71 is a STRICT > on weekly_loss_halt ().
    # Kills: >= instead of > on the weekly sum.
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100000.0)
        _realized(store, "-18", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()


def test_weekly_losses_summing_to_36_01_halt_with_the_weekly_trigger_and_ramp_step(tmp_path):
    # Boundary pair, just-over side: 36.01 > 36 -> HALT(weekly_loss_halt) + ramp step B.
    # Kills: dropping the weekly arm, wrong reason string, or forgetting the "weekly" step.
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100000.0)
        _realized(store, "-18.01", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt",)
        assert state.ramp_steps == ("weekly",)


def test_a_loss_exactly_at_the_7d_window_edge_is_included(tmp_path):
    # Window boundary pair, in side: now - wall_at == 604800 is INCLUSIVE (the breaker/ApiStorm
    # convention -- keeping the boundary row is tighter). Kills: < instead of <= on the edge.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=604800.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt",)


def test_a_loss_just_older_than_the_7d_window_is_excluded(tmp_path):
    # Window boundary pair, out side: age 604801 falls out of the weekly sum (and the single
    # trailing loss is a streak of 1 < 3, so nothing else fires). Kills: a windowless weekly
    # sum, or an off-by-one widening of the window.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=604801.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"
        assert state.triggers == ()


def test_two_trailing_losses_do_not_pause(tmp_path):
    # Streak boundary pair, under side: caps.consecutive_loss == 3. Kills: > vs >= confusion
    # lowering the threshold to 2.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_three_trailing_losses_pause_with_the_consecutive_trigger_and_no_ramp_step(tmp_path):
    # Streak boundary pair, at side: 3 >= 3 -> PAUSE(consecutive_loss). The streak arm carries
    # NO ramp step (only the weekly and pending arms tighten caps). Kills: dropping the streak
    # arm, wrong reason string, or attaching a ramp step to it.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("consecutive_loss",)
        assert state.ramp_steps == ()


def test_a_positive_win_mid_sequence_resets_the_streak(tmp_path):
    # The streak is the TRAILING run at the END of the realized sequence: 4 losses total but a
    # +1 win splits them into a trailing run of 2. Kills: counting ALL losses instead of the
    # trailing run (4 >= 3 would wrongly pause).
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_a_zero_amount_realized_row_counts_as_a_win_and_resets_the_streak(tmp_path):
    # amount >= 0 breaks the trail -- zero is the boundary value of "win". Kills: treating
    # amount <= 0 as a loss (a scratch exit would wrongly extend the streak).
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "0", age=100000.0)
        _realized(store, "-1", age=100000.0)
        _realized(store, "-1", age=100000.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_the_streak_has_no_time_window_so_ancient_losses_still_count(tmp_path):
    # DESIGN row 72: the streak is windowless (only a WIN resets it). Losses far older than 7d
    # contribute nothing to the weekly sum yet still form the trailing streak. Kills: adding a
    # wall-clock window filter to the streak arm.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=10000000.0)
        _realized(store, "-1", age=10000000.0)
        _realized(store, "-1", age=10000000.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("consecutive_loss",)


def test_pending_of_exactly_24_does_not_pause(tmp_path):
    # Pending-arm boundary pair, at side: pending_in_window == daily_pending_ceiling () is
    # a STRICT >, so at-the-ceiling does not fire. Kills: >= on the pending comparison.
    with _store(tmp_path) as store:
        _accept_row(store, "12", age=100.0)
        _accept_row(store, "12", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "NONE"


def test_pending_of_24_01_pauses_with_daily_pending_pause_and_the_daily_ramp_step(tmp_path):
    # Pending-arm boundary pair, over side (rows 70-vs-72 interplay: only a REALIZED LOSS can
    # push pending past the gate-guarded ceiling -- here a /usr/bin/zsh.01 loss joins  of accepts).
    # 24.01 > 24 -> PAUSE(daily_pending_pause) + ramp step A ("daily"). Kills: dropping the
    # pending arm, wrong reason, or forgetting the "daily" step.
    with _store(tmp_path) as store:
        _accept_row(store, "12", age=100.0)
        _accept_row(store, "12", age=100.0)
        _realized(store, "-0.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "PAUSE"
        assert state.triggers == ("daily_pending_pause",)
        assert state.ramp_steps == ("daily",)
