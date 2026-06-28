"""StubMarketMeta (S6 / POL-8) — the MVP MarketMeta seam.

Safety property under test: the stub is intentionally degenerate so that, with no real
MarketRegistry wired, every proposal lands in ONE "unknown" category bucket (=> the
CalibrationGate has no resolved forecasts for it => k = 0 => paper-only), the calibration
anchor reads its question text from the proposal's own resolution_summary, and the
seconds-to-resolution is a fixed sentinel STRICTLY past the prior-decay window so the prior
anchor stays active. The real condition_id -> category/question/seconds feed is deferred.
"""
from decimal import Decimal

from polybot.ers.intent_store import PendingIntent
from polybot.ers.market_meta import StubMarketMeta


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
