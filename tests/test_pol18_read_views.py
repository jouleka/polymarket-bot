import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polybot.core.models import Envelope
from polybot.ingestion.news import DISCOVERY, PRIMARY, Source


def _registry_provider():
    from polybot.runtime.registry_provider import FixedUniverseRegistryProvider

    condition_id = "0x" + "ab" * 32
    market = {
        "conditionId": condition_id,
        "question": "Will the home team win?",
        "slug": "home-team-win",
        "clobTokenIds": json.dumps(["11", "22"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.4", "0.6"]),
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "endDate": "2030-01-01T00:00:00Z",
        "events": [{"id": "7"}],
        "volume24hr": "999999",  # provider ranking data must not leak
    }
    event = {
        "id": "7",
        "tags": [{"id": "1", "label": "provider label must not leak"}],
        "markets": [{
            "conditionId": condition_id,
            "clobTokenIds": json.dumps(["11", "22"]),
        }],
    }
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: ([market], [event]),
        wall_clock=lambda: 1_800_000_000.0,
        age_clock=lambda: 100.0,
        max_age_seconds=900.0,
    )
    provider.load()
    return provider, condition_id


def test_market_reader_returns_bounded_sanitized_current_registry_view():
    from polybot.hermes.read_views import MarketReadView

    provider, condition_id = _registry_provider()
    reader = MarketReadView(provider, default_limit=25, max_limit=50)

    result = reader(offset=0, limit=1)

    assert result == {
        "offset": 0,
        "limit": 1,
        "total": 1,
        "markets": [{
            "event_id": "7",
            "condition_id": condition_id,
            "category": "sports",
            "question": "Will the home team win?",
            "seconds_to_resolution": 93_456_000,
            "active": True,
            "closed": False,
            "outcomes": [
                {"label": "Yes", "token_id": "11", "outcome_slot": 0},
                {"label": "No", "token_id": "22", "outcome_slot": 1},
            ],
        }],
    }
    assert "volume24hr" not in json.dumps(result)
    assert "provider label" not in json.dumps(result)


def test_market_reader_prioritizes_nearest_positive_deadline_before_expired_rows():
    from polybot.hermes.read_views import MarketReadView

    def market(condition_suffix, event_id, tokens):
        return {
            "conditionId": "0x" + condition_suffix * 64,
            "question": f"Market {event_id}?",
            "slug": f"market-{event_id}",
            "clobTokenIds": json.dumps(tokens),
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.4", "0.6"]),
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "events": [{"id": event_id}],
        }

    rows = (
        market("1", "expired", ["11", "12"]),
        market("2", "later", ["21", "22"]),
        market("3", "nearest", ["31", "32"]),
    )
    seconds = {"expired": 0, "later": 600, "nearest": 30}

    class Registry:
        def metadata_for(self, identity):
            return SimpleNamespace(
                category="sports",
                question_text=f"Market {identity.event_id}?",
                seconds_to_resolution=seconds[identity.event_id],
            )

        def resolution_subject_for(self, identity):
            return SimpleNamespace(event_id=identity.event_id)

    provider = SimpleNamespace(
        market_rows=rows,
        require_fresh=lambda: Registry(),
    )

    page = MarketReadView(provider)(offset=0, limit=25)

    assert [row["event_id"] for row in page["markets"]] == [
        "nearest", "later", "expired",
    ]


def test_market_reader_samples_shared_live_tokens_once_and_marks_each_outcome():
    from polybot.hermes.read_views import MarketReadView

    provider, _condition_id = _registry_provider()
    calls = []

    def live_tokens():
        calls.append(True)
        return ("11",)

    page = MarketReadView(provider, live_token_ids=live_tokens)()

    assert calls == [True]
    assert page["markets"][0]["outcomes"] == [
        {"label": "Yes", "token_id": "11", "outcome_slot": 0, "live_book": True},
        {"label": "No", "token_id": "22", "outcome_slot": 1, "live_book": False},
    ]


def test_market_reader_rejects_noncanonical_selectors_instead_of_returning_empty():
    from polybot.hermes.read_views import MarketReadView

    provider, _condition_id = _registry_provider()
    reader = MarketReadView(provider)

    for kwargs in ({"condition_id": ""}, {"condition_id": 7}, {"token_id": ""},
                   {"token_id": 11}):
        with pytest.raises(ValueError, match="exact string"):
            reader(**kwargs)


def test_market_reader_page_isolates_one_row_without_registry_metadata():
    from polybot.hermes.read_views import MarketReadView, ReadViewUnavailable

    provider, condition_id = _registry_provider()
    malformed = dict(provider.market_rows[0])
    malformed.update({
        "conditionId": "0x" + "cd" * 32,
        "clobTokenIds": json.dumps(["33", "44"]),
        "events": [{"id": "9"}],
    })
    provider._market_rows = (malformed, *provider.market_rows)

    result = MarketReadView(provider)(offset=0, limit=25)

    assert result["total"] == 1
    assert [row["condition_id"] for row in result["markets"]] == [condition_id]
    exact = MarketReadView(provider)(condition_id=condition_id)
    assert [row["condition_id"] for row in exact["markets"]] == [condition_id]
    with pytest.raises(
            ReadViewUnavailable, match="registry metadata is unavailable"):
        MarketReadView(provider)(condition_id=malformed["conditionId"])
    with pytest.raises(
            ReadViewUnavailable, match="registry metadata is unavailable"):
        MarketReadView(provider)(token_id="33")


@pytest.mark.parametrize("event_links", [
    None,
    [],
    [{"id": "9"}, {"id": "10"}],
    ["not-an-event"],
    [{}],
])
def test_market_reader_isolates_missing_event_identity_but_exact_fails(
        event_links):
    from polybot.hermes.read_views import MarketReadView, ReadViewUnavailable

    provider, condition_id = _registry_provider()
    malformed = dict(provider.market_rows[0])
    malformed.update({
        "conditionId": "0x" + "cd" * 32,
        "clobTokenIds": json.dumps(["33", "44"]),
        "events": event_links,
    })
    provider._market_rows = (malformed, *provider.market_rows)
    reader = MarketReadView(provider)

    page = reader(offset=0, limit=25)
    assert [row["condition_id"] for row in page["markets"]] == [condition_id]
    with pytest.raises(ReadViewUnavailable, match="event identity is unavailable"):
        reader(condition_id=malformed["conditionId"])
    with pytest.raises(ReadViewUnavailable, match="event identity is unavailable"):
        reader(token_id="33")
    exact_good = reader(condition_id=condition_id)
    assert [row["condition_id"] for row in exact_good["markets"]] == [condition_id]


def test_market_reader_does_not_isolate_registry_freshness_failure(monkeypatch):
    from polybot.ers.market_meta import MarketSnapshotError
    from polybot.hermes.read_views import MarketReadView

    provider, _condition_id = _registry_provider()

    def stale():
        raise MarketSnapshotError("registry stale")

    monkeypatch.setattr(provider, "require_fresh", stale)
    with pytest.raises(MarketSnapshotError, match="registry stale"):
        MarketReadView(provider)()


def test_market_reader_does_not_isolate_gamma_normalization_failure(monkeypatch):
    from polybot.hermes import read_views

    provider, _condition_id = _registry_provider()

    def invalid(_raw):
        raise ValueError("Gamma format changed")

    monkeypatch.setattr(read_views, "normalize_market", invalid)
    with pytest.raises(ValueError, match="Gamma format changed"):
        read_views.MarketReadView(provider)()


def test_book_reader_returns_exact_live_local_book_projection():
    from polybot.hermes.read_views import BookReadView
    from polybot.ingestion.orderbook import LocalBook

    book = LocalBook()
    book.apply_book({
        "bids": [{"price": "0.40", "size": "12.50"}],
        "asks": [{"price": "0.44", "size": "7.25"}],
    })
    reader = BookReadView(lambda token_id: book, token_ids=("11", "22"))

    assert reader(token_id="11") == {
        "token_id": "11",
        "best_bid": "0.40",
        "bid_size": "12.50",
        "best_ask": "0.44",
        "ask_size": "7.25",
        "midpoint": "0.42",
        "stale": False,
    }


def test_news_reader_returns_bounded_allowlisted_tier_consistent_sanitized_evidence():
    from polybot.hermes.read_views import NewsReadView

    calls = []
    events = [
        Envelope(
            source="primary-a", source_tier=PRIMARY, event_id="primary-id",
            observed_at=30, published_at=123, content="headline\u202e" + "x" * 100,
        ),
        Envelope(
            source="primary-a", source_tier=DISCOVERY, event_id="tier-mismatch",
            observed_at=20, content="must not pass",
        ),
        Envelope(
            source="discovery-a", source_tier=DISCOVERY, event_id="discovery-id",
            observed_at=10, content="discovery headline",
        ),
    ]

    class Store:
        def recent_by_sources(self, sources, *, offset, limit):
            calls.append((sources, offset, limit))
            return events

    allowlist = (
        Source("primary-a", "https://primary.test/feed", PRIMARY,
               publisher_group="primary-group"),
        Source("discovery-a", "https://discovery.test/feed", DISCOVERY,
               publisher_group="discovery-group"),
    )

    page = NewsReadView(
        Store(), allowlist=allowlist, max_content_chars=64,
    )(offset=2, limit=3)

    assert calls == [(("primary-a", "discovery-a"), 2, 3)]
    assert [event["citation_id"] for event in page["events"]] == [
        "primary-id", "discovery-id",
    ]
    assert page["events"][0] | {"content": None} == {
        "source": "primary-a",
        "source_tier": PRIMARY,
        "publisher_group": "primary-group",
        "citation_eligible": True,
        "citation_id": "primary-id",
        "published_at": 123,
        "content": None,
    }
    assert page["events"][1]["citation_eligible"] is False
    assert len(page["events"][0]["content"]) <= 64
    assert page["events"][0]["content"].startswith("⟦UNTRUSTED⟧\n")
    assert page["events"][0]["content"].endswith("\n⟦UNTRUSTED⟧")
    assert "\u202e" not in page["events"][0]["content"]


def test_news_reader_rejects_offsets_beyond_fixed_scan_bound():
    from polybot.hermes.read_views import NewsReadView

    store = SimpleNamespace(recent_by_sources=lambda *_args, **_kwargs: [])
    source = Source("primary-a", "https://primary.test/feed", PRIMARY)

    with pytest.raises(ValueError, match="offset"):
        NewsReadView(store, allowlist=(source,))(offset=1001, limit=1)


def test_book_reader_rejects_a_stale_shared_local_book():
    from polybot.hermes.read_views import BookReadView, ReadViewUnavailable
    from polybot.ingestion.orderbook import LocalBook

    book = LocalBook()
    book.apply_book({
        "bids": [{"price": "0.40", "size": "12.50"}],
        "asks": [{"price": "0.44", "size": "7.25"}],
    })
    book.is_stale = lambda: True

    with pytest.raises(ReadViewUnavailable, match="stale"):
        BookReadView(lambda _token_id: book, token_ids=("11",))(token_id="11")


@pytest.mark.parametrize("bids,asks", [
    ([], [{"price": "0.44", "size": "7"}]),
    ([{"price": "0.44", "size": "7"}], []),
    ([{"price": "0.44", "size": "7"}], [{"price": "0.44", "size": "7"}]),
    ([{"price": "0.45", "size": "7"}], [{"price": "0.44", "size": "7"}]),
])
def test_book_reader_rejects_empty_locked_and_crossed_books(bids, asks):
    from polybot.hermes.read_views import BookReadView, ReadViewUnavailable
    from polybot.ingestion.orderbook import LocalBook

    book = LocalBook()
    book.apply_book({"bids": bids, "asks": asks})

    with pytest.raises(ReadViewUnavailable, match="usable"):
        BookReadView(lambda _token_id: book, token_ids=("11",))(token_id="11")


def test_ledger_reader_returns_only_bounded_resolved_history_newest_first():
    from polybot.hermes.read_views import LedgerReadView

    pending = SimpleNamespace(forecast_id="pending", resolution_status=None)
    resolved = SimpleNamespace(
        forecast_id="f-2", category="sports", condition_id="condition-2",
        p=Decimal("0.625"), market_mid=Decimal("0.500"), created_at=11,
        resolution_status="WON", resolved_at=21, event_id="event-2",
        token_id="token-2", outcome_slot=0,
        sibling_token_ids=("token-2", "token-3"),
        resolution_value=Decimal("1"), resolution_numerator=1,
        resolution_denominator=1, terminal_id="terminal-2",
    )

    class Ledger:
        def resolved(self, category=None, limit=None):
            assert category == "sports"
            assert limit == 1
            return [resolved]

        def all(self):
            raise AssertionError("Hermes ledger view must never query pending forecasts")

    reader = LedgerReadView(Ledger(), categories=("sports",), max_limit=20)

    assert reader(category="sports", limit=1) == {
        "category": "sports",
        "limit": 1,
        "records": [{
            "forecast_id": "f-2",
            "category": "sports",
            "condition_id": "condition-2",
            "p": "0.625",
            "market_mid": "0.500",
            "created_at": 11,
            "resolution_status": "WON",
            "resolved_at": 21,
            "event_id": "event-2",
            "token_id": "token-2",
            "outcome_slot": 0,
            "sibling_token_ids": ["token-2", "token-3"],
            "resolution_value": "1",
            "resolution_numerator": 1,
            "resolution_denominator": 1,
            "terminal_id": "terminal-2",
        }],
    }


def test_flags_reader_is_conservative_and_read_only_before_live_detectors_exist():
    from polybot.hermes.read_views import FlagsReadView

    reader = FlagsReadView(
        runtime_ready=lambda: True,
        controller_state=lambda: "RUNNING",
        resolution_state=lambda: SimpleNamespace(
            terminal_condition_ids=("terminal-condition",),
            frozen_condition_ids=("frozen-condition",),
        ),
        registry_fresh=lambda: True,
        live_book_tokens=lambda: ("11", "22"),
    )

    assert reader() == {
        "runtime_ready": True,
        "controller_state": "RUNNING",
        "registry_fresh": True,
        "live_book_tokens": ["11", "22"],
        "terminal_condition_ids": ["terminal-condition"],
        "frozen_condition_ids": ["frozen-condition"],
        "detectors": {
            "available": False,
            "action": "FLAG_ONLY",
            "reasons": ["live_detector_inputs_unavailable"],
        },
        "trading_permission": False,
    }


def test_flags_reader_rejects_ambiguous_truthy_health_values():
    from polybot.hermes.read_views import FlagsReadView

    reader = FlagsReadView(
        runtime_ready=lambda: "false",
        controller_state=lambda: "RUNNING",
        resolution_state=lambda: SimpleNamespace(
            terminal_condition_ids=(), frozen_condition_ids=(),
        ),
        registry_fresh=lambda: True,
        live_book_tokens=lambda: (),
    )

    with pytest.raises(TypeError, match="boolean"):
        reader()
