"""POL-15 whole-slice resolution and settlement verification."""

from dataclasses import replace
from decimal import Decimal

import pytest

from polybot.calibration.ledger import ForecastLedger
from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger
from polybot.maker.ledger import MakerLedger
from polybot.resolution.dispatcher import ResolutionDispatcher
from polybot.resolution.errors import (
    ConditionAlreadyTerminal,
    IntegrityHalted,
    SettlementConflict,
)
from polybot.resolution.feed import PollDisposition, ResolutionFeed
from polybot.resolution.models import (
    PUSD_ADDRESS,
    DisputeState,
    LifecyclePhase,
    PayoutVector,
    ProviderObservation,
    ResolutionSubject,
    TerminalResolution,
)
from polybot.resolution.store import ResolutionStore


def _terminal(condition_byte, *, payout=PayoutVector((3, 1), 4)):
    return TerminalResolution(
        subject=ResolutionSubject(
            "event-1", "0x" + condition_byte * 32,
            ("101", "202"), "politics",
        ),
        payout=payout,
        dispute=DisputeState.CLEAR,
        block_number=100,
        block_hash="0x" + "22" * 32,
        adapter_address="0x" + "33" * 20,
        question_id="0x" + "44" * 32,
        audit_event_ids=(
            "99:1:" + "0x" + "55" * 32 + ":CONDITION_RESOLUTION",
        ),
        provider_ids=("archive-a", "archive-b"),
    )


class _Provider:
    def __init__(self, provider_id, observations, block_hash):
        self.provider_id = provider_id
        self._observations = observations
        self._block_hash = block_hash
        self.verification_error = None
        self.verify_calls = []

    def chain_id(self):
        return 137

    def latest_block(self):
        return 20

    def block_hash(self, block_number):
        assert block_number == 15
        return self._block_hash

    def observe(self, subject, block_number):
        assert block_number == 15
        return self._observations[subject.condition_id]

    def verify_terminal(self, terminal):
        self.verify_calls.append(terminal)
        if self.verification_error is not None:
            raise self.verification_error


def _observation(provider_id, subject, block_hash, *, payout=None,
                 dispute=DisputeState.UNKNOWN):
    if payout is None:
        return ProviderObservation(
            provider_id, 15, block_hash, LifecyclePhase.UNRESOLVED, None,
            DisputeState.UNKNOWN, None, None, None, None, (),
        )
    classified = dispute is not DisputeState.UNKNOWN
    audit_event_ids = (
        "14:1:" + "0x" + "77" * 32 + ":CONDITION_RESOLUTION",
    )
    if classified:
        audit_event_ids += (
            "14:2:" + "0x" + "77" * 32 + ":QUESTION_RESOLVED",
        )
    return ProviderObservation(
        provider_id, 15, block_hash, LifecyclePhase.FINALIZED, payout, dispute,
        PUSD_ADDRESS, subject.token_ids, "0x" + "55" * 20,
        "0x" + "66" * 32, audit_event_ids,
    )


def test_fractional_terminal_fans_out_crash_safely_to_all_real_ledgers(tmp_path):
    terminal = _terminal("91")
    empty_terminal = _terminal("92", payout=PayoutVector((1, 0), 1))
    condition_id = terminal.subject.condition_id
    empty_condition_id = empty_terminal.subject.condition_id
    stamper = MonotonicStamper()

    with (
        ResolutionStore(str(tmp_path / "resolution.db"), stamper) as store,
        ForecastLedger(str(tmp_path / "forecast.db"), stamper) as forecast,
        MakerLedger(str(tmp_path / "maker.db"), stamper) as maker,
        ShadowLedger(str(tmp_path / "shadow.db"), stamper) as shadow,
    ):
        forecast.record_forecast(
            "forecast-legacy", category="politics", condition_id=condition_id,
            p=Decimal("0.5"), market_mid=Decimal("0.5"),
        )
        forecast.record_forecast(
            "forecast-canonical", category="politics", condition_id=condition_id,
            p=Decimal("0.7"), market_mid=Decimal("0.6"), event_id="event-1",
            token_id="101", outcome_slot=0,
            sibling_token_ids=("101", "202"),
        )
        maker.record_fill(
            "maker-legacy", token_id="legacy", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"),
        )
        maker.record_fill(
            "maker-canonical", token_id="202", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=1,
            sibling_token_ids=("101", "202"),
        )
        shadow.record_trade(
            "shadow-legacy", token_id="legacy", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"),
        )
        shadow.record_trade(
            "shadow-canonical", token_id="101", condition_id=condition_id,
            category="politics", side="BUY", shares=Decimal("2"),
            fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
            reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
            sibling_token_ids=("101", "202"),
        )

        store.accept_terminal(terminal)
        store.accept_terminal(empty_terminal)
        dispatcher = ResolutionDispatcher(store, forecast, maker, shadow)

        def crash_after_forecast(record, changed):
            assert record.role == "FORECAST"
            assert changed == 1
            raise RuntimeError("crash after real forecast commit")

        dispatcher._after_apply = crash_after_forecast
        with pytest.raises(RuntimeError, match="real forecast commit"):
            dispatcher.drain(6)
        assert [record.sequence for record in store.pending_outbox(10)] == [
            1, 2, 3, 4, 5, 6,
        ]
        committed_forecast = forecast.get("forecast-canonical")
        assert committed_forecast.terminal_id == terminal.terminal_id
        assert committed_forecast.resolution_status == "VOID"
        assert forecast._conn.execute(
            "SELECT terminal_id, payload FROM resolution_receipts "
            "WHERE condition_id=?", (condition_id,),
        ).fetchone() == (terminal.terminal_id, terminal.canonical_bytes)

        replay = []
        dispatcher._after_apply = lambda record, changed: replay.append(
            (record.role, record.terminal.terminal_id, changed)
        )
        assert dispatcher.drain(6) == 6
        assert replay[0] == ("FORECAST", terminal.terminal_id, 0)
        assert store.pending_outbox(10) == ()

        forecasts = {row.forecast_id: row for row in forecast.all()}
        assert forecasts["forecast-canonical"].resolution_status == "VOID"
        assert forecasts["forecast-canonical"].resolution_value == Decimal("0.75")
        assert forecasts["forecast-canonical"].resolution_numerator == 3
        assert forecasts["forecast-canonical"].resolution_denominator == 4
        assert forecasts["forecast-canonical"].terminal_id == terminal.terminal_id
        assert forecasts["forecast-legacy"].resolution_status is None
        assert forecasts["forecast-legacy"].terminal_id is None

        fills = {row.fill_id: row for row in maker.all()}
        assert fills["maker-canonical"].status == "SETTLED"
        assert fills["maker-canonical"].resolution_value == Decimal("0.25")
        assert fills["maker-canonical"].resolution_numerator == 1
        assert fills["maker-canonical"].resolution_denominator == 4
        assert fills["maker-canonical"].terminal_id == terminal.terminal_id
        assert fills["maker-legacy"].status is None
        assert fills["maker-legacy"].terminal_id is None

        trades = {row.trade_id: row for row in shadow.all()}
        assert trades["shadow-canonical"].status == "SETTLED"
        assert trades["shadow-canonical"].resolution_value == Decimal("0.75")
        assert trades["shadow-canonical"].resolution_numerator == 3
        assert trades["shadow-canonical"].resolution_denominator == 4
        assert trades["shadow-canonical"].terminal_id == terminal.terminal_id
        assert trades["shadow-legacy"].status is None
        assert trades["shadow-legacy"].terminal_id is None

        with pytest.raises(ConditionAlreadyTerminal):
            forecast.record_forecast(
                "late-forecast", category="politics",
                condition_id=empty_condition_id, p=Decimal("0.7"),
                market_mid=Decimal("0.6"), event_id="event-1", token_id="101",
                outcome_slot=0, sibling_token_ids=("101", "202"),
            )
        with pytest.raises(ConditionAlreadyTerminal):
            maker.record_fill(
                "late-maker", token_id="101", condition_id=empty_condition_id,
                category="politics", side="BUY", shares=Decimal("2"),
                price_exec=Decimal("0.4"), fill_mid=Decimal("0.5"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )
        with pytest.raises(ConditionAlreadyTerminal):
            shadow.record_trade(
                "late-shadow", token_id="101", condition_id=empty_condition_id,
                category="politics", side="BUY", shares=Decimal("2"),
                fill_price=Decimal("0.4"), fill_mid=Decimal("0.5"),
                reward_accrued=Decimal("0"), event_id="event-1", outcome_slot=0,
                sibling_token_ids=("101", "202"),
            )


def test_fake_provider_whole_slice_isolates_lifecycles_and_recovers(tmp_path):
    path = str(tmp_path / "resolution.db")
    block_hash = "0x" + "ab" * 32
    subjects = tuple(
        ResolutionSubject(
            f"event-{index}", "0x" + f"{0x93 + index:02x}" * 32,
            ("101", "202"), "politics",
        )
        for index in range(7)
    )
    (unresolved, unknown, binary, disagreement, fractional, disputed,
     manual) = subjects
    cases = (
        (unresolved, None, DisputeState.UNKNOWN),
        (unknown, PayoutVector((1, 1), 2), DisputeState.UNKNOWN),
        (binary, PayoutVector((1, 0), 1), DisputeState.CLEAR),
        (disagreement, PayoutVector((1, 0), 1), DisputeState.CLEAR),
        (fractional, PayoutVector((3, 1), 4), DisputeState.CLEAR),
        (disputed, PayoutVector((1, 0), 1), DisputeState.DISPUTED),
        (manual, PayoutVector((0, 1), 1), DisputeState.MANUAL),
    )
    first_observations = {
        subject.condition_id: _observation(
            "archive-a", subject, block_hash, payout=payout, dispute=dispute
        )
        for subject, payout, dispute in cases
    }
    second_observations = {
        condition_id: replace(observation, provider_id="archive-b")
        for condition_id, observation in first_observations.items()
    }
    second_observations[disagreement.condition_id] = replace(
        second_observations[disagreement.condition_id],
        payout=PayoutVector((0, 1), 1),
    )
    first = _Provider("archive-a", first_observations, block_hash)
    second = _Provider("archive-b", second_observations, block_hash)

    with ResolutionStore(path, MonotonicStamper()) as store:
        feed = ResolutionFeed(store, (first, second))
        results = feed.poll(subjects)
        assert tuple(result.disposition for result in results) == (
            PollDisposition.UNRESOLVED,
            PollDisposition.UNKNOWN,
            PollDisposition.ACCEPTED,
            PollDisposition.UNAVAILABLE,
            PollDisposition.ACCEPTED,
            PollDisposition.ACCEPTED,
            PollDisposition.ACCEPTED,
        )
        assert tuple(results[index].dispute for index in (2, 4, 5, 6)) == (
            DisputeState.CLEAR,
            DisputeState.CLEAR,
            DisputeState.DISPUTED,
            DisputeState.MANUAL,
        )
        assert store.assessment_for(unresolved.condition_id).phase is (
            LifecyclePhase.UNRESOLVED
        )
        assert store.assessment_for(unknown.condition_id).phase is (
            LifecyclePhase.FINALIZED
        )
        assert store.assessment_for(disagreement.condition_id) is None
        assert store.terminal_for(manual.condition_id).dispute is DisputeState.MANUAL
        assert len(store.pending_outbox(20)) == 12

        repeated, = feed.poll((binary,))
        binary_terminal = store.terminal_for(binary.condition_id)
        assert repeated.disposition is PollDisposition.ALREADY_TERMINAL
        assert repeated.terminal_id == binary_terminal.terminal_id
        assert first.verify_calls[-1] == second.verify_calls[-1] == binary_terminal

    with ResolutionStore(path, MonotonicStamper()) as reopened:
        assert reopened.recovery_required is True
        feed = ResolutionFeed(reopened, (first, second))
        assert feed.recover_pending() == 4
        assert reopened.recovery_required is False

        contradiction = SettlementConflict("accepted payout changed")
        first.verification_error = contradiction
        with pytest.raises(SettlementConflict) as caught:
            feed.verify_terminal(reopened.terminal_for(binary.condition_id))
        assert caught.value is contradiction
        with pytest.raises(IntegrityHalted, match="accepted payout changed"):
            reopened.require_healthy()
