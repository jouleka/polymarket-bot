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
