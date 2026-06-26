"""S3 / POL-5 slice 3 -- L7 real-time unrealized-drawdown breaker (§4 L7).

The breaker marks every non-frozen open position to the live book midpoint, sums the net
unrealized drawdown, and emits FREEZE_ADDS / FLATTEN / velocity / stale-mark signals. It is
STATEFUL (the velocity trigger needs a rolling history of the mark), unlike the pure validator,
and fails closed: an un-markable position freezes + alerts (never FLATTEN blind).
"""

from decimal import Decimal

from polybot.ers.breaker import FLATTEN, FREEZE_ADDS, NONE, DrawdownBreaker
from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition


class _Book:
    def __init__(self, mid):
        self._mid = mid

    def midpoint(self):
        return self._mid


def _book_for(marks):
    """marks: {token_id: mid|None}. A missing token -> no book (None); an explicit None mid
    -> a present-but-unmarkable (stale/crossed) book."""
    def f(token):
        if token not in marks:
            return None
        return _Book(marks[token])
    return f


def _clock(*times):
    it = iter(times)
    return lambda: next(it)


def _pos(token, entry, risk, *, frozen=False):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal(risk), matrix_cold=False,
                        token_id=token, entry_price=Decimal(entry), frozen=frozen)


def test_profit_or_flat_portfolio_is_no_action():
    # entry 0.50, $12 notional -> 24 shares; mid 0.60 -> +$2.40 P&L -> negative drawdown.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "12")], _book_for({"A": Decimal("0.60")}))
    assert state.action == NONE


def test_freeze_adds_above_the_freeze_floor():
    # 48 shares ($24 @ 0.50); mid 0.10 -> -$19.20 -> drawdown 19.20 in ($18, $30] -> FREEZE.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "24")], _book_for({"A": Decimal("0.10")}))
    assert state.action == FREEZE_ADDS
    assert state.drawdown == Decimal("19.20")
    assert "freeze_floor" in state.triggers


def test_flatten_above_the_flatten_floor():
    # two $18 positions (36 shares each) marked to 0.05 -> -$16.20 each -> drawdown $32.40 > $30.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "18"), _pos("B", "0.50", "18")],
                       _book_for({"A": Decimal("0.05"), "B": Decimal("0.05")}))
    assert state.action == FLATTEN
    assert "flatten_floor" in state.triggers


def test_frozen_positions_are_excluded_from_the_drawdown():
    # only A counts ($16.20 < $18 -> NONE); if frozen B were counted it would be $32.40 -> FLATTEN.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "18"), _pos("B", "0.50", "18", frozen=True)],
                       _book_for({"A": Decimal("0.05"), "B": Decimal("0.05")}))
    assert state.action == NONE


def test_unmarkable_position_freezes_and_alerts_never_flattens_blind():
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "18")], _book_for({"A": None}))  # stale/crossed book
    assert state.action == FREEZE_ADDS
    assert "stale_mark" in state.triggers


def test_confirmed_flatten_still_flattens_even_with_a_stale_position():
    # a stale mark never flattens BLIND, but a confirmed >$30 loss still flattens (and flags stale).
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate(
        [_pos("A", "0.50", "18"), _pos("B", "0.50", "18"), _pos("C", "0.50", "18")],
        _book_for({"A": Decimal("0.05"), "B": Decimal("0.05"), "C": None}))
    assert state.action == FLATTEN
    assert "flatten_floor" in state.triggers and "stale_mark" in state.triggers


def test_velocity_trigger_on_a_fast_rise_below_the_freeze_floor():
    # drawdown swings from -$7.20 (profit) to +$11.52 within the window: a rise of $18.72 > $18,
    # while the level $11.52 stays UNDER the $18 freeze floor -> FREEZE via velocity alone.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0, 100))
    book_hi = _book_for({"A": Decimal("0.60"), "B": Decimal("0.60")})
    book_lo = _book_for({"A": Decimal("0.34"), "B": Decimal("0.34")})
    positions = [_pos("A", "0.50", "18"), _pos("B", "0.50", "18")]
    assert b.evaluate(positions, book_hi).action == NONE          # -$7.20 drawdown
    state = b.evaluate(positions, book_lo)
    assert state.action == FREEZE_ADDS
    assert "velocity" in state.triggers and "freeze_floor" not in state.triggers


def test_single_position_loss_above_freeze_floor_is_not_masked_by_a_paper_gain():
    # M1 (review): net drawdown alone is only $9.60 (would be NONE), but position A is down $24
    # (> the $18 freeze floor) while B shows a $14.40 paper gain. A single catastrophic position
    # must not be hidden by another's (possibly non-exitable) unrealized gain -> at least FREEZE.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0))
    state = b.evaluate([_pos("A", "0.50", "30"), _pos("B", "0.50", "18")],
                       _book_for({"A": Decimal("0.10"), "B": Decimal("0.90")}))
    assert state.action == FREEZE_ADDS
    assert "position_loss" in state.triggers


def test_velocity_window_inclusive_boundary_at_exactly_the_window_edge():
    # L1 (review): a sample at exactly now - window is RETAINED (inclusive window), so a fast rise
    # measured across the full 900s edge still fires velocity. Pins the inclusive semantics.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0, 900))
    book_hi = _book_for({"A": Decimal("0.60"), "B": Decimal("0.60")})
    book_lo = _book_for({"A": Decimal("0.34"), "B": Decimal("0.34")})
    positions = [_pos("A", "0.50", "18"), _pos("B", "0.50", "18")]
    b.evaluate(positions, book_hi)                       # drawdown -$7.20 at ts 0
    state = b.evaluate(positions, book_lo)               # +$11.52 at ts 900 (edge, retained)
    assert "velocity" in state.triggers


def test_velocity_window_prunes_a_stale_low_so_no_false_trigger():
    # same swing, but the second mark is 2000s later -> the earlier profit low is outside the
    # 900s window -> no rise measured -> level $11.52 < $18 -> NONE.
    b = DrawdownBreaker(RiskCaps(), clock=_clock(0, 2000))
    book_hi = _book_for({"A": Decimal("0.60"), "B": Decimal("0.60")})
    book_lo = _book_for({"A": Decimal("0.34"), "B": Decimal("0.34")})
    positions = [_pos("A", "0.50", "18"), _pos("B", "0.50", "18")]
    b.evaluate(positions, book_hi)
    assert b.evaluate(positions, book_lo).action == NONE
