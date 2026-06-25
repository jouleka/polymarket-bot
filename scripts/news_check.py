"""Live read-only check: the news fast-path ingests real PRIMARY feeds, sanitized.

Polls a small example allowlist of government primary sources (the operator curates
the full list), parses RSS/Atom, sanitizes each item, and persists UNTRUSTED
envelopes. Read-only. Run manually:
    ./.venv/bin/python scripts/news_check.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.news import PRIMARY, NewsPoller, Source
from polybot.ingestion.transport import make_text_fetch
from polybot.storage.market_memory import EventStore

# Example PRIMARY allowlist (live-reachable government primary feeds). The operator
# curates the real allowlist; DISCOVERY sources (aggregator/GDELT) are added separately.
ALLOWLIST = [
    Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", PRIMARY),
    Source("sec-press", "https://www.sec.gov/news/pressreleases.rss", PRIMARY),
]


async def main():
    fetch = make_text_fetch()
    with EventStore(tempfile.mktemp(suffix=".db")) as store:
        poller = NewsPoller(fetch, MonotonicStamper(), store, allowlist=ALLOWLIST)
        for src in ALLOWLIST:
            try:
                n = await poller.poll_source(src.name)
                print(f"{src.name} ({src.tier}): {n} items")
            except Exception as exc:
                print(f"{src.name}: ERROR {exc!r}")

        rows = store.all()
        untrusted = all(r.trust == "UNTRUSTED" for r in rows)
        spotlit = all("UNTRUSTED" in r.content for r in rows)
        no_invisible = all(("​" not in r.content and "‮" not in r.content
                            and "‭" not in r.content) for r in rows)
        with_pub = sum(1 for r in rows if r.published_at)
        monotonic = all(b > a for a, b in zip([r.observed_at for r in rows],
                                              [r.observed_at for r in rows][1:]))
        print(f"\npersisted {len(rows)} UNTRUSTED news envelopes; {with_pub} with a parsed published time")
        for r in rows[:4]:
            lines = r.content.splitlines()
            head = lines[1] if len(lines) > 1 else r.content
            print(f"  [{r.source}/{r.source_tier}] pub={r.published_at} {head[:84]}")
        ok = len(rows) > 0 and untrusted and spotlit and no_invisible and monotonic
        print(f"\nRESULT: {'PASS' if ok else 'CHECK'} "
              f"(untrusted={untrusted}, spotlit={spotlit}, no-invisible-chars={no_invisible}, "
              f"observed_at monotonic={monotonic})")


if __name__ == "__main__":
    asyncio.run(main())
