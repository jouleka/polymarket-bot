"""Canonical data models for the ingestion layer.

token_id is the decimal ERC-1155 id (-> CLOB book/price); it is kept as a string
because the values are ~77-digit integers and any int round-trip risks precision
loss in downstream JSON. conditionId is the hex id (-> trades/holders).
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Outcome:
    name: str          # "Yes" / "No"
    token_id: str      # decimal ERC-1155 id, kept as an exact string
    price: Decimal     # outcome price in [0, 1]


@dataclass(frozen=True)
class Market:
    condition_id: str          # hex conditionId
    question: str
    slug: str
    outcomes: tuple[Outcome, ...]
    active: bool
    closed: bool


@dataclass(frozen=True)
class Envelope:
    """Uniform wrapper around one external observation.

    trust is UNTRUSTED by default: ingested content is data, never instructions.
    observed_at is a monotonic ns stamp applied at receive time; published_at is
    the source's own (untrusted) wall-clock time, if any.
    """

    source: str
    source_tier: str
    event_id: str
    observed_at: int
    content: str
    published_at: int | None = None
    entities: tuple[str, ...] = ()
    market_links: tuple[str, ...] = ()
    trust: str = "UNTRUSTED"
