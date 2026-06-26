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
