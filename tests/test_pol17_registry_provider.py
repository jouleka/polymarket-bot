"""POL-17 fixed-universe immutable MarketRegistry generations."""

import json

import pytest

from polybot.ers.market_meta import MarketSnapshotError
from polybot.runtime.registry_provider import (
    FixedUniverseRegistryProvider,
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


def test_registry_refresh_publishes_only_the_fresh_coherent_subset():
    age = [100.0]
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
        age_clock=lambda: age[0],
        max_age_seconds=900.0,
    )

    first = provider.load()
    age[0] = 500.0
    refreshed = provider.refresh()

    assert refreshed is not first
    assert len(refreshed) == 1
    assert provider.condition_ids == frozenset({"c1", "c2"})
    assert provider.available_token_ids == ("t1", "t2")
    assert [row["conditionId"] for row in provider.market_rows] == ["c1"]
    age[0] = 1300.0
    assert provider.require_fresh() is refreshed


def test_registry_available_tokens_exclude_quarantined_metadata_rows():
    unavailable = {**_market("c2", ("t3", "t4"), "e2"), "endDate": None}
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: (
            [_market(), unavailable],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 100.0,
        max_age_seconds=900.0,
    )

    provider.load()

    assert provider.token_ids == ("t1", "t2", "t3", "t4")
    assert provider.available_token_ids == ("t1", "t2")


def test_registry_subset_with_no_usable_market_halts_without_replacing_authority():
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

    with pytest.raises(MarketSnapshotError, match="no usable categorized market"):
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


def test_registry_omission_does_not_mask_event_token_contradiction():
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([_market()], [_event(tokens=("changed-1", "changed-2"))]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )
    provider.load()

    with pytest.raises(MarketSnapshotError, match="token identity conflict"):
        provider.refresh()


@pytest.mark.parametrize("malformed_side", ["market", "event"])
def test_registry_omission_does_not_mask_malformed_token_container(malformed_side):
    malformed = {"t1": 0, "t2": 0}
    refresh_market = _market()
    refresh_event = _event()
    if malformed_side == "market":
        refresh_market["clobTokenIds"] = malformed
    else:
        refresh_event["markets"][0]["clobTokenIds"] = malformed
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([refresh_market], [refresh_event]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: 10.0,
        max_age_seconds=900.0,
    )
    provider.load()

    with pytest.raises(MarketSnapshotError, match="malformed"):
        provider.refresh()


def test_fresh_coherent_subset_renews_only_its_own_age_budget():
    age = [100.0]
    snapshots = iter([
        (
            [_market(), _market("c2", ("t3", "t4"), "e2")],
            [_event(), _event("e2", "c2", ("t3", "t4"), "21")],
        ),
        ([_market()], [_event()]),
    ])
    provider = FixedUniverseRegistryProvider(
        fetch_snapshot=lambda: next(snapshots),
        wall_clock=lambda: 1_700_000_000,
        age_clock=lambda: age[0],
        max_age_seconds=900.0,
    )
    provider.load()

    age[0] = 500.0
    subset = provider.refresh()

    age[0] = 1000.1
    assert provider.require_fresh() is subset

    age[0] = 1400.1
    with pytest.raises(MarketSnapshotError, match="stale"):
        provider.require_fresh()


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
