"""Tests for the citation truth-gate (S6 / POL-8).

The truth-gate is the ERS-side re-derivation of a Hermes proposal's evidence: it
matches citations against the already-sanitized EventStore (NEVER fetches/executes
them), keeps only allowlisted PRIMARY envelopes, collapses them by publisher_group,
and answers (a) corroborated = >=2 INDEPENDENT primaries, (b) the indirect-prompt-
injection signature: one fresh source moving p while a thin book lets it push the
mid -> same_source_collusion. DISCOVERY tier and non-allowlisted citations never
count and never trigger. Every value that is money/depth is a Decimal from a string.
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.news import DISCOVERY, PRIMARY, Source
from polybot.ingestion.orderbook import LocalBook
from polybot.storage.market_memory import EventStore
from polybot.truthgate.gate import (
    REASON_SAME_SOURCE,
    REASON_TRUTH_GATE_REFUSE,
    TruthGateConfig,
    TruthVerdict,
    verify,
)


def test_config_rejects_non_positive_fields():
    # all three fields must be strictly > 0 (fail loud, not a silent default)
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=0,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("0"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0"))


# --- local builders (the repo's per-file pattern; no conftest) ---
_CFG = TruthGateConfig(freshness_window_ns=10_000,
                       thin_book_depth_usd=Decimal("50"),
                       thin_book_move=Decimal("0.05"))

# Two independent primaries (distinct publisher_group), one discovery aggregator.
_FED = Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml",
              PRIMARY, publisher_group="federalreserve.gov")
_SEC = Source("sec-press", "https://www.sec.gov/news/pressreleases.rss",
              PRIMARY, publisher_group="sec.gov")
_GNEWS = Source("google-news-top", "https://news.google.com/rss", DISCOVERY,
                publisher_group="google.com")
_ALLOWLIST = (_FED, _SEC, _GNEWS)


def _book(ask="0.50", ask_size="1000", bid="0.49", bid_size="1000"):
    """Healthy, deep, tight book by default (NOT the collusion signature)."""
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book


def _seed(store, stamper, source, event_id, *, link):
    store.append(make_envelope(stamper, source=source.name, source_tier=source.tier,
                               event_id=event_id, content="text",
                               published_at=None, entities=(link,), market_links=()))


def test_two_distinct_groups_corroborated(tmp_path):
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _SEC, "sec1", link="https://www.sec.gov/1")
        now = stamper.stamp()
        v = verify(("fed1", "sec1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert isinstance(v, TruthVerdict)
    assert v.refused is False and v.reason is None
    assert v.corroborated is True
    assert set(v.primary_groups) == {"federalreserve.gov", "sec.gov"}


def test_same_publisher_group_not_corroborated_regression(tmp_path):
    # fed-press and fed-monetary are both federalreserve.gov: the confirmed same-domain
    # bypass. Two FEEDS, ONE publisher_group -> NOT independent -> NOT corroborated.
    fed_press = Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml",
                       PRIMARY, publisher_group="federalreserve.gov")
    fed_monetary = Source("fed-monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml",
                          PRIMARY, publisher_group="federalreserve.gov")
    allowlist = (fed_press, fed_monetary)
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, fed_press, "fp1", link="https://www.federalreserve.gov/p1")
        _seed(store, stamper, fed_monetary, "fm1", link="https://www.federalreserve.gov/m1")
        now = stamper.stamp()
        v = verify(("fp1", "fm1"), event_store=store, book=_book(),
                   allowlist=allowlist, now_ns=now, config=_CFG)

    assert v.refused is False                       # present, just not independent
    assert v.corroborated is False                  # the regression assertion
    assert v.primary_groups == ("federalreserve.gov",)   # collapsed to one group


def test_discovery_tier_ignored(tmp_path):
    # One real primary + one discovery aggregator citation. Discovery must not count
    # toward corroboration NOR toward refusal: the single primary stands alone ->
    # present, uncorroborated, not refused.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _GNEWS, "gn1", link="https://news.google.com/x")
        now = stamper.stamp()
        v = verify(("fed1", "gn1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.corroborated is False                       # gn1 (DISCOVERY) does not count
    assert v.primary_groups == ("federalreserve.gov",)   # only the primary survives


def test_no_allowlisted_primary_refused(tmp_path):
    # A citation that resolves to a NON-allowlisted source, plus a citation that
    # resolves to nothing. No allowlisted primary survives -> refuse-and-alert.
    rogue = Source("rogue-blog", "https://rogue.example/feed", PRIMARY,
                   publisher_group="rogue.example")   # NOT in _ALLOWLIST
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, rogue, "rogue1", link="https://rogue.example/1")
        now = stamper.stamp()
        v = verify(("rogue1", "does-not-exist"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is True
    assert v.reason == REASON_TRUTH_GATE_REFUSE
    assert v.corroborated is False
    assert v.primary_groups == ()


def test_empty_citations_refused_truth_gate(tmp_path):
    # Empty citations resolve to zero allowlisted primaries -> refuse-and-alert.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        now = stamper.stamp()
        v = verify((), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
    assert v.refused is True and v.reason == REASON_TRUTH_GATE_REFUSE
    assert v.corroborated is False and v.primary_groups == ()


def test_single_primary_present_but_uncorroborated(tmp_path):
    # Exactly ONE allowlisted primary group, healthy deep book (no collusion signature)
    # -> NOT refused, corroborated=False (informational-only, w_news=0 downstream).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()
        v = verify(("fed1",), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
    assert v.refused is False and v.reason is None
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)


def _thin_pushed_book():
    """The collusion signature: a THIN top-of-book (tiny resting size) whose wide
    bid/ask spread reads as a mid that was pushed. depth USD = 10 * 0.55 = 5.5 < 50;
    spread = 0.55 - 0.45 = 0.10 >= thin_book_move 0.05."""
    book = LocalBook()
    book.apply_book({"bids": [{"price": "0.45", "size": "10"}],
                     "asks": [{"price": "0.55", "size": "10"}]})
    return book


def _fake_stamper():
    """A MonotonicStamper backed by a counter (1, 2, 3, ...) so that stamp deltas
    are exactly 1 ns -- well within freshness_window_ns=10_000 and independent of
    SQLite write latency, making freshness tests deterministic."""
    counter = [0]

    def _clock():
        counter[0] += 1
        return counter[0]

    return MonotonicStamper(clock=_clock)


def test_same_source_plus_thin_book_move_refused(tmp_path):
    # ONE fresh primary source moving p + a thin book it can push -> indirect-prompt-
    # injection signature -> refused same_source_collusion. The forecast is NOT logged
    # upstream for this reason (handled in the loop), and the signer is never reached.
    stamper = _fake_stamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()                         # fed1 is fresh within the window
        v = verify(("fed1",), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is True
    assert v.reason == REASON_SAME_SOURCE
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)


def test_corroborated_evidence_never_collusion(tmp_path):
    # Two INDEPENDENT fresh primaries on the SAME thin pushed book: corroboration
    # defeats the collusion signature (it takes >=2 distinct groups to push together,
    # which is exactly what corroboration verifies against). Not refused.
    stamper = _fake_stamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        _seed(store, stamper, _SEC, "sec1", link="https://www.sec.gov/1")
        now = stamper.stamp()
        v = verify(("fed1", "sec1"), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False and v.reason is None
    assert v.corroborated is True


def test_single_source_but_stale_not_collusion(tmp_path):
    # ONE primary + thin pushed book, but the source is STALE (outside the freshness
    # window) -> the "fresh injection + pre-position" timing signature is absent ->
    # NOT collusion; just present-uncorroborated.
    stamper = _fake_stamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        observed = stamper.stamp()
        now = observed + _CFG.freshness_window_ns + 1   # fed1 is now stale
        v = verify(("fed1",), event_store=store, book=_thin_pushed_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.corroborated is False
    assert v.primary_groups == ("federalreserve.gov",)


def test_non_allowlisted_citation_dropped_but_primary_survives(tmp_path):
    # A rogue (non-allowlisted) citation alongside a real allowlisted primary: the
    # rogue is silently dropped, the primary still yields present-uncorroborated.
    rogue = Source("rogue-blog", "https://rogue.example/feed", PRIMARY,
                   publisher_group="rogue.example")
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        _seed(store, stamper, rogue, "rogue1", link="https://rogue.example/1")
        _seed(store, stamper, _FED, "fed1", link="https://www.federalreserve.gov/1")
        now = stamper.stamp()
        v = verify(("rogue1", "fed1"), event_store=store, book=_book(),
                   allowlist=_ALLOWLIST, now_ns=now, config=_CFG)

    assert v.refused is False
    assert v.primary_groups == ("federalreserve.gov",)   # rogue dropped, not counted


def test_citations_are_matched_never_fetched(tmp_path):
    # Pass an http(s) citation string that is NOT in the store. The gate must NOT
    # attempt any network I/O to resolve it -- it simply fails to match. We prove the
    # gate is network-free by patching out the http clients it could conceivably use
    # and asserting they are never called; the unresolved citation yields zero matches
    # (-> refuse), with no exception and no fetch.
    import httpx

    calls = []

    class _Boom:
        def __getattr__(self, _name):
            def _fail(*a, **k):
                calls.append(1)
                raise AssertionError("truth-gate must never fetch a citation")
            return _fail

    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as store:
        now = stamper.stamp()
        original_client = httpx.Client
        httpx.Client = _Boom              # any accidental fetch path explodes
        try:
            v = verify(("https://anything.example/never-fetched",),
                       event_store=store, book=_book(),
                       allowlist=_ALLOWLIST, now_ns=now, config=_CFG)
        finally:
            httpx.Client = original_client

    assert calls == []                                   # no fetch attempted
    assert v.refused is True and v.reason == REASON_TRUTH_GATE_REFUSE
