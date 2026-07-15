from polybot.core.models import Envelope
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
