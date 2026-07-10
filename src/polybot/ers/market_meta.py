"""Market metadata seam (S6 / POL-8 stub; POL-14 real registry policy).

The legacy stub remains an explicit paper-only fixture. POL-14 adds the immutable result, reviewed
Gamma tag-ID policy, strict two-snapshot identity reconciliation, and fail-closed registry.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
from numbers import Real
import re
import time
from types import MappingProxyType
from typing import Any


# Fixed at 10**9 (about 31.7 years): deliberately enormous and strictly greater than the default
# prior_decay_window_seconds=86_400. Kept as a named constant so the invariant is pinned by tests.
# "Past the decay window" keeps the prior anchor active (the prior is only dropped WITHIN the
# window). The real per-market value is supplied by MarketRegistry.
SECONDS_TO_RESOLUTION_SENTINEL = 1_000_000_000

# The single legacy category bucket. The real registry never maps unavailable provider data here.
UNKNOWN_CATEGORY = "unknown"

# datetime.fromisoformat intentionally accepts any single Unicode date/time separator. Gamma's
# provider boundary is narrower: canonical RFC3339 with `T` and an explicit offset only.
_RFC3339 = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)


@dataclass(frozen=True)
class MarketMetadata:
    """One internally-consistent metadata lookup result consumed by the ERS."""

    category: str
    question_text: str
    seconds_to_resolution: int

    def __post_init__(self):
        if not isinstance(self.category, str) or not self.category:
            raise TypeError("market metadata category must be a non-empty string")
        if not isinstance(self.question_text, str):
            raise TypeError("market metadata question must be a string")
        if isinstance(self.seconds_to_resolution, bool) or not isinstance(
                self.seconds_to_resolution, int):
            raise TypeError("market metadata seconds must be an integer")
        if self.seconds_to_resolution < 0:
            raise ValueError("market metadata seconds must be non-negative")


@dataclass(frozen=True)
class CategoryPolicy:
    """Immutable allowlist from reviewed Gamma tag IDs to canonical calibration categories.

    Gamma labels/slugs are descriptive and mutable; only exact string IDs participate. The explicit
    precedence resolves events carrying more than one reviewed broad tag and is therefore a
    safety-relevant, test-pinned policy surface.
    """

    precedence: tuple[str, ...]
    tag_ids_by_category: tuple[tuple[str, frozenset[str]], ...]

    def __post_init__(self):
        if not isinstance(self.precedence, tuple) or not self.precedence:
            raise ValueError("category precedence must be a non-empty tuple")
        if any(not isinstance(name, str) or not name for name in self.precedence):
            raise ValueError("category precedence names must be non-empty strings")
        if len(set(self.precedence)) != len(self.precedence):
            raise ValueError("category precedence names must be unique")
        if not isinstance(self.tag_ids_by_category, tuple):
            raise ValueError("tag_ids_by_category must be a tuple")

        pairs = dict(self.tag_ids_by_category)
        if len(pairs) != len(self.tag_ids_by_category):
            raise ValueError("category policy entries must be unique")
        if set(pairs) != set(self.precedence):
            raise ValueError("category policy entries must exactly match precedence")

        seen: set[str] = set()
        for category in self.precedence:
            tag_ids = pairs[category]
            if not isinstance(tag_ids, frozenset) or not tag_ids:
                raise ValueError(f"tag IDs for {category!r} must be a non-empty frozenset")
            for tag_id in tag_ids:
                if not isinstance(tag_id, str) or not tag_id:
                    raise ValueError(f"tag IDs for {category!r} must be non-empty strings")
                if tag_id in seen:
                    raise ValueError(f"Gamma tag ID {tag_id!r} appears in multiple categories")
                seen.add(tag_id)

    def classify(self, tags):
        """Return the highest-precedence category represented in a live Gamma tag list.

        Unknown reviewed IDs return ``None``. Malformed wire types fail loudly rather than letting a
        label, slug, numeric coercion, or partial object silently activate a category.
        """
        if not isinstance(tags, list):
            raise TypeError(f"Gamma event tags must be a list, got {type(tags).__name__}")
        observed: set[str] = set()
        for tag in tags:
            if not isinstance(tag, Mapping):
                raise TypeError(f"Gamma tag must be a mapping, got {type(tag).__name__}")
            if "id" not in tag:
                raise ValueError("Gamma tag is missing tag id")
            tag_id = tag["id"]
            if not isinstance(tag_id, str) or not tag_id:
                raise TypeError(f"Gamma tag id must be a non-empty string, got {tag_id!r}")
            observed.add(tag_id)

        by_category = dict(self.tag_ids_by_category)
        for category in self.precedence:
            if observed & by_category[category]:
                return category
        return None


DEFAULT_CATEGORY_POLICY = CategoryPolicy(
    precedence=(
        "sports", "geopolitics", "politics", "crypto", "finance",
        "econ", "tech", "culture", "weather",
    ),
    tag_ids_by_category=(
        ("sports", frozenset({"1"})),
        ("geopolitics", frozenset({"100265"})),
        ("politics", frozenset({"2"})),
        ("crypto", frozenset({"21"})),
        ("finance", frozenset({"120", "107"})),
        ("econ", frozenset({"100328", "159", "225"})),
        ("tech", frozenset({"1401", "439"})),
        ("culture", frozenset({"596"})),
        ("weather", frozenset({"84"})),
    ),
)


class MarketSnapshotError(ValueError):
    """A Gamma snapshot cannot be interpreted without weakening the metadata contract."""


class MarketMetadataUnavailable(LookupError):
    """The requested condition/token pair has no safe metadata definition in this snapshot."""


@dataclass(frozen=True)
class _MarketDefinition:
    condition_id: str
    token_ids: tuple[str, str]
    event_id: str
    category: str | None
    question_text: str
    end_epoch: float


@dataclass(frozen=True)
class _EventDefinition:
    category: str | None
    # Only selected conditions are retained; tuple form is deeply immutable and deterministic.
    market_token_ids: tuple[tuple[str, tuple[str, str]], ...]


def _required_string(row, field, context):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MarketSnapshotError(
            f"Gamma {context} {field} must be a non-empty string, got {value!r}"
        )
    return value


def _parse_tokens(raw, condition_id):
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise MarketSnapshotError(
                f"Gamma market {condition_id} clobTokenIds is not valid JSON"
            ) from exc
    else:
        values = raw
    if not isinstance(values, list):
        raise MarketSnapshotError(
            f"Gamma market {condition_id} clobTokenIds must decode to a token list"
        )
    if len(values) != 2:
        raise MarketSnapshotError(
            f"Gamma market {condition_id} token list must contain exactly two IDs"
        )
    if any(not isinstance(token, str) or not token for token in values):
        raise MarketSnapshotError(
            f"Gamma market {condition_id} token IDs must be non-empty strings"
        )
    if values[0] == values[1]:
        raise MarketSnapshotError(f"Gamma market {condition_id} token IDs must be distinct")
    return values[0], values[1]


def _parse_deadline(raw, condition_id):
    if not isinstance(raw, str) or _RFC3339.fullmatch(raw) is None:
        raise MarketSnapshotError(
            f"Gamma market {condition_id} endDate must be a strict offset-aware RFC3339 string"
        )
    value = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MarketSnapshotError(
            f"Gamma market {condition_id} endDate is not valid RFC3339: {raw!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketSnapshotError(
            f"Gamma market {condition_id} endDate must include a UTC offset"
        )
    epoch = parsed.timestamp()
    if not math.isfinite(epoch):
        raise MarketSnapshotError(f"Gamma market {condition_id} endDate is non-finite")
    return epoch


def _event_id_for_market(row, condition_id):
    relations = row.get("events")
    if not isinstance(relations, list) or len(relations) != 1:
        raise MarketSnapshotError(
            f"Gamma market {condition_id} event link must contain exactly one event"
        )
    relation = relations[0]
    if not isinstance(relation, Mapping):
        raise MarketSnapshotError(f"Gamma market {condition_id} event link must be a mapping")
    return _required_string(relation, "id", f"market {condition_id} event")


@dataclass(frozen=True, repr=False)
class MarketRegistry:
    """Immutable condition+token metadata registry built from injected Gamma snapshots.

    Construction parses the selected market snapshot first, then accepts category metadata only when
    the event snapshot independently contains the same condition and exact sibling token pair.
    Stored mappings are read-only; refresh builds a complete replacement registry.
    """

    _by_condition: Mapping[str, _MarketDefinition]
    _by_token: Mapping[str, _MarketDefinition]
    _unavailable_conditions: frozenset[str]
    _unavailable_tokens: frozenset[str]
    _clock: Any

    @classmethod
    def from_gamma_snapshots(cls, market_rows, event_rows, *, clock=None,
                             category_policy=DEFAULT_CATEGORY_POLICY):
        if not isinstance(market_rows, list):
            raise MarketSnapshotError(
                f"Gamma market snapshot must be a list, got {type(market_rows).__name__}"
            )
        if not isinstance(event_rows, list):
            raise MarketSnapshotError(
                f"Gamma event snapshot must be a list, got {type(event_rows).__name__}"
            )
        if not isinstance(category_policy, CategoryPolicy):
            raise MarketSnapshotError("category_policy must be a CategoryPolicy")

        # Parse and de-duplicate the selected market snapshot before consulting event metadata.
        # Category remains unavailable until the second snapshot proves the event relationship.
        all_by_condition: dict[str, _MarketDefinition] = {}
        all_by_token: dict[str, _MarketDefinition] = {}
        for index, row in enumerate(market_rows):
            if not isinstance(row, Mapping):
                raise MarketSnapshotError(
                    f"Gamma market row {index} must be a mapping, got {type(row).__name__}"
                )
            condition_id = _required_string(row, "conditionId", f"market row {index}")
            question = _required_string(row, "question", f"market {condition_id}")
            tokens = _parse_tokens(row.get("clobTokenIds"), condition_id)
            event_id = _event_id_for_market(row, condition_id)
            deadline = _parse_deadline(row.get("endDate"), condition_id)
            definition = _MarketDefinition(
                condition_id=condition_id,
                token_ids=tokens,
                event_id=event_id,
                category=None,
                question_text=question,
                end_epoch=deadline,
            )

            previous = all_by_condition.get(condition_id)
            if previous is not None and previous != definition:
                raise MarketSnapshotError(f"Gamma condition {condition_id} definition conflict")
            all_by_condition[condition_id] = definition
            for token_id in tokens:
                token_owner = all_by_token.get(token_id)
                if token_owner is not None and token_owner.condition_id != condition_id:
                    raise MarketSnapshotError(
                        f"Gamma token {token_id} maps to multiple conditions: "
                        f"{token_owner.condition_id}, {condition_id}"
                    )
                all_by_token[token_id] = definition

        conditions_by_event: dict[str, set[str]] = {}
        for definition in all_by_condition.values():
            conditions_by_event.setdefault(definition.event_id, set()).add(definition.condition_id)

        # Parse only relationship details for selected conditions. Gamma events legitimately contain
        # unrelated legacy markets with absent CLOB IDs; those cannot authorize a selected market and
        # are ignored. The referenced event itself and every matching relationship remain strict.
        event_definitions: dict[str, _EventDefinition] = {}
        for index, row in enumerate(event_rows):
            if not isinstance(row, Mapping):
                raise MarketSnapshotError(
                    f"Gamma event row {index} must be a mapping, got {type(row).__name__}"
                )
            event_id = _required_string(row, "id", f"event row {index}")
            selected_conditions = conditions_by_event.get(event_id)
            if selected_conditions is None:
                continue
            try:
                category = category_policy.classify(row.get("tags"))
            except (TypeError, ValueError) as exc:
                raise MarketSnapshotError(f"Gamma event {event_id} tags are invalid: {exc}") from exc

            embedded_rows = row.get("markets")
            if not isinstance(embedded_rows, list):
                raise MarketSnapshotError(
                    f"Gamma event {event_id} markets must be a market list"
                )
            selected_tokens: dict[str, tuple[str, str]] = {}
            for embedded_index, embedded in enumerate(embedded_rows):
                if not isinstance(embedded, Mapping):
                    raise MarketSnapshotError(
                        f"Gamma event {event_id} market row {embedded_index} must be a mapping"
                    )
                embedded_condition = embedded.get("conditionId")
                if embedded_condition not in selected_conditions:
                    continue
                tokens = _parse_tokens(embedded.get("clobTokenIds"), embedded_condition)
                previous_tokens = selected_tokens.get(embedded_condition)
                if previous_tokens is not None and previous_tokens != tokens:
                    raise MarketSnapshotError(
                        f"Gamma event {event_id} condition {embedded_condition} "
                        "token identity conflict"
                    )
                selected_tokens[embedded_condition] = tokens

            event_definition = _EventDefinition(
                category=category,
                market_token_ids=tuple(sorted(selected_tokens.items())),
            )
            previous_event = event_definitions.get(event_id)
            if previous_event is not None and previous_event != event_definition:
                raise MarketSnapshotError(f"Gamma event {event_id} definition conflict")
            event_definitions[event_id] = event_definition

        # Reconcile the two snapshots. Missing event/relationship/category data leaves only that
        # market unavailable. Contradictory token identities are provider corruption and halt the
        # whole construction rather than exposing a partially trusted generation.
        reconciled_by_condition: dict[str, _MarketDefinition] = {}
        for condition_id, definition in all_by_condition.items():
            event_definition = event_definitions.get(definition.event_id)
            category = None
            if event_definition is not None:
                event_tokens = dict(event_definition.market_token_ids).get(condition_id)
                if event_tokens is not None:
                    if event_tokens != definition.token_ids:
                        raise MarketSnapshotError(
                            f"Gamma event {definition.event_id} token identity conflict "
                            f"for condition {condition_id}"
                        )
                    category = event_definition.category
            reconciled_by_condition[condition_id] = replace(definition, category=category)

        all_by_condition = reconciled_by_condition
        all_by_token = {
            token_id: definition
            for definition in all_by_condition.values()
            for token_id in definition.token_ids
        }
        by_condition = {
            condition_id: definition
            for condition_id, definition in all_by_condition.items()
            if definition.category is not None
        }
        if not by_condition:
            raise MarketSnapshotError("Gamma snapshots contain no usable categorized market")
        by_token = {
            token_id: definition
            for token_id, definition in all_by_token.items()
            if definition.category is not None
        }
        unavailable_conditions = frozenset(set(all_by_condition) - set(by_condition))
        unavailable_tokens = frozenset(
            token_id for token_id, definition in all_by_token.items()
            if definition.category is None
        )
        return cls(
            MappingProxyType(dict(by_condition)),
            MappingProxyType(dict(by_token)),
            unavailable_conditions,
            unavailable_tokens,
            time.time if clock is None else clock,
        )

    def __len__(self):
        return len(self._by_condition)

    def metadata_for(self, intent):
        """Resolve one intent by BOTH trusted identity keys and one injected wall-clock read."""
        condition_id = getattr(intent, "condition_id", None)
        token_id = getattr(intent, "token_id", None)
        if not isinstance(condition_id, str) or not condition_id:
            raise MarketMetadataUnavailable("market condition identifier is unavailable")
        if not isinstance(token_id, str) or not token_id:
            raise MarketMetadataUnavailable("market token identifier is unavailable")

        condition_definition = self._by_condition.get(condition_id)
        if condition_definition is None:
            state = "unavailable" if condition_id in self._unavailable_conditions else "unknown"
            raise MarketMetadataUnavailable(f"market condition {condition_id!r} is {state}")
        token_definition = self._by_token.get(token_id)
        if token_definition is None:
            state = "unavailable" if token_id in self._unavailable_tokens else "unknown"
            raise MarketMetadataUnavailable(f"market token {token_id!r} is {state}")
        if condition_definition is not token_definition:
            raise MarketMetadataUnavailable(
                f"market condition/token identity mismatch: {condition_id!r}, {token_id!r}"
            )

        try:
            now = self._clock()
        except Exception as exc:
            raise MarketMetadataUnavailable("market metadata wall clock failed") from exc
        if isinstance(now, bool) or not isinstance(now, Real) or not math.isfinite(now):
            raise MarketMetadataUnavailable(
                f"market metadata wall clock must be finite real seconds, got {now!r}"
            )
        seconds = max(0, math.floor(condition_definition.end_epoch - float(now)))
        category = condition_definition.category
        if category is None:  # defensive invariant: unavailable rows never enter the public indices
            raise MarketMetadataUnavailable("market category is unavailable")
        return MarketMetadata(
            category=category,
            question_text=condition_definition.question_text,
            seconds_to_resolution=seconds,
        )


class StubMarketMeta:
    """Legacy MVP metadata fixture. Production composition must use ``MarketRegistry``.

    ``unknown`` keeps a cold calibration bucket at k=0 in historical tests. The proposal summary
    and large sentinel preserve the original S6 behavior until the real registry is explicitly
    injected; there is no implicit production fallback.
    """

    def metadata_for(self, intent):
        return MarketMetadata(
            category=UNKNOWN_CATEGORY,
            question_text=intent.resolution_summary,
            seconds_to_resolution=SECONDS_TO_RESOLUTION_SENTINEL,
        )

    def category_for(self, intent):
        return self.metadata_for(intent).category

    def question_text_for(self, intent):
        return self.metadata_for(intent).question_text

    def seconds_to_resolution_for(self, intent):
        return self.metadata_for(intent).seconds_to_resolution
