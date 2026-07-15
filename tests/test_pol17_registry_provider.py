"""POL-17 fixed-universe immutable MarketRegistry generations."""

import json

import pytest

from polybot.ers.market_meta import MarketSnapshotError
from polybot.runtime.registry_provider import FixedUniverseRegistryProvider


def _market(condition_id="c1", tokens=("t1", "t2"), event_id="e1", question="Will X?"):
    return {
        "conditionId": condition_id,
        "question": question,
        "endDate": "2030-01-01T00:00:00Z",
        "clobTokenIds": json.dumps(list(tokens)),
        "events": [{"id": event_id}],
    }


def _event(event_id="e1", condition_id="c1", tokens=("t1", "t2"), tag="2"):
    return {
        "id": event_id,
        "tags": [{"id": tag}],
        "markets": [{
            "conditionId": condition_id,
            "clobTokenIds": json.dumps(list(tokens)),
        }],
    }


def test_registry_refresh_cannot_expand_the_collector_universe():
    snapshots = iter([
        ([_market()], [_event()]),
        (
            [_market(question="Updated question"),
             _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )

    first = provider.load()
    assert len(first) == 1
    assert provider.condition_ids == frozenset({"c1"})
    assert provider.token_ids == ("t1", "t2")

    with pytest.raises(MarketSnapshotError, match="fixed universe"):
        provider.refresh()

    assert provider.registry is first
    assert provider.condition_ids == frozenset({"c1"})
    assert provider.token_ids == ("t1", "t2")
