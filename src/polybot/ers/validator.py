"""The ERS risk-engine validator (S3 / POL-5 slice 1).

``evaluate_intent`` is a PURE function: given an untrusted proposed intent, the re-fetched
live book, the ERS's own confirmed portfolio, and the signed caps, it re-prices, sizes
1/4-Kelly on the EXECUTABLE price, clamps by every S0 cap, and FAILS CLOSED (any ambiguity
-> REJECT/SKIP + reason code). No persistence, no network, no keys. See docs/DESIGN-S3-ERS.md.

Risk accounting: a position is a LONG in an outcome token, so its worst-case
mark-to-resolution loss == its notional (the USD deployed) -- you lose the full stake if it
resolves to 0. Every cap is measured in those worst-case-loss dollars.

CONTRACT (slice-2 obligation): each call clamps the new intent against the PASSED portfolio,
so the holistic at-risk ceiling holds across intents ONLY if the caller folds every ACCEPT
into ``portfolio`` before the next ``evaluate_intent``. Evaluating two intents against the
same fresh portfolio could let them sum past total_open -- the ERS loop must serialize.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TradeIntent:
    token_id: str            # the outcome token to BUY (a Yes leg, or a No leg = short-Yes)
    condition_id: str        # market            -> per-market cap
    event_id: str            # event             -> per-event UNION cap (NegRisk legs share it)
    resolution_source: str   # UMA source        -> per-source cap (ERS-populated, not Hermes-trusted)
    cluster_id: str          # latent-driver cluster
    p: Decimal               # Hermes P(this token resolves YES=$1), in [0,1] -- UNTRUSTED
    max_price: Decimal       # Hermes's price limit; we re-price off the live book, never pay above it
    size_usd_suggestion: Decimal  # requested size -- capped, NEVER trusted upward
    matrix_cold: bool = True      # is this intent's cluster correlation still UNKNOWN (-> +1 fail-closed)


@dataclass(frozen=True)
class OpenPosition:
    condition_id: str
    event_id: str
    resolution_source: str
    cluster_id: str
    worst_case_risk: Decimal  # = notional for a long
    matrix_cold: bool = True
    # Slice-3 L7 marking fields (the ERS fills these on ACCEPT; shares = worst_case_risk /
    # entry_price). Defaulted so pre-L7 construction sites stay valid; the breaker treats a
    # non-positive entry_price as un-markable (fail closed). ``frozen`` (disputed/frozen) is
    # excluded from the L7 drawdown + velocity but still counts toward total_open.
    token_id: str = ""
    entry_price: Decimal = Decimal(0)
    frozen: bool = False


@dataclass(frozen=True)
class Portfolio:
    nav: Decimal
    positions: tuple = ()

    def total_open_risk(self):
        return sum((p.worst_case_risk for p in self.positions), Decimal(0))

    def market_risk(self, condition_id):
        return self._risk_where(lambda p: p.condition_id == condition_id)

    def event_risk(self, event_id):
        return self._risk_where(lambda p: p.event_id == event_id)

    def source_risk(self, resolution_source):
        return self._risk_where(lambda p: p.resolution_source == resolution_source)

    def cluster_risk(self, cluster_id):
        return self._risk_where(lambda p: p.cluster_id == cluster_id)

    def matrix_cold_count(self):
        return sum(1 for p in self.positions if p.matrix_cold)

    def _risk_where(self, pred):
        return sum((p.worst_case_risk for p in self.positions if pred(p)), Decimal(0))


@dataclass(frozen=True)
class Decision:
    verdict: str                  # "ACCEPT" | "REJECT" | "SKIP"
    stake_usd: Decimal | None
    price_exec: Decimal | None
    reason: str                   # reason code; on ACCEPT, the binding cap (or "kelly")


@dataclass(frozen=True)
class ClusterView:
    """The ERS's learned co-move verdict for an intent's cluster (slice 3). ``warm`` is True
    only once every member pair has enough price-bar history; then ``rho`` is the max pairwise
    correlation. Fail-closed default ``ClusterView(False, None)`` = cold = the slice-1 path
    (matrix_cold, the <=3 count gate, no dollar cluster cap)."""
    warm: bool
    rho: Decimal | None = None


_COLD_CLUSTER = ClusterView(warm=False, rho=None)  # fail-closed default: the slice-1 path


def evaluate_intent(intent, book, portfolio, caps, *, calib_score=Decimal(1), cluster=_COLD_CLUSTER):
    # 1. Re-price off the touch (we always BUY an outcome token). Fail closed on any
    #    un-priceable book: stale, no ask, or crossed/locked (midpoint None).
    price = book.best_ask()
    if book.is_stale() or price is None or book.midpoint() is None:
        return Decision("REJECT", None, None, "book_stale")
    # A top-of-book price outside (0,1) is degenerate: no tradeable edge, and price==1 would
    # divide-by-zero below. A $1 ask passes the crossed-book guard above, so guard it here.
    if not (Decimal(0) < price < Decimal(1)):
        return Decision("REJECT", None, price, "degenerate_price")
    if price > intent.max_price:
        return Decision("SKIP", None, price, "price_above_limit")
    # The proposed probability is UNTRUSTED: an impossible p (>=1 or <=0) is garbage /
    # hallucination and must be REFUSED, never sized maximally on.
    if not (Decimal(0) < intent.p < Decimal(1)):
        return Decision("REJECT", None, price, "bad_probability")
    # calib_score is an ERS-internal multiplier in [0,1] (0 = cold-start paper-only -> sizes
    # to 0 -> SKIP via the floor); anything outside [0,1] is a wiring error -> fail closed.
    if not (Decimal(0) <= calib_score <= Decimal(1)):
        return Decision("REJECT", None, price, "bad_calibration")

    # 2. Edge: 1/4-Kelly fraction must be positive. (Deferred: the full stacked hurdle H.)
    f_full = (intent.p - price) / (Decimal(1) - price)
    if f_full <= 0:
        return Decision("SKIP", None, price, "no_edge")

    # 2b. Concurrency / fail-closed cluster gate (size-independent hard gates).
    if len(portfolio.positions) >= caps.max_concurrent:
        return Decision("REJECT", None, price, "max_concurrent")
    if intent.matrix_cold and portfolio.matrix_cold_count() >= caps.matrix_cold_concurrent:
        # unknown-correlation positions are capped by COUNT (the slice-1 cluster gate).
        return Decision("REJECT", None, price, "matrix_cold_concurrent")
    # A WARM cluster verdict with no rho is an ERS wiring error -> fail closed, never size on it.
    # (Cold clusters keep the count gate above; warm clusters earn the dollar cap in step 4.)
    if cluster.warm and cluster.rho is None:
        return Decision("REJECT", None, price, "bad_cluster")

    # 3. 1/4-Kelly stake on the executable price; calibration auto-shrinks it.
    frac_eff = caps.kelly_fraction * min(Decimal(1), calib_score)
    stake = frac_eff * f_full * portfolio.nav
    binding = "kelly"

    # 4. Clamp by every cap (worst-case-loss dollars = notional for a long). The smallest
    #    headroom wins; its reason is recorded. Liquidity uses the resting TOUCH depth
    #    (slice 1; full multi-level book-walk + the <=1c impact term are deferred).
    ask_size = book.top_of_book()[3]
    candidates = [
        (caps.per_trade, "per_trade_cap"),
        (caps.per_market - portfolio.market_risk(intent.condition_id), "per_market_cap"),
        (caps.per_event_union - portfolio.event_risk(intent.event_id), "per_event_cap"),
        (caps.per_source_open - portfolio.source_risk(intent.resolution_source), "per_source_cap"),
        (caps.total_open_risk - portfolio.total_open_risk(), "total_open_cap"),
        (intent.size_usd_suggestion, "size_suggestion"),   # Hermes's request: clamp, never trust upward
        (caps.liquidity_depth_frac * ask_size * price, "liquidity_cap"),
    ]
    if cluster.warm:
        # Learned co-move cap: the cluster's summed worst-case risk is bounded by cluster_cap(rho).
        # An ADDITIONAL min() term (can only tighten); replaces the cold count gate for warm clusters.
        candidates.append(
            (caps.cluster_cap(cluster.rho) - portfolio.cluster_risk(intent.cluster_id), "per_cluster_cap")
        )
    for headroom, reason in candidates:
        if headroom < stake:
            stake, binding = headroom, reason

    # 5. Min-floor: never round up to meet a cap.
    if stake < caps.min_position_floor:
        return Decision("SKIP", None, price, "below_min_floor")

    return Decision("ACCEPT", stake, price, binding)
