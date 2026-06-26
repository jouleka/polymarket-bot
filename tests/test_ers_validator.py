"""Tests for the ERS risk-engine validator (S3 / POL-5 slice 1).

evaluate_intent is the deterministic heart of "the ERS disposes": it re-prices off the
live book, sizes 1/4-Kelly on the EXECUTABLE price, clamps by every S0 cap, and FAILS
CLOSED (any ambiguity -> REJECT/SKIP + reason code). Pure function; tested against the
DECISIONS-S0 §4 envelope with synthetic intents + books. One cap per test.
"""

from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.validator import (
    Decision,
    OpenPosition,
    Portfolio,
    TradeIntent,
    evaluate_intent,
)
from polybot.ingestion.orderbook import LocalBook


def _book(ask, ask_size="1000", bid="0.01", bid_size="1000"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book


def _intent(p, max_price, *, condition="m1", event="e1", source="s1", cluster="c1",
            matrix_cold=True, size="100"):
    return TradeIntent(token_id="t1", condition_id=condition, event_id=event,
                       resolution_source=source, cluster_id=cluster, p=Decimal(p),
                       max_price=Decimal(max_price), size_usd_suggestion=Decimal(size),
                       matrix_cold=matrix_cold)


def _portfolio(nav="300", positions=()):
    return Portfolio(nav=Decimal(nav), positions=tuple(positions))


def _pos(risk, *, condition="mX", event="eX", source="sX", cluster="cX", matrix_cold=False):
    return OpenPosition(condition_id=condition, event_id=event, resolution_source=source,
                        cluster_id=cluster, worst_case_risk=Decimal(risk), matrix_cold=matrix_cold)


# --- Batch 0: fail-closed input-domain guards (must REJECT/SKIP, never crash or oversize) ---

def test_rejects_a_degenerate_dollar_price_without_dividing_by_zero():
    # A $1.00 ask is a valid book state (not crossed/stale, midpoint 0.505) but degenerate:
    # it has no tradeable edge and would divide-by-zero at p/(1-price). Must REJECT, even
    # when max_price allows it -- never raise.
    book = _book("1.00", bid="0.01")
    d = evaluate_intent(_intent("0.99", "1.00"), book, _portfolio(), RiskCaps())
    assert d.verdict == "REJECT" and d.reason == "degenerate_price"


def test_rejects_an_impossible_probability():
    # p outside (0,1) is garbage/hallucination -- the ERS must refuse it, NOT size maximally.
    book = _book("0.50")
    for p in ("1.0", "1.5", "0", "-0.1"):
        d = evaluate_intent(_intent(p, "0.60"), book, _portfolio(), RiskCaps())
        assert d.verdict == "REJECT" and d.reason == "bad_probability", p


def test_rejects_an_out_of_range_calibration_score():
    book = _book("0.50")
    for c in ("-0.1", "1.5"):
        d = evaluate_intent(_intent("0.9", "0.60"), book, _portfolio(), RiskCaps(),
                            calib_score=Decimal(c))
        assert d.verdict == "REJECT" and d.reason == "bad_calibration", c


def test_zero_calibration_is_valid_and_skips_via_floor():
    # calib_score = 0 is VALID (cold-start paper-only) -> stake 0 -> SKIP, not REJECT.
    book = _book("0.50")
    d = evaluate_intent(_intent("0.9", "0.60"), book, _portfolio(), RiskCaps(), calib_score=Decimal(0))
    assert d.verdict == "SKIP" and d.reason == "below_min_floor"


def test_negative_nav_fails_closed_to_skip():
    book = _book("0.50")
    d = evaluate_intent(_intent("0.9", "0.60"), book, Portfolio(nav=Decimal("-5")), RiskCaps())
    assert d.verdict == "SKIP" and d.reason == "below_min_floor"


def test_portfolio_already_over_a_cap_skips_never_oversizes():
    # negative headroom (a cap already breached) must drive SKIP, never a negative/oversized ACCEPT.
    book = _book("0.50")
    pos = _pos("70", condition="ma", event="ea", source="sa")  # total already > $60
    d = evaluate_intent(_intent("0.9", "0.60", condition="m1", event="e1", source="s1", matrix_cold=False),
                        book, _portfolio(positions=[pos]), RiskCaps())
    assert d.verdict == "SKIP" and d.reason == "below_min_floor"


# --- Batch 1: re-price, edge, 1/4-Kelly sizing, per-trade clamp, floor ---

def test_rejects_a_stale_book():
    book = _book("0.50")
    book.mark_stale()
    d = evaluate_intent(_intent("0.7", "0.60"), book, _portfolio(), RiskCaps())
    assert d.verdict == "REJECT" and d.reason == "book_stale"


def test_skips_when_executable_price_is_above_the_limit():
    book = _book("0.70")
    d = evaluate_intent(_intent("0.9", "0.60"), book, _portfolio(), RiskCaps())
    assert d.verdict == "SKIP" and d.reason == "price_above_limit"


def test_skips_when_there_is_no_edge():
    book = _book("0.50")
    d = evaluate_intent(_intent("0.50", "0.60"), book, _portfolio(), RiskCaps())  # p == price
    assert d.verdict == "SKIP" and d.reason == "no_edge"


def test_accepts_and_clamps_to_per_trade_cap():
    # p=0.9, ask=0.50 -> f=(0.9-0.5)/0.5=0.8; 1/4-Kelly = 0.25*0.8*300 = 60 -> clamped to per_trade $12.
    book = _book("0.50")
    d = evaluate_intent(_intent("0.9", "0.60"), book, _portfolio(), RiskCaps())
    assert d.verdict == "ACCEPT"
    assert d.price_exec == Decimal("0.50")
    assert d.stake_usd == Decimal("12")
    assert d.reason == "per_trade_cap"


def test_accepts_with_kelly_binding_when_below_all_caps():
    # p=0.55, ask=0.50 -> f=0.1; 1/4-Kelly = 0.25*0.1*300 = 7.5, under every cap -> Kelly binds.
    book = _book("0.50")
    d = evaluate_intent(_intent("0.55", "0.60"), book, _portfolio(), RiskCaps())
    assert d.verdict == "ACCEPT"
    assert d.stake_usd == Decimal("7.5")
    assert d.reason == "kelly"


def test_skips_below_min_floor_without_rounding_up():
    # tiny edge -> 1/4-Kelly below the $5 floor -> SKIP, never round up to meet a cap.
    # p=0.52, ask=0.50 -> f=0.04; 0.25*0.04*300 = 3.0 < $5 floor.
    book = _book("0.50")
    d = evaluate_intent(_intent("0.52", "0.60"), book, _portfolio(), RiskCaps())
    assert d.verdict == "SKIP" and d.reason == "below_min_floor"


# --- Batch 2: portfolio dollar caps + size-suggestion + liquidity ---

def test_clamps_to_per_market_headroom():
    # existing $12 in market m1 -> per_market headroom $6; intent into m1 clamps to $6.
    book = _book("0.50")
    pos = _pos("12", condition="m1", event="eX", source="sX")
    d = evaluate_intent(_intent("0.9", "0.60", condition="m1", event="e1", source="s1", matrix_cold=False),
                        book, _portfolio(positions=[pos]), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("6") and d.reason == "per_market_cap"


def test_clamps_to_per_event_union_headroom():
    # existing $16 in event e1 (different market) -> per_event headroom $8.
    book = _book("0.50")
    pos = _pos("16", condition="m2", event="e1", source="sX")
    d = evaluate_intent(_intent("0.9", "0.60", condition="m1", event="e1", source="s1", matrix_cold=False),
                        book, _portfolio(positions=[pos]), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("8") and d.reason == "per_event_cap"


def test_clamps_to_per_source_headroom():
    # existing $23 from source s1 (different market+event) -> per_source headroom $7.
    book = _book("0.50")
    pos = _pos("23", condition="m2", event="e2", source="s1")
    d = evaluate_intent(_intent("0.9", "0.60", condition="m1", event="e1", source="s1", matrix_cold=False),
                        book, _portfolio(positions=[pos]), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("7") and d.reason == "per_source_cap"


def test_clamps_to_total_open_headroom():
    # 3 distinct positions summing to $54 -> total_open headroom $6; intent has fresh keys.
    book = _book("0.50")
    positions = [_pos("18", condition="ma", event="ea", source="sa"),
                 _pos("18", condition="mb", event="eb", source="sb"),
                 _pos("18", condition="mc", event="ec", source="sc")]
    d = evaluate_intent(_intent("0.9", "0.60", condition="m1", event="e1", source="s1", matrix_cold=False),
                        book, _portfolio(positions=positions), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("6") and d.reason == "total_open_cap"


def test_never_sizes_above_the_size_suggestion():
    # Hermes requests only $9 though Kelly+caps would allow $12 -> we never exceed the request.
    book = _book("0.50")
    d = evaluate_intent(_intent("0.9", "0.60", size="9", matrix_cold=False), book, _portfolio(), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("9") and d.reason == "size_suggestion"


def test_clamps_to_liquidity_touch_depth():
    # ask depth 200 shares @ $0.50 -> touch notional $100; 10% liquidity cap = $10.
    book = _book("0.50", ask_size="200")
    d = evaluate_intent(_intent("0.9", "0.60", matrix_cold=False), book, _portfolio(), RiskCaps())
    assert d.verdict == "ACCEPT" and d.stake_usd == Decimal("10") and d.reason == "liquidity_cap"


# --- Batch 3: concurrency + the fail-closed matrix-cold sub-cap ---

def test_rejects_when_max_concurrent_reached():
    # 4 open positions (the cap) -> a 5th is rejected regardless of size/edge.
    book = _book("0.50")
    positions = [_pos("5", condition=f"m{i}", event=f"e{i}", source=f"s{i}") for i in range(4)]
    d = evaluate_intent(_intent("0.9", "0.60", condition="m9", event="e9", source="s9", matrix_cold=False),
                        book, _portfolio(positions=positions), RiskCaps())
    assert d.verdict == "REJECT" and d.reason == "max_concurrent"


def test_rejects_matrix_cold_intent_at_the_matrix_cold_subcap():
    # 3 matrix-cold positions (the sub-cap) -> a 4th MATRIX-COLD intent is rejected even
    # though max_concurrent (4) is not yet hit -- the fail-closed unknown-corr=+1 gate.
    book = _book("0.50")
    positions = [_pos("5", condition=f"m{i}", event=f"e{i}", source=f"s{i}", matrix_cold=True)
                 for i in range(3)]
    d = evaluate_intent(_intent("0.9", "0.60", condition="m9", event="e9", source="s9", matrix_cold=True),
                        book, _portfolio(positions=positions), RiskCaps())
    assert d.verdict == "REJECT" and d.reason == "matrix_cold_concurrent"


def test_non_cold_intent_not_blocked_by_the_matrix_cold_subcap():
    # 3 matrix-cold positions but a KNOWN-correlation intent -> the sub-cap doesn't apply;
    # only max_concurrent (3 < 4) governs, so it is accepted.
    book = _book("0.50")
    positions = [_pos("5", condition=f"m{i}", event=f"e{i}", source=f"s{i}", matrix_cold=True)
                 for i in range(3)]
    d = evaluate_intent(_intent("0.9", "0.60", condition="m9", event="e9", source="s9", matrix_cold=False),
                        book, _portfolio(positions=positions), RiskCaps())
    assert d.verdict == "ACCEPT"
