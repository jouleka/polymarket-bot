"""Curated default news allowlist (POL-3 / S1).

The allowlist is the FIRST injection-defense gate: ``NewsPoller`` REFUSES any source not
listed here. Every URL below was VALIDATED live (fetched + parsed into items) on
2026-06-26. This is a conservative STARTER set, not exhaustive.

  >>> OPERATOR REVIEW REQUIRED <<<  The PRIMARY tier MAY inform trades (downstream still
  requires >= 2 independent primaries before non-tiny size), so adding a PRIMARY source
  is a TRUST + legal/ToS decision. Review/extend this before relying on it for sizing.

Tiers:
  PRIMARY   = agency / regulator / econ-release pages + wires. May inform trades.
  DISCOVERY = aggregator (Google News). Tagged so the ERS / Hermes NEVER lets it trigger
              a trade -- discovery + backtest ONLY; its legal/ToS posture is the
              operator's call.

Validation notes (2026-06-26) -- candidates that did NOT make the cut, and why:
  - Treasury press feed declares a DOCTYPE/ENTITY -> ``parse_feed`` refuses it (the XXE
    defense working as designed); not ingestible without resolving that upstream.
  - BLS (bls.gov) returns 403 to a bot user-agent; not fetchable read-only here.
  - WhiteHouse / uscourts / sec-litigation candidate URLs 404'd (feed paths move).
  - GDELT is JSON/CSV, not RSS/Atom -> it is the SEPARATE slow-path, not this poller.
  - Sports / league feeds: add per the markets you actually trade (operator knows which).
"""

from polybot.ingestion.news import DISCOVERY, PRIMARY, Source

DEFAULT_ALLOWLIST = (
    # --- PRIMARY: US financial regulators (relevant to crypto / finance markets) ---
    Source("fed-press", "https://www.federalreserve.gov/feeds/press_all.xml", PRIMARY),
    Source("fed-monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml", PRIMARY),  # FOMC statements
    Source("sec-press", "https://www.sec.gov/news/pressreleases.rss", PRIMARY),
    Source("cftc-press", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", PRIMARY),
    # --- PRIMARY: macro econ releases (GDP / personal income / PCE) ---
    Source("bea-news", "https://apps.bea.gov/rss/rss.xml", PRIMARY),
    # --- DISCOVERY: aggregator -- NEVER triggers a trade (legal/ToS is the operator's call) ---
    Source("google-news-top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", DISCOVERY),
)
