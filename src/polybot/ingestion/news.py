"""News fast-path ingestion (POL-3 / S1).

News is UNTRUSTED data, never instructions. This layer ingests ONLY allowlisted
sources, parses RSS/Atom into items, runs each item's free text through the
sanitizer (strip control / zero-width / bidi + spotlight), and persists it as an
UNTRUSTED Envelope tagged with the source tier:

  - PRIMARY   -- curated primary feeds (agency / court / econ release pages, wires,
                 league feeds). May inform trades (downstream still requires >=2
                 independent primary sources before non-tiny size).
  - DISCOVERY -- a cheap aggregator / GDELT. For discovery + backtest ONLY; the tier
                 is recorded so the ERS / Hermes NEVER lets it trigger a trade.

The HTTP fetch is injected so the core is network-free + testable. A non-allowlisted
source is REFUSED (the allowlist is the first injection-defense gate). The Calendar
pre-stager schedules known events (FOMC / CPI / SCOTUS / elections / games) so a
collector can arm connections seconds ahead; the actual arming is orchestration that
consumes ``due_within`` and is wired later.
"""

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.sanitizer import neutralize, sanitize

PRIMARY = "PRIMARY"
DISCOVERY = "DISCOVERY"


class RecentNewsCache:
    """Fixed-memory current-feed snapshots for bounded Hermes content queries."""

    def __init__(self, *, max_items_per_source=50, max_content_chars=4096):
        if (isinstance(max_items_per_source, bool)
                or not isinstance(max_items_per_source, int)
                or not 1 <= max_items_per_source <= 50):
            raise ValueError("recent news item bound must be in [1, 50]")
        if (isinstance(max_content_chars, bool)
                or not isinstance(max_content_chars, int)
                or not 1 <= max_content_chars <= 4096):
            raise ValueError("recent news content bound must be in [1, 4096]")
        self._max_items_per_source = max_items_per_source
        self._max_content_chars = max_content_chars
        self._by_source = {}

    def replace_source(self, source_name, envelopes):
        if not isinstance(source_name, str) or not source_name:
            raise ValueError("recent news source must be a non-empty string")
        values = tuple(envelopes)
        if any(
                getattr(value, "source", None) != source_name
                or not isinstance(getattr(value, "observed_at", None), int)
                or isinstance(value.observed_at, bool)
                or not isinstance(getattr(value, "content", None), str)
                for value in values):
            raise ValueError("recent news snapshot contains an invalid envelope")
        selected = sorted(
            values, key=lambda value: value.observed_at, reverse=True,
        )[:self._max_items_per_source]
        self._by_source[source_name] = tuple(
            replace(value, content=value.content[:self._max_content_chars])
            for value in selected
        )

    def recent_by_sources(self, source_names, *, offset, limit,
                          max_content_chars=4096, max_event_id_chars=2048,
                          priority_sources=(), content_query=None):
        sources = tuple(source_names)
        priority = tuple(priority_sources)
        if (not sources or len(sources) != len(set(sources))
                or any(not isinstance(source, str) or not source for source in sources)):
            raise ValueError("recent news sources must be non-empty unique strings")
        if (len(priority) != len(set(priority))
                or any(not isinstance(source, str) or not source for source in priority)
                or not set(priority).issubset(sources)):
            raise ValueError("priority news sources must be a unique source subset")
        if (isinstance(offset, bool) or not isinstance(offset, int)
                or not 0 <= offset <= 1000
                or isinstance(limit, bool) or not isinstance(limit, int)
                or not 1 <= limit <= 50):
            raise ValueError("recent news pagination must be bounded")
        if (isinstance(max_content_chars, bool)
                or not isinstance(max_content_chars, int)
                or not 1 <= max_content_chars <= 4096
                or isinstance(max_event_id_chars, bool)
                or not isinstance(max_event_id_chars, int)
                or not 1 <= max_event_id_chars <= 2048):
            raise ValueError("recent news field bounds are invalid")
        if content_query is not None and (
                not isinstance(content_query, str) or not content_query
                or len(content_query) > 128 or not content_query.isascii()
                or not content_query.isprintable()):
            raise ValueError("recent news content query must be printable ASCII")

        rows = [
            value
            for source in sources
            for value in self._by_source.get(source, ())
            if len(value.event_id) <= max_event_id_chars
            and (content_query is None
                 or content_query.casefold() in value.content.casefold())
        ]
        priority_set = frozenset(priority)
        rows.sort(key=lambda value: (
            value.source not in priority_set,
            -value.observed_at,
            value.source,
            value.event_id,
        ))
        return [
            replace(value, content=value.content[:max_content_chars])
            for value in rows[offset:offset + limit]
        ]


class Source:
    """An allowlisted news feed: a stable name, a URL, and a trust tier.

    ``publisher_group`` is the source-INDEPENDENCE key the S6 truth-gate uses: two
    citations are independent iff their publisher_groups differ. Left empty it is
    auto-derived from the registrable domain of ``url`` -- so two feeds on the same
    host (e.g. both federalreserve.gov feeds) collapse to ONE group and are correctly
    NOT counted as two independent corroborating sources. Pass an explicit
    ``publisher_group`` to bind feeds across hosts that share an owner."""

    def __init__(self, name, url, tier, kind="rss", *, publisher_group=""):
        if tier not in (PRIMARY, DISCOVERY):
            raise ValueError(f"unknown news tier: {tier!r}")
        self.name = name
        self.url = url
        self.tier = tier
        self.kind = kind
        self.publisher_group = publisher_group or _registrable_domain(url)


def _local(tag):
    return tag.rsplit("}", 1)[-1]  # drop any XML namespace -> local name


# A small set of multi-label public suffixes (no tldextract dependency: pyproject
# pins only httpx + websockets). Covers the common ccTLD second levels so a UK/AU/etc.
# host resolves to its registrable domain rather than the bare suffix. Single-label
# TLDs (.gov, .com, .org, ...) fall through to the simple "last two labels" rule, which
# is exactly what collapses both federalreserve.gov feeds into one publisher_group.
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au", "gov.au",
    "co.jp", "or.jp", "go.jp",
    "co.nz", "govt.nz",
    "com.br", "gov.br",
    "co.in", "gov.in",
    "com.cn", "gov.cn",
})


def _registrable_domain(url):
    """Best-effort registrable domain (eTLD+1) of a URL, lowercased, no port/userinfo.

    Dependency-free: handles common multi-label ccTLD suffixes explicitly, otherwise
    takes the last two labels. Returns "" if no host can be parsed."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in _MULTI_LABEL_SUFFIXES:
        return last_three
    return last_two


def parse_feed(xml_text):
    """Parse an RSS 2.0 or Atom feed into a list of item dicts
    {title, link, guid, published, summary}. Namespace-tolerant.

    The XML is UNTRUSTED, so a DOCTYPE / ENTITY declaration is refused outright
    (billion-laughs amplification + XXE defense) -- legitimate RSS/Atom never
    declares entities."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("refusing news feed with a DOCTYPE/ENTITY declaration "
                         "(billion-laughs / XXE defense)")
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            items.append(_parse_entry(el))
    return items


def _parse_entry(entry):
    item = {"title": "", "link": "", "guid": "", "published": "", "summary": ""}
    for child in entry:
        name = _local(child.tag)
        text = (child.text or "").strip()
        if name == "title":
            item["title"] = text
        elif name == "link":
            item["link"] = child.get("href") or text  # Atom href attr / RSS text
        elif name in ("guid", "id"):
            item["guid"] = text
        elif name in ("pubDate", "published", "updated") and not item["published"]:
            item["published"] = text
        elif name in ("description", "summary", "content") and not item["summary"]:
            item["summary"] = text
    if not item["guid"]:
        item["guid"] = item["link"]  # fall back to the link as the stable id
    return item


def _epoch(published):
    """Best-effort RFC-822 (RSS) or ISO-8601 (Atom) timestamp -> unix epoch, else None."""
    if not published:
        return None
    for parse in (parsedate_to_datetime, lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            dt = parse(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except (TypeError, ValueError):
            continue
    return None


class NewsPoller:
    def __init__(self, fetch, stamper, store, allowlist, *, sanitizer=sanitize,
                 recent_cache=None):
        self._fetch = fetch  # async (url) -> feed text
        self._stamper = stamper
        self._store = store
        self._sanitizer = sanitizer
        self._allowlist = {s.name: s for s in allowlist}
        if recent_cache is not None and not callable(
                getattr(recent_cache, "replace_source", None)):
            raise TypeError("recent news cache must expose replace_source")
        self._recent_cache = recent_cache

    async def poll_source(self, name):
        """Fetch + persist one allowlisted feed. Refuses an unknown source (the
        allowlist gate). Idempotent on the item guid. Raises (fail loud) on a
        malformed feed or fetch error -- a MULTI-source caller should use ``poll_all``
        so one bad feed never aborts the others.

        NB: ``content`` is sanitized + spotlit text for an LLM consumer; it is NOT
        HTML-safe -- a human-facing renderer must HTML-escape it. ``event_id`` (guid)
        and the ``entities`` link are NEUTRALIZED (invisible chars + forged spotlight
        markers stripped) so they cannot smuggle a delimiter onto a path that might be
        read; they are still raw text otherwise and are plumbing, not instructions."""
        source = self._allowlist.get(name)
        if source is None:
            raise ValueError(f"refusing news source not in the allowlist: {name!r}")
        items = parse_feed(await self._fetch(source.url))
        persisted = 0
        snapshot = []
        for item in items:
            guid = neutralize(item["guid"])
            if not guid:
                continue  # no stable id -> skip (cannot dedup / reference it)
            free_text = f"{item['title']}\n{item['summary']}".strip()
            envelope = make_envelope(
                self._stamper,
                source=source.name,
                source_tier=source.tier,
                event_id=guid,
                content=self._sanitizer(free_text),     # spotlighted, invisible chars stripped
                published_at=_epoch(item["published"]),
                entities=(neutralize(item["link"]),) if item["link"] else (),
            )
            self._store.append(envelope)
            snapshot.append(envelope)
            persisted += 1
        if self._recent_cache is not None:
            self._recent_cache.replace_source(source.name, snapshot)
        return persisted

    async def poll_all(self):
        """Poll every allowlisted source, ISOLATING failures so one bad feed (parse
        error, transient fetch) never aborts the others. Returns {name: count|Exception}."""
        results = {}
        for name in self._allowlist:
            try:
                results[name] = await self.poll_source(name)
            except Exception as exc:  # noqa: BLE001 - one bad source must not stop the rest
                results[name] = exc
        return results


class Calendar:
    """Scheduled high-impact events (FOMC / CPI / SCOTUS / elections / games) for the
    pre-stager. Each event is a dict with at least ``at`` (unix epoch) and ``label``.
    Pure: ``due_within`` is the query a collector uses to arm connections ahead."""

    def __init__(self, events=()):
        self._events = sorted(events, key=lambda e: e["at"])

    def due_within(self, now, horizon):
        return [e for e in self._events if now <= e["at"] <= now + horizon]


class CalendarScheduler:
    """Drives the Calendar: polls ``due_within`` and fires a pre-stage hook ONCE per
    event as it enters the horizon, so a collector can arm connections seconds ahead of
    a scheduled event (FOMC / CPI / SCOTUS / a game). The scheduler decides only WHEN to
    pre-stage; what arming actually does is the consumer's job (deferred orchestration),
    passed as ``on_due`` (sync or async). Clock-injected + ``max_polls``-bounded so it is
    deterministic and testable. ``on_due`` exceptions propagate (fail loud) -- the hook
    must be robust.
    """

    def __init__(self, calendar, on_due, *, horizon, clock, sleep=asyncio.sleep,
                 poll_interval=30.0):
        self._calendar = calendar
        self._on_due = on_due
        self._horizon = horizon
        self._clock = clock
        self._sleep = sleep
        self._poll_interval = poll_interval

    async def run(self, *, max_polls=None):
        fired = set()  # (at, label) already pre-staged -> fire exactly once
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            now = self._clock()
            for event in self._calendar.due_within(now, self._horizon):
                key = (event["at"], event["label"])
                if key in fired:
                    continue  # already pre-staged on an earlier poll while still in-window
                fired.add(key)
                result = self._on_due(event)
                if asyncio.iscoroutine(result):
                    await result
            if max_polls is None or polls < max_polls:
                await self._sleep(self._poll_interval)
        return fired
