"""Tests for the ERS poll-loop (S3 / POL-5 slice 2).

process_pending wires the chokepoint to the validator: poll PROPOSED intents, RE-FETCH the
live book (never trust the proposed price), run evaluate_intent vs the current portfolio,
record_decision + audit, fold each ACCEPT into the working portfolio (so cross-intent caps
hold), and call the signer SEAM on ACCEPT (a PaperSigner stub in slice 2; the real Rust
signer is S2/POL-4). These pin: ACCEPT path (status + paper order + fold), SKIP/REJECT (no
order), live-book re-fetch (stale -> REJECT), missing book (fail-closed REJECT), and the
cross-intent fold contract.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_accept_records_status_places_paper_order_and_folds_portfolio(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")  # per_trade cap
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")


def test_skip_records_status_and_places_no_order(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, p="0.50"))  # p == price -> no edge
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "SKIPPED" and store.get("i1").decision_reason == "no_edge"
        assert signer.placed == []


def test_re_fetches_the_live_book_and_rejects_a_stale_one(tmp_path):
    # The proposal carries a target_price, but the ERS re-prices off the LIVE book and
    # refuses a stale one -- never trusts the proposed price.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        stale = _book("0.50")
        stale.mark_stale()
        signer = PaperSigner()
        process_pending(store, book_for={"t1": stale}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "book_stale"
        assert signer.placed == []


def test_missing_book_is_fail_closed_reject(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={}.get,  # no book for t1
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "no_book"
        assert signer.placed == []


def test_folds_accepts_so_cross_intent_total_open_holds(tmp_path):
    # Two intents that each individually fit; accepting the first consumes the total_open
    # headroom, so the second must SKIP. Proves the loop threads the portfolio between intents.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, token_id="t1", condition_id="ma", event_id="ea"))
        store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="mb", event_id="eb"))
        start = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition("mz", "ez", "sz", "cz", Decimal("50"), False),))  # $50 at risk -> $10 headroom
        books = {"t1": _book("0.50"), "t2": _book("0.50")}
        process_pending(store, book_for=books.get, portfolio=start, caps=RiskCaps(), signer=PaperSigner())

        assert store.get("i1").status == "ACCEPTED" and store.get("i1").decision_stake_usd == Decimal("10")
        assert store.get("i2").status == "SKIPPED"  # folding i1 left $0 total_open headroom


def test_a_raising_intent_is_isolated_and_the_batch_continues(tmp_path):
    # A malformed intent (here: its live-book fetch raises) must NOT wedge the FIFO queue
    # head -- it is failed closed to REJECT(internal_error) + audited, and the rest process.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("bad", **dict(_P, token_id="boom"))
        store.propose_trade("good", **dict(_P, token_id="t1", condition_id="mb", event_id="eb"))

        def book_for(token_id):
            if token_id == "boom":
                raise RuntimeError("rpc blew up")
            return _book("0.50")

        signer = PaperSigner()
        process_pending(store, book_for=book_for, portfolio=Portfolio(nav=Decimal("300")),
                        caps=RiskCaps(), signer=signer)

        assert store.get("bad").status == "REJECTED" and store.get("bad").decision_reason == "internal_error"
        assert store.get("good").status == "ACCEPTED"
        assert [o["intent_id"] for o in signer.placed] == ["good"]
