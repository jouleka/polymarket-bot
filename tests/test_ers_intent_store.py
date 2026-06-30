"""Tests for the ERS chokepoint store (S3 / POL-5 slice 2).

The entire safety model in one sentence: Hermes gets ONLY ``propose_trade(...)``, which
does nothing but INSERT a ``PROPOSED`` row -- it can never set a non-PROPOSED status, sign,
or submit. The deterministic ERS (not Hermes) polls those rows, runs the validator, and
``record_decision`` transitions the status + appends an immutable audit row. These tests
pin: INSERT-only + idempotency + the no-status-control invariant, the status lifecycle,
audit append, and restart persistence.
"""

import inspect
from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.ers.validator import Decision

_PROPOSAL = dict(
    token_id="t1", condition_id="0xabc", event_id="e1", side="BUY",
    target_price="0.55", max_price="0.60", size_usd_suggestion="10",
    p="0.7", p_confidence="0.6", resolution_summary="resolves YES if X",
    thesis="X is likely", citations=("https://primary/1", "https://primary/2"),
)


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_propose_trade_inserts_a_proposed_row(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("intent-1", **_PROPOSAL)

        pend = store.pending()
        assert [i.intent_id for i in pend] == ["intent-1"]
        assert pend[0].status == "PROPOSED"
        assert pend[0].p == Decimal("0.7")
        assert pend[0].max_price == Decimal("0.60")
        assert pend[0].citations == ("https://primary/1", "https://primary/2")
        assert pend[0].created_at > 0


def test_propose_trade_is_idempotent_on_intent_id(tmp_path):
    # A re-proposing Hermes (retry / re-run) must not inflate the queue.
    with _store(str(tmp_path / "i.db")) as store:
        assert store.propose_trade("dup", **_PROPOSAL) is True
        assert store.propose_trade("dup", **_PROPOSAL) is False  # no-op
        assert len(store.pending()) == 1


def test_propose_trade_is_insert_only_with_no_status_control(tmp_path):
    # The chokepoint invariant: propose_trade has NO status parameter, so Hermes can
    # never create an ACCEPTED row -- every row it writes is PROPOSED.
    with _store(str(tmp_path / "i.db")) as store:
        assert "status" not in inspect.signature(store.propose_trade).parameters
        store.propose_trade("a", **_PROPOSAL)
        store.propose_trade("b", **_PROPOSAL)
        assert all(i.status == "PROPOSED" for i in store.pending())


def test_pending_returns_intents_in_insertion_order(tmp_path):
    # FIFO by insertion (rowid) -- restart-stable, not dependent on a per-process clock.
    with _store(str(tmp_path / "i.db")) as store:
        for intent_id in ("a", "b", "c"):
            store.propose_trade(intent_id, **_PROPOSAL)
        assert [i.intent_id for i in store.pending()] == ["a", "b", "c"]


def test_record_decision_transitions_status_and_stores_the_decision(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_PROPOSAL)
        store.record_decision("i1", Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly"))

        assert store.pending() == []  # no longer PROPOSED
        decided = store.get("i1")
        assert decided.status == "ACCEPTED"
        assert decided.decision_stake_usd == Decimal("8")
        assert decided.decision_price_exec == Decimal("0.55")
        assert decided.decision_reason == "kelly"
        assert decided.decided_at is not None


def test_reject_and_skip_map_to_their_statuses(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("r", **_PROPOSAL)
        store.propose_trade("s", **_PROPOSAL)
        store.record_decision("r", Decision("REJECT", None, Decimal("0.55"), "book_stale"))
        store.record_decision("s", Decision("SKIP", None, Decimal("0.55"), "no_edge"))
        assert store.get("r").status == "REJECTED"
        assert store.get("s").status == "SKIPPED"


def test_each_decision_appends_an_immutable_audit_row(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_PROPOSAL)
        store.record_decision("i1", Decision("REJECT", None, Decimal("0.55"), "book_stale"))

        audit = store.audit_log()
        assert len(audit) == 1
        assert audit[0]["intent_id"] == "i1"
        assert audit[0]["verdict"] == "REJECT"
        assert audit[0]["reason"] == "book_stale"
        assert audit[0]["at"] > 0


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "i.db")
    with _store(path) as store:
        store.propose_trade("i1", **_PROPOSAL)
        store.record_decision("i1", Decision("ACCEPT", Decimal("8"), Decimal("0.55"), "kelly"))
        store.propose_trade("i2", **_PROPOSAL)

    with _store(path) as reopened:  # process restart
        assert [i.intent_id for i in reopened.pending()] == ["i2"]
        assert reopened.get("i1").status == "ACCEPTED"
        assert len(reopened.audit_log()) == 1


# --- S4.1: op/kill append-only audit table (POL-6) -------------------------------------------
from decimal import Decimal  # noqa: F401 (harmless if already imported at top of file)
from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _op_store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_op_event_appends_ordered_rows(tmp_path):
    with _op_store(str(tmp_path / "i.db")) as store:
        store.record_op_event(kind="state_change", reason="unclean_restart", detail="boot")
        store.record_op_event(kind="kill", reason="l8_kill")  # detail defaults to ""
        store.record_op_event(kind="flatten", reason="op_flatten", detail="2 positions")

        rows = store.op_audit_log()
        assert [r["kind"] for r in rows] == ["state_change", "kill", "flatten"]
        assert [r["reason"] for r in rows] == ["unclean_restart", "l8_kill", "op_flatten"]
        assert rows[0]["detail"] == "boot" and rows[1]["detail"] == ""
        # Each row carries the shared monotonic stamp, strictly increasing in id-order.
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 3


def test_op_audit_log_persists_across_restart(tmp_path):
    db = str(tmp_path / "i.db")
    with _op_store(db) as store:
        store.record_op_event(kind="pause", reason="l8_paused", detail="operator")
    # Reopen the SAME path with a FRESH stamper -- the row must survive (append-only + committed).
    with _op_store(db) as reopened:
        rows = reopened.op_audit_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "pause" and rows[0]["reason"] == "l8_paused"
        # A new event after restart appends AFTER the persisted one (id ordering, not stamp clock).
        reopened.record_op_event(kind="state_change", reason="unclean_restart")
        rows = reopened.op_audit_log()
        assert [r["kind"] for r in rows] == ["pause", "state_change"]


# --- S4.5a: durable fills ledger (POL-6) -----------------------------------------------------
from decimal import Decimal  # noqa: F401 (harmless if already imported at top of file)
from polybot.core.clock import MonotonicStamper  # noqa: F401
from polybot.ers.intent_store import IntentStore  # noqa: F401


def _fills_store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_fill_appends_ordered_decimal_exact_rows(tmp_path):
    # The fills ledger is the durable INTERNAL leg of S4.5 reconciliation: append-only, ordered by
    # fill_id, every Decimal round-tripped EXACTLY (stored as string, read back as Decimal), and each
    # row carries the shared monotonic stamp. Mirrors record_op_event / op_audit_log.
    with _fills_store(str(tmp_path / "i.db")) as store:
        store.record_fill(intent_id="i1", token_id="t1", condition_id="0xabc", event_id="e1",
                          side="BUY", shares=Decimal("24"), price_exec=Decimal("0.50"),
                          worst_case_risk=Decimal("12"))
        store.record_fill(intent_id="i2", token_id="t2", condition_id="0xdef", event_id="e2",
                          side="BUY", shares=Decimal("13.333333"), price_exec=Decimal("0.45"),
                          worst_case_risk=Decimal("6"))

        rows = store.fills_log()
        assert [r["intent_id"] for r in rows] == ["i1", "i2"]   # ORDER BY fill_id
        assert [r["token_id"] for r in rows] == ["t1", "t2"]
        assert rows[0]["condition_id"] == "0xabc" and rows[0]["event_id"] == "e1"
        assert rows[0]["side"] == "BUY"
        # Decimal-exact round-trip (NOT float):
        assert rows[0]["shares"] == Decimal("24") and isinstance(rows[0]["shares"], Decimal)
        assert rows[0]["price_exec"] == Decimal("0.50")
        assert rows[0]["worst_case_risk"] == Decimal("12")
        assert rows[1]["shares"] == Decimal("13.333333")
        # Each row carries the shared monotonic stamp, strictly increasing in id-order.
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 2 and ats[0] > 0


def test_fills_log_persists_across_restart(tmp_path):
    # Append-only + committed: a fill survives a process restart and a fresh stamper, and a new fill
    # appends AFTER the persisted one (id ordering, not the per-process stamp clock).
    db = str(tmp_path / "i.db")
    with _fills_store(db) as store:
        store.record_fill(intent_id="i1", token_id="t1", condition_id="0xabc", event_id="e1",
                          side="BUY", shares=Decimal("24"), price_exec=Decimal("0.50"),
                          worst_case_risk=Decimal("12"))
    with _fills_store(db) as reopened:
        rows = reopened.fills_log()
        assert len(rows) == 1 and rows[0]["token_id"] == "t1"
        reopened.record_fill(intent_id="i2", token_id="t2", condition_id="0xdef", event_id="e2",
                             side="BUY", shares=Decimal("4"), price_exec=Decimal("0.50"),
                             worst_case_risk=Decimal("2"))
        assert [r["intent_id"] for r in reopened.fills_log()] == ["i1", "i2"]
