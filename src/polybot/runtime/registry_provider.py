"""Immutable POL-14 registry generations for one fixed websocket universe."""

from __future__ import annotations

import json
import math

from polybot.ers.market_meta import MarketRegistry, MarketSnapshotError


class RegistryRefreshUnavailable(RuntimeError):
    """A transient Gamma transport/server failure with no new snapshot authority."""


def _snapshot_identity(market_rows):
    identities = {}
    token_order = []
    for row in market_rows:
        try:
            condition_id = row["conditionId"]
            raw_tokens = row["clobTokenIds"]
            tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
            tokens = tuple(tokens)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MarketSnapshotError("Gamma fixed universe identity is malformed") from exc
        if (not isinstance(condition_id, str) or not condition_id
                or len(tokens) != 2
                or any(not isinstance(token, str) or not token for token in tokens)
                or tokens[0] == tokens[1]):
            raise MarketSnapshotError("Gamma fixed universe identity is malformed")
        previous = identities.get(condition_id)
        if previous is not None and previous != tokens:
            raise MarketSnapshotError("Gamma fixed universe condition identity conflicts")
        identities[condition_id] = tokens
        for token in tokens:
            if token not in token_order:
                token_order.append(token)
    return identities, tuple(token_order)


class FixedUniverseRegistryProvider:
    """Build complete registry replacements without changing subscribed identities."""

    def __init__(self, *, fetch_snapshot, wall_clock, age_clock, max_age_seconds):
        if (type(max_age_seconds) not in (int, float)
                or not math.isfinite(max_age_seconds) or max_age_seconds <= 0):
            raise ValueError("max_age_seconds must be finite and > 0")
        self._fetch_snapshot = fetch_snapshot
        self._wall_clock = wall_clock
        self._age_clock = age_clock
        self._max_age_seconds = max_age_seconds
        self._registry = None
        self._identity = None
        self._token_ids = ()
        self._market_rows = ()
        self._loaded_at = None

    def load(self):
        if self._registry is not None:
            raise RuntimeError("fixed universe registry is already loaded")
        return self._replace(initial=True)

    def refresh(self):
        if self._registry is None:
            raise RuntimeError("fixed universe registry must load before refresh")
        return self._replace(initial=False)

    def require_fresh(self):
        """Return the last coherent generation only while its age is trustworthy."""
        if self._registry is None or self._loaded_at is None:
            raise MarketSnapshotError("Gamma registry is not loaded")
        now = self._age_clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or type(self._loaded_at) not in (int, float)
                or not math.isfinite(self._loaded_at)
                or now < self._loaded_at
                or now - self._loaded_at > self._max_age_seconds):
            raise MarketSnapshotError("Gamma registry is stale")
        return self._registry

    def _replace(self, *, initial):
        market_rows, event_rows = self._fetch_snapshot()
        candidate = MarketRegistry.from_gamma_snapshots(
            market_rows, event_rows, clock=self._wall_clock
        )
        identity, token_ids = _snapshot_identity(market_rows)
        if not initial and identity != self._identity:
            raise MarketSnapshotError("Gamma refresh changed the fixed universe")
        self._registry = candidate
        if initial:
            self._identity = identity
            self._token_ids = token_ids
        self._market_rows = tuple(market_rows)
        self._loaded_at = self._age_clock()
        return candidate

    @property
    def registry(self):
        if self._registry is None:
            raise RuntimeError("fixed universe registry is not loaded")
        return self._registry

    @property
    def condition_ids(self):
        return frozenset() if self._identity is None else frozenset(self._identity)

    @property
    def token_ids(self):
        return self._token_ids

    @property
    def market_rows(self):
        if self._registry is None:
            raise RuntimeError("fixed universe registry is not loaded")
        return self._market_rows
