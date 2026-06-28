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


def verify(citations, *, event_store, book, allowlist, now_ns, config):
    raise NotImplementedError("verify not yet implemented")
