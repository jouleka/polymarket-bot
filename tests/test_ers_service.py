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
from polybot.ers.breaker import DrawdownBreaker
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import ClusterView, OpenPosition, Portfolio
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


# --- slice-3: L7 breaker gating + co-move ClusterView wiring + mark-field fold ---------------

class _FakeClusterModel:
    """Returns a fixed ClusterView regardless of token_ids -- pins that the service applies the
    model's verdict (ClusterModel.view itself is covered in test_ers_comove)."""

    def __init__(self, view):
        self._view = view

    def view(self, token_ids):
        return self._view


def _open(token, entry, risk, *, cluster="cz"):
    return OpenPosition("m", "e", "s", cluster, Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry))


def test_l7_freeze_rejects_new_intents_without_placing(tmp_path):
    # an open position marked into the freeze band (drawdown ~$19.20) -> the breaker freezes adds,
    # so an otherwise-acceptable PROPOSED intent is REJECTED(l7_freeze) and nothing is placed.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        caps = RiskCaps()
        portfolio = Portfolio(nav=Decimal("300"), positions=(_open("P", "0.50", "24"),))
        books = {"t1": _book("0.50"), "P": _book("0.12", bid="0.08")}  # P mid 0.10 -> drawdown 19.2
        signer = PaperSigner()
        process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps, signer=signer,
                        breaker=DrawdownBreaker(caps, clock=lambda: 0))

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "l7_freeze"
        assert signer.placed == []


def test_l7_flatten_signals_exit_and_blocks_new_intents(tmp_path):
    # two positions marked to a >$30 portfolio drawdown -> FLATTEN: the seam is signalled to exit
    # and new intents are REJECTED(l7_flatten).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        caps = RiskCaps()
        positions = (_open("P1", "0.50", "18"), _open("P2", "0.50", "18"))
        portfolio = Portfolio(nav=Decimal("300"), positions=positions)
        books = {"t1": _book("0.50"), "P1": _book("0.06", bid="0.04"), "P2": _book("0.06", bid="0.04")}
        signer = PaperSigner()
        process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps, signer=signer,
                        breaker=DrawdownBreaker(caps, clock=lambda: 0))

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "l7_flatten"
        assert signer.placed == []
        assert signer.flattened  # the exit was signalled through the seam


def test_accept_folds_the_l7_mark_fields(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)  # token t1
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=PaperSigner())
        pos = final.positions[0]
        assert pos.token_id == "t1"
        assert pos.entry_price == Decimal("0.50")  # = the executable price the ERS re-priced at
        assert pos.frozen is False


def test_warm_cluster_model_applies_the_per_cluster_cap(tmp_path):
    # a warm co-move verdict (rho=1) + an existing $4 position in the same cluster (cluster_id =
    # event_id placeholder "e1") -> cluster_cap $12 - $4 = $8 binds, and the new position folds
    # matrix_cold=False (warm leaves the cold count gate).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, token_id="t1", condition_id="m1", event_id="e1"))
        existing = OpenPosition("mx", "e1", "sx", "e1", Decimal("4"), False,
                                token_id="P", entry_price=Decimal("0.50"))
        portfolio = Portfolio(nav=Decimal("300"), positions=(existing,))
        cm = _FakeClusterModel(ClusterView(warm=True, rho=Decimal("1")))
        final = process_pending(store, book_for={"t1": _book("0.50")}.get, portfolio=portfolio,
                                caps=RiskCaps(), signer=PaperSigner(), cluster_model=cm)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("8")
        assert store.get("i1").decision_reason == "per_cluster_cap"
        assert final.positions[-1].matrix_cold is False
