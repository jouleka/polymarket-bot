"""POL-17 uses a separate read-only Market-Memory connection for ERS."""

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.envelope import make_envelope
from polybot.storage.market_memory import EventStore, ReadOnlyEventStore


def test_read_only_event_store_can_replay_but_has_no_writer_surface(tmp_path):
    path = str(tmp_path / "market_memory.db")
    stamper = MonotonicStamper()
    with EventStore(path) as writer:
        writer.append(make_envelope(
            stamper,
            source="fed-press",
            source_tier="PRIMARY",
            event_id="news-1",
            content="trusted evidence",
        ))

    with ReadOnlyEventStore(path) as reader:
        assert [event.event_id for event in reader.all()] == ["news-1"]
        assert [event.event_id for event in reader.replay_until(2**63 - 1)] == ["news-1"]
        assert not hasattr(reader, "append")
