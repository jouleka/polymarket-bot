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


def test_frozen_token_losses_are_excluded_from_the_weekly_sum(tmp_path):
    # DECISIONS row 74: disputed/frozen tokens leave the realized counters (their PnL is not
    # yet real). The same -36.01 that halts in D3 is inert when its token is frozen.
    # Kills: dropping the frozen filter from the weekly sum.
    with _store(tmp_path) as store:
        _realized(store, "-36.01", age=100000.0, token_id="tf")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_frozen_token_losses_are_excluded_from_the_streak(tmp_path):
    # Three trailing losses, the middle one frozen -> a filtered trailing run of 2 < 3.
    # Kills: filtering the weekly sum but streak-counting the unfiltered sequence.
    with _store(tmp_path) as store:
        _realized(store, "-1", age=100000.0, token_id="t1")
        _realized(store, "-1", age=100000.0, token_id="tf")
        _realized(store, "-1", age=100000.0, token_id="t1")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_frozen_token_losses_are_excluded_from_the_pending_loss_component(tmp_path):
    #  accepts + a  frozen loss = pending 20 (not 30) -> under the  ceiling.
    # Kills: passing the UNfiltered realized list to pending_in_window.
    with _store(tmp_path) as store:
        _accept_row(store, "20", age=100.0, token_id="t1")
        _realized(store, "-10", age=100.0, token_id="tf")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "NONE"


def test_accept_rows_on_a_frozen_token_still_count_toward_pending(tmp_path):
    # Row 74's exclusion is for REALIZED counters only -- frozen positions still count toward
    # open/pending flow.  frozen-token accept +  live loss = pending 30 > 24 -> PAUSE.
    # Kills: over-widening the frozen filter to the accept rows.
    with _store(tmp_path) as store:
        _accept_row(store, "20", age=100.0, token_id="tf")
        _realized(store, "-10", age=100.0, token_id="t1")
        state = _breakers(store).evaluate(frozen_tokens=frozenset({"tf"}))
        assert state.action == "PAUSE"
        assert state.triggers == ("daily_pending_pause",)


def test_weekly_and_pending_both_firing_halt_with_both_triggers_and_both_ramp_steps(tmp_path):
    # Losses inside 24h: weekly sum 36.01 > 36 AND pending 36.01 > 24 (streak 2 < 3 stays
    # quiet). HALT beats PAUSE; triggers most-severe-first; ramp_steps ordered
    # ("weekly", "daily") deduped. Kills: the early-return implementation that reports only
    # the first firing arm (the consumer would miss the daily tightening + the audit detail
    # would under-report provenance).
    with _store(tmp_path) as store:
        _realized(store, "-18", age=100.0)
        _realized(store, "-18.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt", "daily_pending_pause")
        assert state.ramp_steps == ("weekly", "daily")


def test_all_three_arms_firing_order_triggers_most_severe_first(tmp_path):
    # Three losses inside 24h: weekly 36.01 > 36, streak 3 >= 3, pending 36.01 > 24. Pinned
    # severity order (weekly_loss_halt, consecutive_loss, daily_pending_pause); ramp_steps
    # stay ("weekly", "daily") -- the streak arm never adds a step. Kills: any reordering of
    # the trigger tuple, or dedupe loss on ramp_steps.
    with _store(tmp_path) as store:
        _realized(store, "-12", age=100.0)
        _realized(store, "-12", age=100.0)
        _realized(store, "-12.01", age=100.0)
        state = _breakers(store).evaluate()
        assert state.action == "HALT"
        assert state.triggers == ("weekly_loss_halt", "consecutive_loss", "daily_pending_pause")
        assert state.ramp_steps == ("weekly", "daily")


# --- ERSController lossbreakers= seam (the run_cycle consult wiring) ---------------------------
from polybot.ers import safety as _safety
from polybot.ers.controller import ERSController
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _loss_state(action, triggers=(), ramp_steps=()):
    from polybot.ers.lossbreaker import LossState
    return LossState(action=action, triggers=triggers, ramp_steps=ramp_steps)


class _LossDouble:
    """Duck-typed LossBreakers double (.evaluate(frozen_tokens=...) -> LossState) recording
    the frozen_tokens it was consulted with; mutable so the sticky tests can CLEAR it."""

    def __init__(self, state):
        self.state = state
        self.frozen_seen = []

    def evaluate(self, *, frozen_tokens=frozenset()):
        self.frozen_seen.append(frozen_tokens)
        return self.state


def _rc(store, ctl, signer, *, lossbreakers=None):
    return ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                         signer=signer, controller=ctl, lossbreakers=lossbreakers,
                         clock=lambda: 0)


def test_a_none_action_lossbreakers_is_consulted_but_the_cycle_trades_exactly_as_today(tmp_path):
    # The seam exists and is consulted once per cycle (with the empty frozen set for an empty
    # portfolio), and a NONE state changes nothing: the intent ACCEPTs, no cancel_all, no
    # caps_swap, only the setup state_change in op_audit. Kills: making the seam mandatory,
    # forgetting the consult, or acting on a NONE state.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("NONE"))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert double.frozen_seen == [frozenset()]
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_lossbreakers_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Dormant-by-default: an ERSController WITHOUT the lossbreakers kwarg trades exactly as
    # before S4.7d. Expected GREEN from birth (pins the None default; the full-suite baseline
    # is the wider proof). Kills: consulting/acting when the seam is None.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=signer, controller=ctl, clock=lambda: 0)  # lossbreakers unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert signer.cancelled_all == []
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_frozen_position_tokens_are_plumbed_into_the_consult(tmp_path):
    # run_cycle feeds evaluate(frozen_tokens=...) the token_ids of FROZEN positions only
    # (row 74's live-Portfolio filter). Direct _portfolio assignment mimics the S4.5
    # boot-reconcile rebuild. Kills: passing all tokens, or never passing frozen ones.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot HALTED ok
        signer = PaperSigner()
        double = _LossDouble(_loss_state("NONE"))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc._portfolio = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m9", event_id="e9", resolution_source="s9",
                         cluster_id="c9", worst_case_risk=Decimal("8"), matrix_cold=False,
                         token_id="t9", entry_price=Decimal("0.50"), frozen=True),
            OpenPosition(condition_id="m8", event_id="e8", resolution_source="s8",
                         cluster_id="c8", worst_case_risk=Decimal("8"), matrix_cold=False,
                         token_id="t8", entry_price=Decimal("0.50"), frozen=False),
        ))
        rc.run_cycle()
        assert double.frozen_seen == [frozenset({"t9"})]


def _daily_swap_detail():
    from polybot.ers.ramp import step_daily
    return RiskCaps().content_hash()[:16] + "->" + step_daily(RiskCaps()).content_hash()[:16]


def _weekly_swap_detail():
    from polybot.ers.ramp import step_weekly
    return RiskCaps().content_hash()[:16] + "->" + step_weekly(RiskCaps()).content_hash()[:16]


def test_ramp_steps_tighten_active_caps_even_on_a_halted_loop_with_a_caps_swap_audit_row(tmp_path):
    # DESIGN §2/§6.7: swaps are applied from ls.ramp_steps in ANY op-state (tightening while
    # halted is harmless and desirable) -- and via SafetyController.swap_caps, so the audit
    # row carries reason=ramp_down and the old->new hash detail. A PAUSE verdict on a
    # boot-HALTED loop must NOT transition state (edge guard) but MUST still tighten.
    # Kills: gating the swap loop on op-state, wiring step_daily to the "weekly" key (or vice
    # versa), or bypassing swap_caps (no audit row).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("daily_pending_pause",), ("daily",)))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED                       # no downgrade, no upgrade
        assert ctl.active_caps().per_trade == Decimal("9")         # step A bit
        assert ctl.active_caps().total_open_risk == Decimal("45")
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("caps_swap", "ramp_down", _daily_swap_detail()),
        ]


def test_reapplying_the_same_ramp_step_next_cycle_is_a_hash_identical_no_op(tmp_path):
    # Idempotent swaps (DESIGN §6.7): the second cycle's step_daily(min'd caps) is
    # hash-identical -> swap_caps returns False -> NO second audit row, caps unchanged.
    # Kills: audit spam on re-application, or a step that keeps compounding.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("daily_pending_pause",), ("daily",)))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        rc.run_cycle()
        assert ctl.active_caps().per_trade == Decimal("9")
        assert len([r for r in store.op_audit_log() if r["kind"] == "caps_swap"]) == 1


class _StateSnoopingSigner(PaperSigner):
    """PaperSigner recording the op-state AT THE MOMENT cancel_all is called -- proves the
    gate closed (HALTED) BEFORE the de-risk fired."""

    def __init__(self, ctl):
        super().__init__()
        self._ctl = ctl
        self.state_at_cancel = []

    def cancel_all(self):
        self.state_at_cancel.append(self._ctl.state())
        super().cancel_all()


def test_loss_halt_from_running_swaps_then_halts_first_then_cancels_once_with_exact_rows(tmp_path):
    # DESIGN §2 step 3: swaps FIRST (any op-state), then the edge-guarded halt (set_state
    # audits it), THEN exactly ONE cancel_all with reason=triggers[0] and
    # detail=",".join(triggers). The daily step composes into the weekly one (min'd) so only
    # ONE caps_swap row appears. Kills: swapping the halt/cancel order (state_at_cancel would
    # read the live state), double-firing cancel_all, wrong reason/detail strings, or
    # applying the swaps after the halt (row order).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = _StateSnoopingSigner(ctl)
        double = _LossDouble(_loss_state(
            "HALT", ("weekly_loss_halt", "daily_pending_pause"), ("weekly", "daily")))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert signer.state_at_cancel == [_safety.HALTED]   # already closed at cancel time
        assert len(signer.cancelled_all) == 1
        assert ctl.active_caps().per_trade == Decimal("6")  # step B bit
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("caps_swap", "ramp_down", _weekly_swap_detail()),
            ("state_change", "weekly_loss_halt", _safety.HALTED),
            ("cancel_all", "weekly_loss_halt", "weekly_loss_halt,daily_pending_pause"),
        ]


def test_loss_halt_escalates_a_paused_loop_to_halted_with_the_one_shot(tmp_path):
    # PAUSED is a LIVE loop -- a weekly loss halt must still escalate it (edge guard is
    # (RUNNING, PAUSED), the S4.4 doctrine). Kills: over-tightening the guard to
    # RUNNING-only.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        signer = PaperSigner()
        double = _LossDouble(_loss_state("HALT", ("weekly_loss_halt",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert len(signer.cancelled_all) == 1
        assert ("cancel_all", "weekly_loss_halt") in [
            (r["kind"], r["reason"]) for r in store.op_audit_log()]


class _RaisingCancelSigner(PaperSigner):
    """cancel_all raises (venue/RPC down at the worst moment): the halt must already be in
    place and must SURVIVE; the failure is audited; the cycle continues."""

    def cancel_all(self):
        raise RuntimeError("venue rejected cancelAll")


def test_raising_cancel_all_is_audited_failed_and_never_unwinds_the_loss_halt_or_the_cycle(tmp_path):
    # The S4.4 pattern verbatim: gate closed FIRST, the failure lands in op_audit as
    # detail="FAILED: ...", and process_pending still runs (the pending intent REJECTs under
    # the stored weekly_loss_halt reason; the standing GTD exits are the backstop).
    # Kills: letting the exception propagate out of run_cycle (the S4.3 supervisor would
    # SIGKILL a healthy loop), or auditing an unconditional success detail.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P)
        signer = _RaisingCancelSigner()
        double = _LossDouble(_loss_state("HALT", ("weekly_loss_halt",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()                                   # must NOT raise
        assert ctl.state() == _safety.HALTED             # the halt held
        cancel_rows = [r for r in store.op_audit_log() if r["kind"] == "cancel_all"]
        assert len(cancel_rows) == 1
        assert cancel_rows[0]["reason"] == "weekly_loss_halt"
        assert cancel_rows[0]["detail"] == "FAILED: venue rejected cancelAll"
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "weekly_loss_halt"


def test_loss_pause_from_running_sets_paused_with_no_cancel_all(tmp_path):
    # DESIGN row 72/Fork 4: consecutive-loss PAUSE is sticky but NOT a de-risk -- set_state
    # only, no cancel_all, and the streak arm carries no ramp step. Kills: dropping the PAUSE
    # branch, or wiring a de-risk onto it.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.PAUSED
        assert signer.cancelled_all == []
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", _safety.RUNNING),
            ("state_change", "consecutive_loss", _safety.PAUSED),
        ]


def test_a_paused_loop_hit_by_a_pause_verdict_again_does_not_re_audit(tmp_path):
    # EDGE-triggered: the breakers evaluate every cycle, but a still-firing PAUSE on an
    # already-PAUSED loop appends nothing (no audit spam). Kills: level-triggered set_state
    # re-firing every cycle.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        rc.run_cycle()
        assert ctl.state() == _safety.PAUSED
        assert [r["kind"] for r in store.op_audit_log()].count("state_change") == 2


def test_a_halted_loop_is_never_downgraded_by_a_pause_verdict(tmp_path):
    # Severity/precedence (DESIGN §3): the loss consult never downgrades -- PAUSE fires from
    # the live state only, so a boot-HALTED loop stays HALTED with an untouched audit log.
    # Kills: widening the PAUSE edge guard to HALTED (a silent halt->pause downgrade would
    # REOPEN a killed loop to a weaker block).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        signer = PaperSigner()
        double = _LossDouble(_loss_state("PAUSE", ("consecutive_loss",), ()))
        rc = _rc(store, ctl, signer, lossbreakers=double)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED
        assert store.op_audit_log() == []
