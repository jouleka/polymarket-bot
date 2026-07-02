"""S4.4c -- L5 abnormal-book checks (DESIGN-S4.4-ANOMALY.md §3 trigger 1).

Driven purely through positions + book_for with REAL LocalBook instances; the monitor
is constructed bare (caps + clock only) because these checks need no seam.
"""

from decimal import Decimal

from polybot.ers.anomaly import HALT, NONE, AnomalyMonitor
from polybot.ers.caps import RiskCaps
from polybot.ers.safety import REASON_L5_ABNORMAL_BOOK
from polybot.ers.validator import OpenPosition
from polybot.ingestion.orderbook import LocalBook


def _monitor():
    """Bare monitor: caps + clock only (0-arg monotonic-SECONDS clock, injected)."""
    return AnomalyMonitor(RiskCaps(), clock=lambda: 0.0)


def _pos(token_id, *, frozen=False):
    return OpenPosition(condition_id="m", event_id="e", resolution_source="s", cluster_id="c",
                        worst_case_risk=Decimal("8"), matrix_cold=False, token_id=token_id,
                        entry_price=Decimal("0.50"), frozen=frozen)


def _book(*, bid=None, ask=None, bid_size="500", ask_size="500"):
    """Fresh LocalBook from one full snapshot (apply_book marks it NON-stale).
    None on a side = that side empty."""
    bids = [{"price": bid, "size": bid_size}] if bid is not None else []
    asks = [{"price": ask, "size": ask_size}] if ask is not None else []
    book = LocalBook()
    book.apply_book({"bids": bids, "asks": asks})
    return book


def test_non_stale_crossed_book_fires_l5_abnormal_book():
    # Kills: deleting the structural midpoint()-is-None check entirely.
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")  # bid > ask -> crossed -> midpoint None
    assert book.is_stale() is False and book.midpoint() is None  # precondition sanity
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_locked_book_fires_l5_abnormal_book():
    # Kills: weakening the LocalBook contract's bid >= ask to bid > ask (locked = bid == ask).
    mon = _monitor()
    book = _book(bid="0.50", ask="0.50")
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_non_stale_empty_ask_side_fires_l5_abnormal_book():
    # Kills: only checking crossed prices and skipping the empty-side midpoint-None case.
    mon = _monitor()
    book = _book(bid="0.40")  # asks empty; apply_book still marks the book non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_stale_crossed_book_does_not_fire_stale_is_breaker_domain():
    # Kills: dropping the is_stale() gate (stale books belong to validator book_stale /
    # breaker stale_mark, NOT L5 -- design §0 'abnormal-book checks run on NON-stale books only').
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    book.mark_stale()
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_frozen_position_book_is_still_checked_and_fires():
    # Kills: copying the breaker's 'if pos.frozen: continue' -- anomaly checks book
    # STRUCTURE, frozen positions still have books (pinned contract: skip frozen? NO).
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")
    state = mon.evaluate([_pos("t1", frozen=True)], lambda token: book)
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_healthy_book_fires_nothing_action_none_triggers_empty():
    # Kills: inverting the midpoint()-is-None condition (firing on every VALID book).
    mon = _monitor()
    book = _book(bid="0.49", ask="0.51")  # mid 0.50, both sides present, non-stale
    state = mon.evaluate([_pos("t1")], lambda token: book)
    assert state.action == NONE
    assert state.triggers == ()


def test_missing_book_none_is_skipped_silently():
    # Kills: treating an ABSENT book as abnormal (book None = validator no_book domain),
    # or calling methods on None (AttributeError would escape evaluate).
    mon = _monitor()
    state = mon.evaluate([_pos("t1")], lambda token: None)
    assert state.action == NONE
    assert state.triggers == ()


def test_first_observation_of_token_never_fires_midpoint_jump():
    # Kills: seeding prev-mid with a default (e.g. 0 -> |0.40 - 0| >= 0.15 would false-fire
    # the very first time a token is seen). First observation is memory-building ONLY.
    mon = _monitor()
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))  # mid 0.40
    assert state.action == NONE
    assert state.triggers == ()


def test_midpoint_jump_of_exactly_the_threshold_0_15_fires():
    # Boundary pair, AT threshold: design says |mid - prev_mid| >= midpoint_jump_halt (0.15).
    # 0.40 -> 0.55 is EXACTLY 0.15. Kills: '>=' -> '>' on the jump compare.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))          # mid 0.40
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.54", ask="0.56"))  # mid 0.55
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_midpoint_jump_just_under_the_threshold_does_not_fire():
    # Boundary pair, JUST UNDER: 0.40 -> 0.549 = 0.149 < 0.15. Kills: loosening the
    # threshold or comparing against the wrong caps field.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))            # mid 0.40
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.539", ask="0.559"))  # mid 0.549
    assert state.action == NONE
    assert state.triggers == ()


def test_midpoint_drop_of_exactly_the_threshold_0_15_fires():
    # Kills: dropping abs() -- a DOWNWARD jump (0.55 -> 0.40) is exactly as anomalous.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.54", ask="0.56"))          # mid 0.55
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.39", ask="0.41"))  # mid 0.40
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_stale_interlude_preserves_prev_mid_so_drift_across_the_gap_still_fires():
    # Kills: updating/clearing per-token memory on a stale cycle. The last VALID mid (0.50)
    # must stay the baseline across the gap: 0.65 - 0.50 = 0.15 fires. A mutant that books
    # the stale book's would-be mid (0.57) sees only 0.08 and stays silent.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51"))          # mid 0.50
    stale = _book(bid="0.56", ask="0.58")                                            # would-be mid 0.57
    stale.mark_stale()
    gap = mon.evaluate([_pos("t1")], lambda token: stale)
    assert gap.action == NONE                                                        # stale cycle inert
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.64", ask="0.66"))  # mid 0.65
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_first_observation_of_token_never_fires_depth_collapse():
    # Pins: cycle 1 on a huge book is memory-building only (no prev-depth to compare).
    # Kills: seeding prev-depth with a comparable default.
    mon = _monitor()
    state = mon.evaluate([_pos("t1")],
                         lambda token: _book(bid="0.49", ask="0.51",
                                             bid_size="5000", ask_size="5000"))
    assert state.action == NONE
    assert state.triggers == ()


def test_depth_collapse_to_exactly_the_80_percent_threshold_fires():
    # Boundary pair, AT threshold: depth <= prev * (1 - depth_collapse_fraction) with
    # prev >= depth_collapse_min_prev_shares. 1000 -> 200 shares = exactly 80% gone, and
    # prev sits EXACTLY on the 1000-share floor. Kills: '<=' -> '<' on the collapse
    # compare AND '>=' -> '>' on the noise floor. Prices unchanged -> no jump interference.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="100"))  # depth 200
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers


def test_two_positions_on_the_same_token_fire_l5_abnormal_book_once():
    # Pinned contract: check every position's token, DEDUPE tokens. Kills: iterating
    # positions without a seen-set (a shared crossed book double-appends the trigger).
    mon = _monitor()
    book = _book(bid="0.60", ask="0.55")  # crossed
    state = mon.evaluate([_pos("t1"), _pos("t1")], lambda token: book)
    assert state.action == HALT
    assert state.triggers.count(REASON_L5_ABNORMAL_BOOK) == 1


def test_simultaneous_jump_and_collapse_fire_l5_abnormal_book_once():
    # Pinned contract: all three checks fire the SAME trigger string ONCE, not three times.
    # Cycle 2 trips BOTH the jump (0.50 -> 0.65 = 0.15 >= 0.15) and the collapse
    # (1000 -> 200 <= 200). Kills: appending per-condition instead of once per cycle.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # mid 0.50 depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.64", ask="0.66",
                                                           bid_size="100", ask_size="100"))  # mid 0.65 depth 200
    assert state.action == HALT
    assert state.triggers.count(REASON_L5_ABNORMAL_BOOK) == 1


def test_depth_drop_to_just_over_the_80_percent_threshold_does_not_fire():
    # Boundary pair, JUST OVER: 1000 -> 201 shares survives (200 is the line).
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="101"))  # depth 201
    assert state.action == NONE
    assert state.triggers == ()


def test_prev_depth_below_the_noise_floor_full_evaporation_does_not_fire():
    # Noise floor (Fork 2): prev depth 999 < depth_collapse_min_prev_shares 1000, so even a
    # near-total evaporation (999 -> 2, book still validly two-sided) is NOISE, not L5.
    # Kills: dropping the min_prev_shares guard.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="499.5", ask_size="499.5"))  # depth 999
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="1", ask_size="1"))  # depth 2
    assert state.action == NONE
    assert state.triggers == ()


def test_stale_interlude_preserves_prev_depth_so_collapse_across_the_gap_still_fires():
    # Kills: updating prev-depth on a stale cycle. top_of_book() is NOT stale-gated
    # (orderbook.py), so a naive impl could book the stale depth (500) and then see
    # 200 > 500 * 0.2 = 100 -> silent. The preserved baseline 1000 gives 200 <= 200 -> HALT.
    mon = _monitor()
    mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                   bid_size="500", ask_size="500"))   # depth 1000
    stale = _book(bid="0.49", ask="0.51", bid_size="250", ask_size="250")             # depth 500
    stale.mark_stale()
    gap = mon.evaluate([_pos("t1")], lambda token: stale)
    assert gap.action == NONE
    state = mon.evaluate([_pos("t1")], lambda token: _book(bid="0.49", ask="0.51",
                                                           bid_size="100", ask_size="100"))  # depth 200
    assert state.action == HALT
    assert REASON_L5_ABNORMAL_BOOK in state.triggers
