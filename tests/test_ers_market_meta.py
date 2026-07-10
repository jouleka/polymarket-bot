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
