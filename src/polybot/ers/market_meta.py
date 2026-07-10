"""Market metadata seam (S6 / POL-8 stub; POL-14 real registry policy).

The legacy stub remains an explicit paper-only fixture. POL-14 adds the immutable result and the
reviewed Gamma tag-ID classification policy first; the strict two-snapshot registry is built in the
following TDD slices.
"""
from collections.abc import Mapping
from dataclasses import dataclass


# Fixed at 10**9 (about 31.7 years): deliberately enormous and strictly greater than the default
# prior_decay_window_seconds=86_400. Kept as a named constant so the invariant is pinned by tests.
# "Past the decay window" keeps the prior anchor active (the prior is only dropped WITHIN the
# window). The real per-market value is supplied by MarketRegistry.
SECONDS_TO_RESOLUTION_SENTINEL = 1_000_000_000

# The single legacy category bucket. The real registry never maps unavailable provider data here.
UNKNOWN_CATEGORY = "unknown"


@dataclass(frozen=True)
class MarketMetadata:
    """One internally-consistent metadata lookup result consumed by the ERS."""

    category: str
    question_text: str
    seconds_to_resolution: int


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


class StubMarketMeta:
    """Legacy MVP metadata fixture. Production composition must use ``MarketRegistry``.

    ``unknown`` keeps a cold calibration bucket at k=0 in historical tests. The proposal summary
    and large sentinel preserve the original S6 behavior until the real registry is explicitly
    injected; there is no implicit production fallback.
    """

    def category_for(self, intent):
        return UNKNOWN_CATEGORY

    def question_text_for(self, intent):
        return intent.resolution_summary

    def seconds_to_resolution_for(self, intent):
        return SECONDS_TO_RESOLUTION_SENTINEL
