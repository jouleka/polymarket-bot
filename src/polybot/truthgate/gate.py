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


def _matches_citation(env, citation):
    """A citation matches an envelope iff it equals the envelope's event_id (guid) OR
    appears in its entities (the neutralized provenance link). Citations are matched,
    never fetched."""
    return env.event_id == citation or citation in env.entities


def _primary_matches_per_citation(citations, *, event_store, by_name):
    """Resolve EACH DISTINCT citation independently to the allowlisted-PRIMARY
    envelopes it matches (on event_id OR a provenance link in entities). Returns
    {citation: [(envelope, publisher_group), ...]}. Citations are matched, never
    fetched. DISCOVERY / non-allowlisted envelopes never appear (they cannot count
    toward, or defend against, anything)."""
    primaries = []
    for env in event_store.all():
        meta = by_name.get(env.source)
        if meta is None:
            continue                          # not allowlisted -> dropped
        tier, group = meta
        if tier != PRIMARY:
            continue                          # DISCOVERY never counts / triggers
        primaries.append((env, group))

    per_citation = {}
    for citation in set(citations):
        per_citation[citation] = [(env, group) for env, group in primaries
                                  if _matches_citation(env, citation)]
    return per_citation


def _is_thin_pushed(book, config):
    """Pure book-snapshot test for the 'a single source pushed a thin mid' signature:
    the smaller top-of-book USD depth is below thin_book_depth_usd AND the bid/ask
    spread is at least thin_book_move. Returns False on an empty side / no midpoint
    (a degenerate book is handled upstream by REJECT book_stale, not here)."""
    bid, bid_size, ask, ask_size = book.top_of_book()
    if bid is None or ask is None or bid_size is None or ask_size is None:
        return False
    bid_usd = bid * bid_size
    ask_usd = ask * ask_size
    # min() is an intentional conservative over-approximation of "thin": if EITHER side
    # is thin the book is treated as thin (easier to trip the collusion guard = safe).
    depth_usd = min(bid_usd, ask_usd)
    spread = ask - bid
    return depth_usd < config.thin_book_depth_usd and spread >= config.thin_book_move


def verify(citations, *, event_store, book, allowlist, now_ns, config):
    by_name = _group_for(allowlist)
    per_citation = _primary_matches_per_citation(
        citations, event_store=event_store, by_name=by_name)

    # AMBIGUOUS-CITATION EXCLUSION (C1 defense): a SINGLE allowlisted feed must not be
    # able to forge independent corroboration. Per DISTINCT citation, look at the set of
    # publisher_groups of the PRIMARY envelopes it matches:
    #   0 groups -> no match (ignore);
    #   exactly 1 group -> a CLEAN attestation of that group (record it + freshness);
    #   >1 group -> AMBIGUOUS (a cross-group collision -- the entities-injection or the
    #               (source, event_id) collision vector) -> drop entirely, contributes
    #               nothing. Fail-safe: a tampering signature can never manufacture
    #               corroboration.
    clean_groups = set()        # distinct groups with a clean (unambiguous) attestation
    clean_fresh_groups = set()  # of those, the groups with >=1 FRESH matched envelope
    for matches in per_citation.values():
        if not matches:
            continue
        groups = {group for _env, group in matches}
        if len(groups) != 1:
            continue            # ambiguous cross-group citation -> tampering -> drop
        (group,) = tuple(groups)
        clean_groups.add(group)
        # freshness `<=` is inclusive on purpose: the boundary counts as fresh (the
        # safe direction for the same-source collusion guard, which fires on freshness).
        if any(now_ns - env.observed_at <= config.freshness_window_ns
               for env, _group in matches):
            clean_fresh_groups.add(group)

    if not clean_groups:
        # Zero clean allowlisted-primary attestations -> refuse-and-alert. This also
        # catches the all-ambiguous case (a proposal whose only "evidence" is a
        # cross-group collision is not valid evidence) so a refuse is never lost.
        return TruthVerdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE,
                            corroborated=False, primary_groups=())

    groups = tuple(sorted(clean_groups))
    corroborated = len(clean_groups) >= 2

    # Same-source / indirect-prompt-injection refusal: the p-moving citations trace to
    # exactly ONE fresh CLEAN source AND the book is thin enough that that one source
    # could have pushed the mid. Corroboration (>=2 distinct groups) defeats this by
    # design.
    if not corroborated:
        if len(clean_fresh_groups) == 1 and _is_thin_pushed(book, config):
            return TruthVerdict(refused=True, reason=REASON_SAME_SOURCE,
                                corroborated=False, primary_groups=groups)

    return TruthVerdict(refused=False, reason=None,
                        corroborated=corroborated, primary_groups=groups)
