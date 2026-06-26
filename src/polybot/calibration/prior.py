"""Base-rate prior engine (S5 / POL-7).

A small curated set of reference-class base rates + a keyword classifier, with a conservative
longshot/favorite shrink (extreme priors regressed toward 0.5). v1 ships curated seed priors;
empirical priors from the Market-Memory resolved history are deferred (the resolved set is thin).

The shrink is a documented, operator-tunable PLACEHOLDER for an empirically-fit favorite-longshot
curve -- regression-to-the-mean keeps the prior from being overconfident in an extreme reference
rate (anti-overconfidence). OPERATOR REVIEW REQUIRED before priors inform trades (a trust call,
like the news allowlist).
"""

from decimal import Decimal

DEFAULT_REFERENCE_CLASSES = {
    "incumbent_reelection": Decimal("0.90"),
    "scheduled_fed_hold": Decimal("0.97"),
    "favorite_by_large_spread": Decimal("0.85"),
}

# First keyword found (in insertion order) wins -- so operators MUST order keywords by priority
# when two could co-occur in one market's text. Operator-tunable; the market->class mapping is
# intentionally simple at v1 (a real semantic classifier is S6-adjacent).
DEFAULT_KEYWORDS = {
    "incumbent": "incumbent_reelection",
    "re-elect": "incumbent_reelection",
    "fed hold": "scheduled_fed_hold",
    "rate unchanged": "scheduled_fed_hold",
    "favorite": "favorite_by_large_spread",
}

_HALF = Decimal("0.5")


class PriorEngine:
    def __init__(self, reference_classes=None, keyword_map=None, longshot_lambda=Decimal("0.9")):
        self._classes = dict(DEFAULT_REFERENCE_CLASSES if reference_classes is None else reference_classes)
        self._keywords = dict(DEFAULT_KEYWORDS if keyword_map is None else keyword_map)
        self._lambda = longshot_lambda

    def base_rate(self, reference_class):
        """The longshot-shrunk base rate for a reference class, or None if the class is unknown."""
        raw = self._classes.get(reference_class)
        if raw is None:
            return None
        return _HALF + self._lambda * (raw - _HALF)  # regress extremes toward 0.5

    def classify(self, text):
        """The first reference class whose keyword appears in ``text`` (case-insensitive), else None."""
        lowered = text.lower()
        for keyword, reference_class in self._keywords.items():
            if keyword in lowered:
                return reference_class
        return None

    def prior_for(self, text):
        """End-to-end: classify the market text to a reference class, then return its (shrunk) base
        rate. None when no class matches (the Anchor Gate then falls back to market-only anchoring)."""
        reference_class = self.classify(text)
        return None if reference_class is None else self.base_rate(reference_class)
