"""POL-15 durable terminal delivery dispatcher."""

from polybot.core.clock import MonotonicStamper
from polybot.resolution.dispatcher import ResolutionDispatcher
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
