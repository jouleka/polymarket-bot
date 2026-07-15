import json
from decimal import Decimal
from types import SimpleNamespace

import pytest


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


def test_market_reader_rejects_noncanonical_selectors_instead_of_returning_empty():
    from polybot.hermes.read_views import MarketReadView

    provider, _condition_id = _registry_provider()
    reader = MarketReadView(provider)

    for kwargs in ({"condition_id": ""}, {"condition_id": 7}, {"token_id": ""},
                   {"token_id": 11}):
        with pytest.raises(ValueError, match="exact string"):
            reader(**kwargs)


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
            return [resolved, pending]

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
