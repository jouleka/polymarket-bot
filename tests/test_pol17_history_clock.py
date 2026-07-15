from polybot.core.models import Envelope
from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.runtime.history_clock import make_history_stamper
from polybot.storage.market_memory import EventStore


def test_history_stamper_starts_above_durable_max_after_restart(tmp_path):
    path = str(tmp_path / "events.db")
    with EventStore(path) as store:
        store.append(Envelope(
            source="test",
            source_tier="DIRECT",
            event_id="old",
            observed_at=900,
            content="{}",
        ))

    stamper = make_history_stamper(path, clock=lambda: 100)

    assert stamper.stamp() == 901


def test_history_stamper_floors_above_every_runtime_store(tmp_path):
    events_path = str(tmp_path / "events.db")
    intents_path = str(tmp_path / "intents.db")
    with EventStore(events_path):
        pass
    with IntentStore(
            intents_path, MonotonicStamper(clock=lambda: 900)) as intents:
        intents.propose_trade(
            "intent", token_id="1", condition_id="condition", event_id="event",
            side="BUY", target_price="0.4", max_price="0.5",
            size_usd_suggestion="5", p="0.7", p_confidence="0.8",
        )

    stamper = make_history_stamper(
        (events_path, intents_path), clock=lambda: 100
    )

    assert stamper.stamp() == 901
