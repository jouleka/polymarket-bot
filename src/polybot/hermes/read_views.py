"""Sanitized read adapters for the propose-only Hermes facade."""

from __future__ import annotations

from types import SimpleNamespace

from polybot.ers.market_meta import MarketMetadataUnavailable
from polybot.ingestion.gamma import normalize_market


class ReadViewUnavailable(LookupError):
    """A sanitized view has no current trustworthy value."""


class MarketReadView:
    """Bounded projection over the current fixed-universe registry generation."""

    def __init__(self, registry_provider, *, default_limit=25, max_limit=50):
        if (isinstance(default_limit, bool) or not isinstance(default_limit, int)
                or isinstance(max_limit, bool) or not isinstance(max_limit, int)
                or default_limit <= 0 or max_limit <= 0 or default_limit > max_limit):
            raise ValueError("market view limits must be positive and bounded")
        self._provider = registry_provider
        self._default_limit = default_limit
        self._max_limit = max_limit

    def __call__(self, *, condition_id=None, token_id=None, offset=0, limit=None):
        for name, value in (("condition_id", condition_id), ("token_id", token_id)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"market {name} must be a non-empty exact string")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("market offset must be a non-negative integer")
        if limit is None:
            limit = self._default_limit
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or limit <= 0 or limit > self._max_limit):
            raise ValueError(f"market limit must be in [1, {self._max_limit}]")
        if condition_id is not None and token_id is not None:
            raise ValueError("select a market by condition_id or token_id, not both")

        registry = self._provider.require_fresh()
        rows = []
        for raw in self._provider.market_rows:
            market = normalize_market(raw)
            token_ids = tuple(outcome.token_id for outcome in market.outcomes)
            if condition_id is not None and market.condition_id != condition_id:
                continue
            if token_id is not None and token_id not in token_ids:
                continue
            event_links = raw.get("events")
            if (not isinstance(event_links, list) or len(event_links) != 1
                    or not isinstance(event_links[0], dict)):
                if condition_id is not None or token_id is not None:
                    raise ReadViewUnavailable(
                        "market event identity is unavailable"
                    )
                continue
            event_id = event_links[0].get("id")
            if not isinstance(event_id, str) or not event_id:
                if condition_id is not None or token_id is not None:
                    raise ReadViewUnavailable(
                        "market event identity is unavailable"
                    )
                continue
            identity = SimpleNamespace(
                condition_id=market.condition_id,
                token_id=token_ids[0],
                event_id=event_id,
            )
            try:
                metadata = registry.metadata_for(identity)
                subject = registry.resolution_subject_for(identity)
            except MarketMetadataUnavailable as exc:
                if condition_id is not None or token_id is not None:
                    raise ReadViewUnavailable(
                        "market registry metadata is unavailable"
                    ) from exc
                continue
            rows.append({
                "event_id": subject.event_id,
                "condition_id": market.condition_id,
                "category": metadata.category,
                "question": metadata.question_text,
                "seconds_to_resolution": metadata.seconds_to_resolution,
                "active": market.active,
                "closed": market.closed,
                "outcomes": [
                    {
                        "label": outcome.name,
                        "token_id": outcome.token_id,
                        "outcome_slot": index,
                    }
                    for index, outcome in enumerate(market.outcomes)
                ],
            })
        rows.sort(key=lambda item: (
            item["seconds_to_resolution"] == 0,
            item["seconds_to_resolution"],
            item["condition_id"],
        ))
        total = len(rows)
        return {
            "offset": offset,
            "limit": limit,
            "total": total,
            "markets": rows[offset:offset + limit],
        }


class BookReadView:
    """Exact top-of-book projection over POL-17's shared live books."""

    def __init__(self, book_for, *, token_ids):
        if not callable(book_for):
            raise TypeError("book_for must be callable")
        tokens = tuple(token_ids)
        if (not tokens or len(set(tokens)) != len(tokens)
                or any(not isinstance(token, str) or not token for token in tokens)):
            raise ValueError("book view tokens must be non-empty unique exact strings")
        self._book_for = book_for
        self._token_ids = frozenset(tokens)

    def __call__(self, *, token_id):
        if not isinstance(token_id, str) or not token_id or token_id not in self._token_ids:
            raise ReadViewUnavailable("book token is outside the selected universe")
        book = self._book_for(token_id)
        if book is None or book.is_stale():
            raise ReadViewUnavailable("live book is unavailable or stale")
        bid, bid_size, ask, ask_size = book.top_of_book()
        midpoint = book.midpoint()
        if bid is None or bid_size is None or ask is None or ask_size is None or midpoint is None:
            raise ReadViewUnavailable("live book has no usable two-sided midpoint")
        return {
            "token_id": token_id,
            "best_bid": str(bid),
            "bid_size": str(bid_size),
            "best_ask": str(ask),
            "ask_size": str(ask_size),
            "midpoint": str(midpoint),
            "stale": False,
        }


class LedgerReadView:
    """Bounded resolved-only calibration history."""

    def __init__(self, ledger, *, categories, max_limit=100):
        category_values = tuple(categories)
        if (not category_values or len(set(category_values)) != len(category_values)
                or any(not isinstance(value, str) or not value for value in category_values)):
            raise ValueError("ledger categories must be non-empty unique exact strings")
        if (isinstance(max_limit, bool) or not isinstance(max_limit, int)
                or max_limit <= 0):
            raise ValueError("ledger maximum limit must be a positive integer")
        self._ledger = ledger
        self._categories = frozenset(category_values)
        self._max_limit = max_limit

    def __call__(self, *, category=None, limit=25):
        if category is not None and category not in self._categories:
            raise ValueError("ledger category is not reviewed")
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or limit <= 0 or limit > self._max_limit):
            raise ValueError(f"ledger limit must be in [1, {self._max_limit}]")
        selected = self._ledger.resolved(category, limit=limit)
        return {
            "category": category,
            "limit": limit,
            "records": [self._project(row) for row in selected],
        }

    @staticmethod
    def _project(row):
        return {
            "forecast_id": row.forecast_id,
            "category": row.category,
            "condition_id": row.condition_id,
            "p": str(row.p),
            "market_mid": str(row.market_mid),
            "created_at": row.created_at,
            "resolution_status": row.resolution_status,
            "resolved_at": row.resolved_at,
            "event_id": row.event_id,
            "token_id": row.token_id,
            "outcome_slot": row.outcome_slot,
            "sibling_token_ids": (
                None if row.sibling_token_ids is None else list(row.sibling_token_ids)
            ),
            "resolution_value": (
                None if row.resolution_value is None else str(row.resolution_value)
            ),
            "resolution_numerator": row.resolution_numerator,
            "resolution_denominator": row.resolution_denominator,
            "terminal_id": row.terminal_id,
        }


class FlagsReadView:
    """Conservative operational facts; never an authorization to trade."""

    def __init__(self, *, runtime_ready, controller_state, resolution_state,
                 registry_fresh, live_book_tokens):
        readers = (
            runtime_ready, controller_state, resolution_state,
            registry_fresh, live_book_tokens,
        )
        if any(not callable(reader) for reader in readers):
            raise TypeError("flag view inputs must be read-only callables")
        self._runtime_ready = runtime_ready
        self._controller_state = controller_state
        self._resolution_state = resolution_state
        self._registry_fresh = registry_fresh
        self._live_book_tokens = live_book_tokens

    def __call__(self):
        state = self._resolution_state()
        runtime_ready = self._runtime_ready()
        registry_fresh = self._registry_fresh()
        if not isinstance(runtime_ready, bool) or not isinstance(registry_fresh, bool):
            raise TypeError("runtime and registry health values must be boolean")
        return {
            "runtime_ready": runtime_ready,
            "controller_state": self._controller_state(),
            "registry_fresh": registry_fresh,
            "live_book_tokens": sorted(self._live_book_tokens()),
            "terminal_condition_ids": sorted(state.terminal_condition_ids),
            "frozen_condition_ids": sorted(state.frozen_condition_ids),
            "detectors": {
                "available": False,
                "action": "FLAG_ONLY",
                "reasons": ["live_detector_inputs_unavailable"],
            },
            "trading_permission": False,
        }
