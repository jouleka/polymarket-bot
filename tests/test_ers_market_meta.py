"""Market metadata seam tests (S6 / POL-8 stub; POL-14 real registry)."""
import dataclasses
from decimal import Decimal

import pytest

from polybot.calibration.config import CalibrationConfig
from polybot.ers.intent_store import PendingIntent
from polybot.ers.market_meta import (
    DEFAULT_CATEGORY_POLICY,
    SECONDS_TO_RESOLUTION_SENTINEL,
    MarketMetadata,
    MarketRegistry,
    MarketSnapshotError,
    StubMarketMeta,
)


def _intent(resolution_summary="Will the incumbent win the 2026 election?"):
    return PendingIntent(
        intent_id="i1", status="PROPOSED", token_id="t1", condition_id="0xabc",
        event_id="e1", side="BUY", target_price=Decimal("0.55"), max_price=Decimal("0.60"),
        size_usd_suggestion=Decimal("10"), p=Decimal("0.7"), p_confidence=Decimal("0.6"),
        resolution_summary=resolution_summary, thesis="thesis text",
        citations=("https://primary/1",), created_at=1,
    )


def test_category_for_is_single_unknown_bucket():
    meta = StubMarketMeta()
    assert meta.category_for(_intent()) == "unknown"


def test_question_text_for_returns_resolution_summary_verbatim():
    meta = StubMarketMeta()
    summary = "Will the Fed hold rates unchanged at the March meeting?"
    assert meta.question_text_for(_intent(resolution_summary=summary)) == summary


def test_question_text_for_passes_through_empty_summary():
    # resolution_summary defaults to "" upstream; the stub must not substitute or raise.
    meta = StubMarketMeta()
    assert meta.question_text_for(_intent(resolution_summary="")) == ""


def test_seconds_to_resolution_for_returns_sentinel():
    meta = StubMarketMeta()
    assert meta.seconds_to_resolution_for(_intent()) == SECONDS_TO_RESOLUTION_SENTINEL


def test_sentinel_is_strictly_past_prior_decay_window():
    # The prior anchor is dropped only WITHIN the decay window of resolution; the sentinel must
    # sit strictly OUTSIDE it so the prior stays active (DESIGN §6). Guard against a future
    # CalibrationConfig default change silently swallowing the prior.
    cfg = CalibrationConfig()
    assert SECONDS_TO_RESOLUTION_SENTINEL > cfg.prior_decay_window_seconds


def test_seconds_to_resolution_for_is_a_positive_int():
    meta = StubMarketMeta()
    secs = meta.seconds_to_resolution_for(_intent())
    assert isinstance(secs, int) and secs > 0


def test_stub_is_stateless_and_repeatable():
    # No registry, no caching, no mutation: two calls on two fresh instances agree, and a
    # second call on the same instance is identical (the seam must be side-effect free).
    a, b = StubMarketMeta(), StubMarketMeta()
    i = _intent()
    assert a.category_for(i) == b.category_for(i) == "unknown"
    assert a.question_text_for(i) == a.question_text_for(i) == i.resolution_summary
    assert a.seconds_to_resolution_for(i) == b.seconds_to_resolution_for(i)


# POL-14 Task 1: frozen result + reviewed tag-ID policy -------------------------


def test_market_metadata_is_frozen():
    result = MarketMetadata("politics", "Will X happen?", 123)
    assert dataclasses.asdict(result) == {
        "category": "politics", "question_text": "Will X happen?", "seconds_to_resolution": 123,
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.category = "crypto"


@pytest.mark.parametrize(("tag_id", "expected"), [
    ("1", "sports"),
    ("100265", "geopolitics"),
    ("2", "politics"),
    ("21", "crypto"),
    ("120", "finance"),
    ("107", "finance"),
    ("100328", "econ"),
    ("159", "econ"),
    ("225", "econ"),
    ("1401", "tech"),
    ("439", "tech"),
    ("596", "culture"),
    ("84", "weather"),
])
def test_default_category_policy_uses_reviewed_tag_ids(tag_id, expected):
    assert DEFAULT_CATEGORY_POLICY.classify([{"id": tag_id}]) == expected


@pytest.mark.parametrize(("higher", "lower", "expected"), [
    ("1", "100265", "sports"),
    ("100265", "2", "geopolitics"),
    ("2", "21", "politics"),
    ("21", "120", "crypto"),
    ("120", "100328", "finance"),
    ("100328", "1401", "econ"),
    ("1401", "596", "tech"),
    ("596", "84", "culture"),
])
def test_category_precedence_is_load_bearing(higher, lower, expected):
    # Reverse input order so this proves policy precedence, not first-tag-wins.
    assert DEFAULT_CATEGORY_POLICY.classify([{"id": lower}, {"id": higher}]) == expected


def test_unreviewed_id_cannot_activate_category_by_label_or_slug():
    assert DEFAULT_CATEGORY_POLICY.classify([
        {"id": "not-reviewed", "label": "Politics", "slug": "politics"},
    ]) is None


@pytest.mark.parametrize("tags", [None, {}, "not-a-list", ({"id": "2"},)])
def test_category_policy_rejects_non_list_tag_container(tags):
    with pytest.raises(TypeError, match="tags"):
        DEFAULT_CATEGORY_POLICY.classify(tags)


@pytest.mark.parametrize("tags", [["2"], [None], [{"slug": "politics"}], [{"id": 2}]])
def test_category_policy_rejects_malformed_tag_items(tags):
    with pytest.raises((TypeError, ValueError), match="tag"):
        DEFAULT_CATEGORY_POLICY.classify(tags)


# POL-14 Task 2: strict two-snapshot construction + identity indices -----------


def _event(event_id="e1", tag_ids=("2",)):
    return {"id": event_id, "tags": [{"id": tag_id} for tag_id in tag_ids]}


def _market(condition_id="c1", tokens=("t1", "t2"), *, event_id="e1",
            question="Will X happen?", end_date="2030-01-01T00:00:00Z", encoded=True):
    clob_tokens = list(tokens)
    if encoded:
        import json
        clob_tokens = json.dumps(clob_tokens)
    return {
        "conditionId": condition_id,
        "question": question,
        "endDate": end_date,
        "clobTokenIds": clob_tokens,
        "events": [{"id": event_id}],
    }


def _registry(markets=None, events=None):
    return MarketRegistry.from_gamma_snapshots(
        [_market()] if markets is None else markets,
        [_event()] if events is None else events,
        clock=lambda: 0,
    )


def test_registry_builds_from_json_string_and_parsed_token_arrays():
    registry = _registry(
        markets=[_market("c1", ("t1", "t2")),
                 _market("c2", ("t3", "t4"), event_id="e2", encoded=False)],
        events=[_event("e1", ("2",)), _event("e2", ("21",))],
    )
    assert len(registry) == 2


@pytest.mark.parametrize(("markets", "events"), [
    (None, [_event()]),
    ({}, [_event()]),
    ((_market(),), [_event()]),
    ([_market()], None),
    ([_market()], {}),
    ([_market()], (_event(),)),
])
def test_registry_rejects_non_list_snapshot_containers(markets, events):
    with pytest.raises(MarketSnapshotError, match="snapshot.*list"):
        MarketRegistry.from_gamma_snapshots(markets, events, clock=lambda: 0)


@pytest.mark.parametrize("row", [None, "market", []])
def test_registry_rejects_non_mapping_market_rows(row):
    with pytest.raises(MarketSnapshotError, match="market row"):
        _registry(markets=[row])


@pytest.mark.parametrize("row", [None, "event", []])
def test_registry_rejects_non_mapping_event_rows(row):
    with pytest.raises(MarketSnapshotError, match="event row"):
        _registry(events=[row])


@pytest.mark.parametrize("tokens", [
    ("only-one",),
    ("one", "two", "three"),
    ("same", "same"),
    ("", "two"),
    (123, "two"),
])
def test_registry_rejects_bad_token_members(tokens):
    with pytest.raises(MarketSnapshotError, match="token"):
        _registry(markets=[_market(tokens=tokens, encoded=False)])


@pytest.mark.parametrize("wire", [None, {}, "not-json", "{}", "[1, 2]"])
def test_registry_rejects_bad_token_wire_shapes(wire):
    row = _market()
    row["clobTokenIds"] = wire
    with pytest.raises(MarketSnapshotError, match="clobTokenIds|token"):
        _registry(markets=[row])


@pytest.mark.parametrize(("field", "value"), [
    ("conditionId", None),
    ("conditionId", ""),
    ("conditionId", 123),
    ("question", None),
    ("question", "   "),
    ("question", 123),
    ("endDate", None),
    ("endDate", "2030-01-01T00:00:00"),
    ("endDate", "not-a-date"),
])
def test_registry_rejects_missing_or_malformed_market_fields(field, value):
    row = _market()
    row[field] = value
    with pytest.raises(MarketSnapshotError, match=field):
        _registry(markets=[row])


@pytest.mark.parametrize("events_field", [None, [], [{"id": "e1"}, {"id": "e2"}], ["e1"], [{"id": 1}]])
def test_registry_rejects_ambiguous_or_malformed_market_event_link(events_field):
    row = _market()
    row["events"] = events_field
    with pytest.raises(MarketSnapshotError, match="event"):
        _registry(markets=[row])


@pytest.mark.parametrize(("field", "value"), [("id", None), ("id", ""), ("id", 1), ("tags", None)])
def test_registry_rejects_missing_or_malformed_event_fields(field, value):
    event = _event()
    event[field] = value
    with pytest.raises(MarketSnapshotError, match="event|tag"):
        _registry(events=[event])


def test_missing_event_and_unmapped_event_are_skipped_when_another_market_is_usable():
    registry = _registry(
        markets=[_market("good", ("g1", "g2"), event_id="mapped"),
                 _market("missing", ("m1", "m2"), event_id="not-returned"),
                 _market("unmapped", ("u1", "u2"), event_id="unknown-tag")],
        events=[_event("mapped", ("2",)), _event("unknown-tag", ("999999",))],
    )
    assert len(registry) == 1


def test_identical_duplicate_market_rows_are_idempotent():
    row = _market()
    assert len(_registry(markets=[row, dict(row)])) == 1


def test_conflicting_duplicate_condition_fails_loud():
    first = _market(question="Will X happen?")
    second = _market(question="Will Y happen?")
    with pytest.raises(MarketSnapshotError, match="condition.*conflict"):
        _registry(markets=[first, second])


def test_token_reused_across_conditions_fails_loud():
    with pytest.raises(MarketSnapshotError, match="token.*condition"):
        _registry(markets=[_market("c1", ("shared", "t2")),
                           _market("c2", ("shared", "t4"))])


def test_conflicting_duplicate_event_category_fails_loud():
    with pytest.raises(MarketSnapshotError, match="event.*conflict"):
        _registry(events=[_event("e1", ("2",)), _event("e1", ("21",))])


@pytest.mark.parametrize(("markets", "events"), [
    ([_market(event_id="missing")], [_event("e1", ("2",))]),
    ([_market()], [_event("e1", ("999999",))]),
    ([], [_event()]),
])
def test_registry_fails_when_no_market_is_usable(markets, events):
    with pytest.raises(MarketSnapshotError, match="no usable"):
        _registry(markets=markets, events=events)
