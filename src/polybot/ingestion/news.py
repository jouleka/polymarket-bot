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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.sanitizer import neutralize, sanitize

PRIMARY = "PRIMARY"
DISCOVERY = "DISCOVERY"


class Source:
    """An allowlisted news feed: a stable name, a URL, and a trust tier."""

    def __init__(self, name, url, tier, kind="rss"):
        if tier not in (PRIMARY, DISCOVERY):
            raise ValueError(f"unknown news tier: {tier!r}")
        self.name = name
        self.url = url
        self.tier = tier
        self.kind = kind


def _local(tag):
    return tag.rsplit("}", 1)[-1]  # drop any XML namespace -> local name


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
    def __init__(self, fetch, stamper, store, allowlist, *, sanitizer=sanitize):
        self._fetch = fetch  # async (url) -> feed text
        self._stamper = stamper
        self._store = store
        self._sanitizer = sanitizer
        self._allowlist = {s.name: s for s in allowlist}

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
        for item in items:
            guid = neutralize(item["guid"])
            if not guid:
                continue  # no stable id -> skip (cannot dedup / reference it)
            free_text = f"{item['title']}\n{item['summary']}".strip()
            self._store.append(make_envelope(
                self._stamper,
                source=source.name,
                source_tier=source.tier,
                event_id=guid,
                content=self._sanitizer(free_text),     # spotlighted, invisible chars stripped
                published_at=_epoch(item["published"]),
                entities=(neutralize(item["link"]),) if item["link"] else (),
            ))
            persisted += 1
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
