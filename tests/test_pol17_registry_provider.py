"""POL-17 fixed-universe immutable MarketRegistry generations."""

import json

import pytest

from polybot.ers.market_meta import MarketSnapshotError
from polybot.runtime.registry_provider import (
    FixedUniverseRegistryProvider,
    RegistryRefreshUnavailable,
)


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


def test_registry_refresh_retains_last_good_generation_when_gamma_omits_a_market():
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([_market(question="Updated question")], [_event()]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )

    first = provider.load()

    with pytest.raises(RegistryRefreshUnavailable, match="incomplete"):
        provider.refresh()

    assert provider.registry is first
    assert provider.condition_ids == frozenset({"c1", "c2"})
    assert provider.market_rows[0]["question"] == "Will X?"


def test_registry_omission_is_detected_before_candidate_metadata_quarantine():
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([{**_market(), "endDate": None}], [_event()]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )
    first = provider.load()

    with pytest.raises(RegistryRefreshUnavailable, match="incomplete"):
        provider.refresh()

    assert provider.registry is first


def test_registry_refresh_does_not_mask_token_contradiction_as_an_omission():
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([_market(tokens=("changed-1", "changed-2"))], [
            _event(tokens=("changed-1", "changed-2")),
        ]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )
    provider.load()

    with pytest.raises(MarketSnapshotError, match="changed"):
        provider.refresh()


def test_registry_refresh_replaces_market_rows_for_read_only_consumers():
    snapshots = iter([
        ([_market(question="Initial question")], [_event()]),
        ([_market(question="Updated question")], [_event()]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )

    provider.load()
    assert provider.market_rows[0]["question"] == "Initial question"

    provider.refresh()

    assert provider.market_rows[0]["question"] == "Updated question"


def test_last_good_registry_fails_closed_after_its_age_budget():
    age = [100.0]
    calls = [0]

    def fetch_snapshot():
        calls[0] += 1
        if calls[0] == 1:
            return [_market()], [_event()]
        raise RuntimeError("Gamma unavailable")

    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=fetch_snapshot,
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: age[0],
        max_age_seconds=900.0,
    )
    loaded = provider.load()

    age[0] = 999.0
    with pytest.raises(RuntimeError, match="Gamma unavailable"):
        provider.refresh()
    assert provider.require_fresh() is loaded

    age[0] = 1000.1
    with pytest.raises(MarketSnapshotError, match="stale"):
        provider.require_fresh()
