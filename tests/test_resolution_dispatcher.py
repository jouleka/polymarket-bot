"""POL-15 durable terminal delivery dispatcher."""

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.errors import (
    IntegrityHalted,
    RecoveryRequired,
    SettlementConflict,
)
from polybot.resolution.models import (
    DisputeState,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionStore


def _terminal(condition_byte):
    return TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + condition_byte * 32,
            ("101", "202"), "politics",
        ),
        payout=PayoutVector((3, 1), 4),
        dispute=DisputeState.CLEAR,
        block_number=200,
        block_hash="0x" + "33" * 32,
        adapter_address="0x" + "44" * 20,
        question_id="0x" + "55" * 32,
        audit_event_ids=(
            "199:1:" + "0x" + "66" * 32 + ":CONDITION_RESOLUTION",
        ),
        provider_ids=("archive-a", "archive-b"),
    )


class _Target:
    def __init__(self, role, events):
        self._role = role
        self._events = events

    def apply_terminal(self, terminal):
        self._events.append(("APPLY", self._role, terminal.terminal_id))
        return 1


class _IdempotentTarget:
    def __init__(self):
        self._receipts = set()
        self.apply_results = []

    def apply_terminal(self, terminal):
        changed = int(terminal.terminal_id not in self._receipts)
        self._receipts.add(terminal.terminal_id)
        self.apply_results.append(changed)
        return changed


class _TransientTarget:
    def __init__(self, role, events):
        self._role = role
        self._events = events
        self.fail = True

    def apply_terminal(self, terminal):
        self._events.append((self._role, terminal.terminal_id))
        if self.fail:
            raise RuntimeError("temporary target outage")
        return 1


class _ConflictTarget:
    def __init__(self, message="target receipt contradicts terminal"):
        self.calls = []
        self._message = message

    def apply_terminal(self, terminal):
        self.calls.append(terminal.terminal_id)
        raise SettlementConflict(self._message)


def test_dispatcher_applies_oldest_role_then_acknowledges(tmp_path):
    first = _terminal("81")
    second = _terminal("82")
    events = []

    with ResolutionStore(
        str(tmp_path / "resolution.db"), MonotonicStamper()
    ) as store:
        store.accept_terminal(first)
        store.accept_terminal(second)
        acknowledge = store.acknowledge

        def record_ack(sequence, terminal_id, role):
            events.append(("ACK", role, terminal_id))
            return acknowledge(sequence, terminal_id, role)

        store.acknowledge = record_ack
        dispatcher = ResolutionDispatcher(
            store,
            _Target("FORECAST", events),
            _Target("MAKER", events),
            _Target("SHADOW", events),
        )

        assert dispatcher.drain(4) == 4
        assert events == [
            ("APPLY", "FORECAST", first.terminal_id),
            ("ACK", "FORECAST", first.terminal_id),
            ("APPLY", "MAKER", first.terminal_id),
            ("ACK", "MAKER", first.terminal_id),
            ("APPLY", "SHADOW", first.terminal_id),
            ("ACK", "SHADOW", first.terminal_id),
            ("APPLY", "FORECAST", second.terminal_id),
            ("ACK", "FORECAST", second.terminal_id),
        ]
        assert [
            (record.sequence, record.role)
            for record in store.pending_outbox(10)
        ] == [(5, "MAKER"), (6, "SHADOW")]


def test_dispatch_retry_after_target_commit_is_idempotent(tmp_path):
    terminal = _terminal("83")
    forecast = _IdempotentTarget()
    unused_events = []

    with ResolutionStore(
        str(tmp_path / "resolution.db"), MonotonicStamper()
    ) as store:
        store.accept_terminal(terminal)
        dispatcher = ResolutionDispatcher(
            store,
            forecast,
            _Target("MAKER", unused_events),
            _Target("SHADOW", unused_events),
        )

        def crash_after_apply(record, changed):
            assert record.role == "FORECAST"
            assert changed == 1
            raise RuntimeError("crash after target commit")

        dispatcher._after_apply = crash_after_apply
        with pytest.raises(RuntimeError, match="after target commit"):
            dispatcher.drain(1)
        assert forecast.apply_results == [1]
        assert [(record.sequence, record.role)
                for record in store.pending_outbox(10)] == [
            (1, "FORECAST"), (2, "MAKER"), (3, "SHADOW"),
        ]

        dispatcher._after_apply = lambda record, changed: None
        assert dispatcher.drain(1) == 1
        assert forecast.apply_results == [1, 0]
        assert [(record.sequence, record.role)
                for record in store.pending_outbox(10)] == [
            (2, "MAKER"), (3, "SHADOW"),
        ]


def test_transient_target_failure_stops_without_ack_or_overtake(tmp_path):
    first = _terminal("84")
    second = _terminal("85")
    events = []
    forecast = _TransientTarget("FORECAST", events)

    with ResolutionStore(
        str(tmp_path / "resolution.db"), MonotonicStamper()
    ) as store:
        store.accept_terminal(first)
        store.accept_terminal(second)
        dispatcher = ResolutionDispatcher(
            store,
            forecast,
            _Target("MAKER", events),
            _Target("SHADOW", events),
        )

        with pytest.raises(RuntimeError, match="temporary target outage"):
            dispatcher.drain(6)
        assert events == [("FORECAST", first.terminal_id)]
        assert [(record.sequence, record.role)
                for record in store.pending_outbox(10)] == [
            (1, "FORECAST"), (2, "MAKER"), (3, "SHADOW"),
            (4, "FORECAST"), (5, "MAKER"), (6, "SHADOW"),
        ]

        forecast.fail = False
        assert dispatcher.drain(1) == 1
        assert events == [
            ("FORECAST", first.terminal_id),
            ("FORECAST", first.terminal_id),
        ]


def test_target_settlement_conflict_persistently_halts_central_store(tmp_path):
    path = str(tmp_path / "resolution.db")
    terminal = _terminal("86")
    later = _terminal("87")
    forecast = _ConflictTarget()
    unused_events = []

    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        dispatcher = ResolutionDispatcher(
            store,
            forecast,
            _Target("MAKER", unused_events),
            _Target("SHADOW", unused_events),
        )

        with pytest.raises(SettlementConflict, match="target receipt"):
            dispatcher.drain(1)
        assert forecast.calls == [terminal.terminal_id]
        assert [(record.sequence, record.role)
                for record in store.pending_outbox(10)] == [
            (1, "FORECAST"), (2, "MAKER"), (3, "SHADOW"),
        ]
        with pytest.raises(IntegrityHalted, match="target receipt"):
            store.require_healthy()
        with pytest.raises(IntegrityHalted):
            store.accept_terminal(later)
        with pytest.raises(IntegrityHalted):
            dispatcher.drain(1)
        assert forecast.calls == [terminal.terminal_id]

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        reopened_dispatcher = ResolutionDispatcher(
            reopened,
            forecast,
            _Target("MAKER", unused_events),
            _Target("SHADOW", unused_events),
        )
        with pytest.raises(IntegrityHalted, match="target receipt"):
            reopened_dispatcher.drain(1)
        assert forecast.calls == [terminal.terminal_id]


def test_dispatcher_refuses_reopened_or_partially_recovered_store(tmp_path):
    path = str(tmp_path / "resolution.db")
    first = _terminal("88")
    second = _terminal("89")
    events = []

    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(first)
        store.accept_terminal(second)

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        assert reopened.recovery_required is True
        dispatcher = ResolutionDispatcher(
            reopened,
            _Target("FORECAST", events),
            _Target("MAKER", events),
            _Target("SHADOW", events),
        )

        with pytest.raises(RecoveryRequired, match="recovery"):
            dispatcher.drain(6)
        assert events == []

        pending_ids = tuple(
            terminal.terminal_id for terminal in reopened.pending_terminals()
        )
        assert pending_ids == (first.terminal_id, second.terminal_id)
        with pytest.raises(RecoveryRequired, match="exact current"):
            reopened._complete_recovery((first.terminal_id,))
        assert reopened.recovery_required is True
        with pytest.raises(RecoveryRequired, match="recovery"):
            dispatcher.drain(6)
        assert events == []

        reopened._complete_recovery(pending_ids)
        assert reopened.recovery_required is False
        assert dispatcher.drain(1) == 1
        assert events == [("APPLY", "FORECAST", first.terminal_id)]


@pytest.mark.parametrize("message", ["", "   "])
def test_message_less_target_conflict_still_persists_halt(tmp_path, message):
    path = str(tmp_path / "resolution.db")
    terminal = _terminal("8a")
    conflict = _ConflictTarget(message)
    unused_events = []

    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        dispatcher = ResolutionDispatcher(
            store,
            conflict,
            _Target("MAKER", unused_events),
            _Target("SHADOW", unused_events),
        )
        with pytest.raises(SettlementConflict):
            dispatcher.drain(1)
        with pytest.raises(IntegrityHalted, match="target settlement conflict"):
            store.require_healthy()

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        with pytest.raises(IntegrityHalted, match="target settlement conflict"):
            reopened.require_healthy()


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, "1", None])
def test_dispatcher_requires_positive_integer_limit(tmp_path, limit):
    events = []
    with ResolutionStore(
        str(tmp_path / "resolution.db"), MonotonicStamper()
    ) as store:
        dispatcher = ResolutionDispatcher(
            store,
            _Target("FORECAST", events),
            _Target("MAKER", events),
            _Target("SHADOW", events),
        )
        with pytest.raises(ValueError, match="limit"):
            dispatcher.drain(limit)
        assert events == []
