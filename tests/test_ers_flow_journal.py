"""Tests for the S4.7a flow journal (DESIGN-S4.7-BREAKERS.md §4/§9 sub-slice a).

The durable dual-stamped flow_journal (monotonic ``at`` for cross-table ordering + injected
wall-clock ``wall_at`` for restart-surviving windows) + the fill_sink-shaped accept recorder +
compose_sinks fan-out + the pure rolling-window helpers (accepts_in_window / pending_in_window).
Window math uses wall_at ONLY -- stored monotonic stamps are not comparable across restarts
(the S4.5 lesson, re-pinned by DESIGN §6.8).
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_flow_journal_round_trips_decimal_amount_float_wall_at_in_flow_id_order(tmp_path):
    # Kills: storing amount as float / returning str (Decimal reconstruction dropped);
    # Kills: ORDER BY wall_at (or at) instead of flow_id -- the wall stamps here are DESCENDING.
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=200.0)
        store.record_flow_event(kind="realized", token_id="t2", amount=Decimal("-3.50"), wall_at=100.0)
        rows = store.flow_log()
        assert [(r["kind"], r["token_id"], r["amount"]) for r in rows] == [
            ("accept", "t1", Decimal("12")), ("realized", "t2", Decimal("-3.50"))]
        assert all(isinstance(r["amount"], Decimal) for r in rows)
        assert [r["wall_at"] for r in rows] == [200.0, 100.0]  # insertion order, NOT wall order
        assert all(isinstance(r["wall_at"], float) for r in rows)


def test_flow_journal_at_comes_from_the_one_shared_monotonic_stamper(tmp_path):
    # Kills: a per-table clock or wall_at reuse for ``at`` -- a flow row's ``at`` must interleave
    # with op_audit's on the ONE shared stamper (total ordering across every table).
    with _store(str(tmp_path / "i.db")) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=1.0)
        store.record_op_event(kind="state_change", reason="r", detail="d")
        store.record_flow_event(kind="realized", token_id="t1", amount=Decimal("-2"), wall_at=2.0)
        first_at, second_at = [r["at"] for r in store.flow_log()]
        op_at = store.op_audit_log()[0]["at"]
        assert isinstance(first_at, int) and isinstance(second_at, int)
        assert first_at < op_at < second_at


def test_flow_journal_survives_close_and_reopen(tmp_path):
    # Kills: an in-memory journal / a missing per-write commit -- restart-surviving windows
    # (DESIGN §2 durability) require the row to be durable across close-and-reopen.
    path = str(tmp_path / "i.db")
    with _store(path) as store:
        store.record_flow_event(kind="accept", token_id="t1", amount=Decimal("12"), wall_at=500.0)
    with _store(path) as reopened:
        rows = reopened.flow_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "accept" and rows[0]["token_id"] == "t1"
        assert rows[0]["amount"] == Decimal("12") and rows[0]["wall_at"] == 500.0


# --- S4.7a: make_flow_recorder (ers/flow.py -- the fill_sink-shaped accept recorder) ----------
from polybot.ers.flow import make_flow_recorder
from polybot.ers.validator import OpenPosition


def test_make_flow_recorder_records_accept_with_worst_case_risk_and_injected_wall_clock(tmp_path):
    # Kills: recording the wrong kind / sourcing amount from anything but position.worst_case_risk;
    # Kills: calling time.time() instead of the injected 0-arg wall_clock (wall_at must be 777.5).
    with _store(str(tmp_path / "i.db")) as store:
        recorder = make_flow_recorder(store, wall_clock=lambda: 777.5)
        position = OpenPosition(condition_id="m1", event_id="e1", resolution_source="s1",
                                cluster_id="c1", worst_case_risk=Decimal("8"), matrix_cold=False,
                                token_id="t9", entry_price=Decimal("0.50"), frozen=False)
        recorder(None, None, position)  # intent/decision unused: the recorder reads ONLY the position
        rows = store.flow_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "accept" and rows[0]["token_id"] == "t9"
        assert rows[0]["amount"] == Decimal("8") and rows[0]["wall_at"] == 777.5


# --- S4.7a: compose_sinks (one fill_sink fanning out to many; NO service.py change) -----------
from polybot.ers.caps import RiskCaps
from polybot.ers.flow import compose_sinks
from polybot.ers.service import PaperSigner, make_fill_sink, process_pending
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def test_compose_sinks_calls_each_sink_exactly_once_in_order_with_the_same_args():
    # Kills: reversed fan-out order; a sink invoked twice or skipped; args not threaded through.
    calls = []

    def _first_sink(intent, decision, position):
        calls.append(("first", intent, decision, position))

    def _second_sink(intent, decision, position):
        calls.append(("second", intent, decision, position))

    composed = compose_sinks(_first_sink, _second_sink)
    composed("I", "D", "P")
    assert calls == [("first", "I", "D", "P"), ("second", "I", "D", "P")]


def test_composed_sink_writes_both_a_fills_row_and_a_flow_row_on_an_accept(tmp_path):
    # Kills: the composite not being fill_sink-shaped end-to-end -- one ACCEPT through the
    # UNCHANGED process_pending fill_sink seam must land BOTH durable legs: the S4.5 fill
    # AND the S4.7 accept-flow row (amount == the folded worst_case_risk == stake $12).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        sink = compose_sinks(make_fill_sink(store),
                             make_flow_recorder(store, wall_clock=lambda: 1000.0))
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, fill_sink=sink)
        assert store.get("i1").status == "ACCEPTED"
        fills = store.fills_log()
        assert len(fills) == 1 and fills[0]["worst_case_risk"] == Decimal("12")
        flow = store.flow_log()
        assert len(flow) == 1
        assert flow[0]["kind"] == "accept" and flow[0]["token_id"] == "t1"
        assert flow[0]["amount"] == Decimal("12") and flow[0]["wall_at"] == 1000.0
