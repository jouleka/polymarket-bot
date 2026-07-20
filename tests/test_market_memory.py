"""Tests for the Market-Memory event store (POL-3 / S1).

Named acceptance criteria exercised here: the DB persists across restart, and
replay is in observed_at order with no look-ahead.
"""

import threading

from polybot.core.models import Envelope
from polybot.storage.market_memory import EventStore, ReadOnlyEventStore


def _env(event_id, observed_at, *, source="reuters", source_tier="A",
         content="x", entities=(), market_links=()):
    return Envelope(
        source=source,
        source_tier=source_tier,
        event_id=event_id,
        observed_at=observed_at,
        content=content,
        entities=tuple(entities),
        market_links=tuple(market_links),
    )


def test_events_persist_across_restart(tmp_path):
    path = str(tmp_path / "mm.db")

    store = EventStore(path)
    store.append(_env("1", 10))
    store.append(_env("2", 20, entities=["FOMC"], market_links=["0xabc"]))
    store.close()

    reopened = EventStore(path)  # simulate process restart
    events = reopened.all()
    reopened.close()

    assert [e.event_id for e in events] == ["1", "2"]
    assert events[1].entities == ("FOMC",)
    assert events[1].market_links == ("0xabc",)
    assert events[1].trust == "UNTRUSTED"


def test_event_store_is_a_context_manager(tmp_path):
    path = str(tmp_path / "mm.db")

    with EventStore(path) as store:
        store.append(_env("1", 10))

    with EventStore(path) as reopened:  # closed + reopened: data is durable
        assert [e.event_id for e in reopened.all()] == ["1"]


def test_all_returns_events_in_observed_at_order(tmp_path):
    store = EventStore(str(tmp_path / "mm.db"))
    store.append(_env("late", 30))
    store.append(_env("early", 10))
    store.append(_env("mid", 20))

    assert [e.event_id for e in store.all()] == ["early", "mid", "late"]


def test_duplicate_event_id_append_is_idempotent(tmp_path):
    # A reconnecting WS or restarted poller re-delivers the same (source, event_id);
    # re-appending must be a no-op so the point-in-time log isn't inflated.
    store = EventStore(str(tmp_path / "mm.db"))
    store.append(_env("dup", 10))
    store.append(_env("dup", 11))

    events = store.all()

    assert [e.event_id for e in events] == ["dup"]
    assert events[0].observed_at == 10  # the first observation is the one kept


def test_store_can_be_driven_from_another_thread_when_opted_in(tmp_path):
    # The off-loop single-writer (POL-12) constructs the connection on one thread
    # and drives append() from a dedicated writer thread. SQLite ties a connection
    # to its creating thread unless check_same_thread=False; opting in permits the
    # writer-thread discipline. Default stays True (the check is only relaxed where
    # we own single-thread access).
    path = str(tmp_path / "mm.db")
    store = EventStore(path, check_same_thread=False)

    errors = []

    def writer():
        try:
            store.append(_env("1", 10))
        except Exception as exc:  # pragma: no cover - only hit on a regression
            errors.append(exc)

    t = threading.Thread(target=writer)
    t.start()
    t.join()
    store.close()

    assert errors == []  # cross-thread append did not raise sqlite ProgrammingError
    with EventStore(path) as reopened:
        assert [e.event_id for e in reopened.all()] == ["1"]


def test_replay_until_excludes_later_observations(tmp_path):
    store = EventStore(str(tmp_path / "mm.db"))
    store.append(_env("a", 10))
    store.append(_env("b", 20))
    store.append(_env("c", 30))

    replayed = store.replay_until(20)

    assert [e.event_id for e in replayed] == ["a", "b"]


def test_recent_by_sources_is_bounded_newest_first_for_writer_and_read_only_reader(tmp_path):
    path = str(tmp_path / "mm.db")
    with EventStore(path) as store:
        store.append(_env("old-news", 10, source="primary-a"))
        store.append(_env("venue", 40, source="data-api"))
        store.append(_env("new-news", 30, source="primary-b"))
        store.append(_env("mid-news", 20, source="primary-a"))
        assert [event.event_id for event in store.recent_by_sources(
            ("primary-a", "primary-b"), offset=1, limit=2,
        )] == ["mid-news", "old-news"]

    with ReadOnlyEventStore(path) as reader:
        assert [event.event_id for event in reader.recent_by_sources(
            ("primary-a", "primary-b"), offset=0, limit=2,
        )] == ["new-news", "mid-news"]
