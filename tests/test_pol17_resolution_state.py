"""POL-17 restart state derives only from durable POL-15 authority."""

from types import SimpleNamespace

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.resolution.errors import SettlementConflict
from polybot.resolution.feed import ResolutionFeed
from polybot.resolution.models import (
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionAssessment, ResolutionStore


def _subject(byte, event_id):
    return ResolutionSubject(
        event_id,
        "0x" + byte * 32,
        (str(int(byte, 16) + 100), str(int(byte, 16) + 200)),
        "politics",
    )


def test_resolution_store_exposes_terminal_and_finalized_unknown_restart_state(tmp_path):
    terminal_subject = _subject("11", "terminal-event")
    unknown_subject = _subject("22", "unknown-event")
    terminal = TerminalResolution(
        subject=terminal_subject,
        payout=PayoutVector((1, 0), 1),
        dispute=DisputeState.CLEAR,
        block_number=200,
        block_hash="0x" + "33" * 32,
        adapter_address="0x" + "44" * 20,
        question_id="0x" + "55" * 32,
        audit_event_ids=(
            "199:1:" + "0x" + "66" * 32 + ":CONDITION_RESOLUTION",
        ),
        provider_ids=("a", "b"),
    )
    unknown = ResolutionAssessment(
        subject=unknown_subject,
        phase=LifecyclePhase.FINALIZED,
        dispute=DisputeState.UNKNOWN,
        payout=PayoutVector((1, 1), 2),
        block_number=201,
        block_hash="0x" + "77" * 32,
        detail="finalized path is unknown",
    )
    path = str(tmp_path / "resolution.db")

    with ResolutionStore(path, MonotonicStamper()) as store:
        store.accept_terminal(terminal)
        store.record_assessment(unknown)

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        state = reopened.runtime_state()
        assert state.terminal_condition_ids == (terminal_subject.condition_id,)
        assert state.frozen_condition_ids == (unknown_subject.condition_id,)


def test_resolution_startup_preflight_rejects_non_polygon_provider(tmp_path):
    providers = (
        SimpleNamespace(provider_id="a", chain_id=lambda: 137),
        SimpleNamespace(provider_id="b", chain_id=lambda: 1),
    )
    with ResolutionStore(
            str(tmp_path / "resolution.db"), MonotonicStamper()) as store:
        feed = ResolutionFeed(store, providers)

        with pytest.raises(SettlementConflict, match="Polygon chain 137"):
            feed.validate_providers()
