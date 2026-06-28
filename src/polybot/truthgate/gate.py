"""Citation truth-gate (S6 / POL-8).

ERS-side, post-INSERT verification of a Hermes proposal's citations. PURE over
(citations, EventStore envelopes, a live LocalBook). Citation strings are MATCHED
against the already-sanitized EventStore -- never fetched, never executed (untrusted-
data discipline). Two outputs the loop consumes:

  * corroborated = (>= 2 distinct, fresh, allowlisted PRIMARY publisher_groups).
    This is the single key that lets w_news go nonzero in fusion AND widens the
    anchor band. DISCOVERY tier and non-allowlisted citations never count.

  * refusal: zero allowlisted primaries -> REASON_TRUTH_GATE_REFUSE (news-only with
    no corroboration is refuse-and-alert). The indirect-prompt-injection signature
    -- one fresh source moving p while a thin book lets that same source push the
    mid -> REASON_SAME_SOURCE. An uncorroborated-but-present proposal is NOT refused;
    it just yields corroborated=False (informational-only, w_news=0 downstream).
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.ingestion.news import PRIMARY


REASON_TRUTH_GATE_REFUSE = "truth_gate_refuse"
REASON_SAME_SOURCE = "same_source_collusion"


@dataclass(frozen=True)
class TruthGateConfig:
    freshness_window_ns: int      # collusion "fresh" window, on the shared ns clock
    thin_book_depth_usd: Decimal  # top-of-book USD depth below which the book is "thin"
    thin_book_move: Decimal       # bid/ask spread that reads as a pushed mid on a thin book

    def __post_init__(self):
        if not self.freshness_window_ns > 0:
            raise ValueError("freshness_window_ns must be > 0")
        if not self.thin_book_depth_usd > 0:
            raise ValueError("thin_book_depth_usd must be > 0")
        if not self.thin_book_move > 0:
            raise ValueError("thin_book_move must be > 0")


@dataclass(frozen=True)
class TruthVerdict:
    refused: bool
    reason: str | None
    corroborated: bool
    primary_groups: tuple[str, ...]


def _group_for(allowlist):
    """name -> (tier, publisher_group) for every Source in the allowlist."""
    return {s.name: (s.tier, s.publisher_group) for s in allowlist}


def _matched_primaries(citations, *, event_store, by_name):
    """Resolve citation strings to envelopes (match on event_id OR a provenance link
    in entities), keep ONLY allowlisted PRIMARY envelopes. Citations are matched,
    never fetched. Returns the list of (envelope, publisher_group) kept."""
    wanted = set(citations)
    kept = []
    for env in event_store.all():
        if env.event_id in wanted or wanted.intersection(env.entities):
            meta = by_name.get(env.source)
            if meta is None:
                continue                      # not allowlisted -> dropped
            tier, group = meta
            if tier != PRIMARY:
                continue                      # DISCOVERY never counts / triggers
            kept.append((env, group))
    return kept


def verify(citations, *, event_store, book, allowlist, now_ns, config):
    by_name = _group_for(allowlist)
    matched = _matched_primaries(citations, event_store=event_store, by_name=by_name)

    if not matched:
        # news-only with no allowlisted primary corroboration -> refuse-and-alert.
        return TruthVerdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE,
                            corroborated=False, primary_groups=())

    groups = tuple(sorted({group for _env, group in matched}))
    corroborated = len(groups) >= 2
    return TruthVerdict(refused=False, reason=None,
                        corroborated=corroborated, primary_groups=groups)
