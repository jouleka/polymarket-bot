"""Compact point-in-time midpoint batches for downsampled market memory."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import asyncio
import json
import math

from polybot.core.models import Envelope


MIDPOINT_SOURCE = "clob-midpoint"
MIDPOINT_SCHEMA = 1


@dataclass(frozen=True)
class MidpointQuote:
    bid: Decimal
    ask: Decimal
    midpoint: Decimal


def decode_midpoint_batch(content: str) -> dict[str, MidpointQuote]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("midpoint batch must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("midpoint batch must be an object")
    if set(payload) != {"schema", "books"}:
        raise ValueError("midpoint batch keys must be exactly schema and books")
    if type(payload["schema"]) is not int or payload["schema"] != MIDPOINT_SCHEMA:
        raise ValueError(f"unsupported midpoint batch schema: {payload['schema']!r}")
    if not isinstance(payload["books"], dict):
        raise ValueError("midpoint batch books must be an object")
    decoded = {}
    for token, quote in sorted(payload["books"].items()):
        if not isinstance(token, str) or not token:
            raise ValueError("midpoint batch token IDs must be non-empty strings")
        if not isinstance(quote, dict) or set(quote) != {"bid", "ask", "mid"}:
            raise ValueError(f"midpoint quote keys invalid for token {token!r}")
        if any(not isinstance(quote[name], str) for name in ("bid", "ask", "mid")):
            raise ValueError(f"midpoint quote prices must be strings for token {token!r}")
        try:
            bid = Decimal(quote["bid"])
            ask = Decimal(quote["ask"])
            midpoint = Decimal(quote["mid"])
        except InvalidOperation as exc:
            raise ValueError(f"midpoint quote prices invalid for token {token!r}") from exc
        if not all(value.is_finite() for value in (bid, ask, midpoint)):
            raise ValueError(f"midpoint quote prices must be finite for token {token!r}")
        if not (Decimal(0) <= bid < ask <= Decimal(1)):
            raise ValueError(f"midpoint quote prices outside valid domain for token {token!r}")
        if midpoint != (bid + ask) / 2:
            raise ValueError(f"midpoint does not match bid/ask for token {token!r}")
        decoded[token] = MidpointQuote(bid, ask, midpoint)
    return decoded


class MidpointSnapshotter:
    def __init__(self, *, token_ids, book_for, stamper, writer,
                 interval_seconds: float = 60.0, sleep=asyncio.sleep):
        ids = tuple(token_ids)
        if (not ids or any(not isinstance(token_id, str) or not token_id for token_id in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("token_ids must be non-empty, unique, non-empty strings")
        if (type(interval_seconds) not in (int, float)
                or not math.isfinite(interval_seconds) or interval_seconds <= 0):
            raise ValueError("interval_seconds must be finite and > 0")
        self._token_ids = tuple(sorted(ids))
        self._book_for = book_for
        self._stamper = stamper
        self._writer = writer
        self._interval_seconds = interval_seconds
        self._sleep = sleep

    def snapshot_once(self) -> int:
        observed_at = self._stamper.stamp()
        books = {}
        for token_id in self._token_ids:
            book = self._book_for(token_id)
            if book is None:
                continue
            midpoint = book.midpoint()
            if midpoint is None:
                continue
            books[token_id] = {
                "bid": str(book.best_bid()),
                "ask": str(book.best_ask()),
                "mid": str(midpoint),
            }
        content = json.dumps(
            {"schema": MIDPOINT_SCHEMA, "books": books},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._writer.append(Envelope(
            source=MIDPOINT_SOURCE,
            source_tier="VENUE",
            event_id=f"batch:{observed_at}",
            observed_at=observed_at,
            content=content,
            published_at=None,
            market_links=tuple(books),
        ))
        return len(books)
