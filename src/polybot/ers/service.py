"""ERS service poll-loop (S3 / POL-5 slices 2 + 3).

Wires the chokepoint to the validator + the safety breaker: run the L7 DrawdownBreaker FIRST,
then poll PROPOSED intents, RE-FETCH the live book per intent (never trust the proposed price),
size against the current portfolio with the intent's learned co-move ClusterView, record the
decision + audit, fold each ACCEPT into the working portfolio so cross-intent caps hold, and call
the signer SEAM on ACCEPT. The ERS is the ONLY component that ever signs -- never Hermes. The
signer here is a paper stub; the real signer (Polymarket/rs-clob-client-v2 sidecar) replaces it
in S2/POL-4, and the real venue FLATTEN/cancelAll lands with the S4 supervisor.
"""

from decimal import Decimal

from polybot.ers.breaker import FLATTEN, FREEZE_ADDS
from polybot.ers.validator import (
    ClusterView,
    Decision,
    OpenPosition,
    Portfolio,
    TradeIntent,
    evaluate_intent,
)

_COLD = ClusterView(warm=False, rho=None)  # fail-closed default when no co-move model is wired


class PaperSigner:
    """Signer-seam stub: records the orders the ERS WOULD place (shadow) and the FLATTEN exits the
    L7 breaker WOULD signal -- no keys or network, so the loop runs end-to-end in shadow (S9). The
    real Rust signer + real venue de-risking replace it."""

    def __init__(self):
        self.placed = []
        self.flattened = []

    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

    def flatten(self, positions):
        # Shadow: record which positions the breaker asked to exit. Real venue de-risking
        # (GTD brackets / cancelAll) is S2/POL-4 + S4.
        self.flattened.append(tuple(p.token_id for p in positions))


def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None):
    """Process every PROPOSED intent in FIFO order; return the updated portfolio.

    Runs the L7 breaker FIRST (when wired): on FLATTEN it signals the exit through the seam, and on
    FLATTEN/FREEZE_ADDS it blocks all new adds this cycle (existing positions are held). Each
    surviving intent is re-priced off the live book, sized against the current portfolio with its
    learned co-move ClusterView, recorded + audited, and folded on ACCEPT (the cross-intent
    contract). A raising intent is isolated to REJECT(internal_error) so it can't wedge the queue."""
    block_reason = None
    if breaker is not None:
        state = breaker.evaluate(portfolio.positions, book_for)
        if state.action == FLATTEN:
            signer.flatten(portfolio.positions)
            block_reason = "l7_flatten"
        elif state.action == FREEZE_ADDS:
            block_reason = "l7_freeze"

    for intent in store.pending():
        trade_intent = None
        try:
            if block_reason is not None:
                decision = Decision("REJECT", None, None, block_reason)
            else:
                cluster = _cluster_view(cluster_model, intent, portfolio)
                trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm)
                book = book_for(trade_intent.token_id)
                if book is None:
                    # No live book to re-price against -> fail closed (never size off the proposal).
                    decision = Decision("REJECT", None, None, "no_book")
                else:
                    decision = evaluate_intent(trade_intent, book, portfolio, caps,
                                               calib_score=calib_score, cluster=cluster)
        except Exception:
            # One malformed intent must not wedge the FIFO queue head: fail it closed + audit,
            # and keep processing the rest.
            decision = Decision("REJECT", None, None, "internal_error")
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision)
    return portfolio


def _cluster_view(cluster_model, intent, portfolio):
    """The learned co-move verdict for this intent's cluster. A None model -> fail-closed cold (the
    slice-1 path). The cluster spans the intent's token + every open position sharing its cluster_id.

    LIMITATION (review M2, tracked for a follow-up): cluster_id is the ``event_id`` PLACEHOLDER, so
    the per-cluster cap currently keys off the same field as the per-event UNION cap -- it fails
    SAFE (over-couples within an event) but does NOT yet discriminate cross-event latent drivers, so
    slice-3's "earned relaxation" is effectively dormant until a real latent-cluster assignment lands
    (a natural consumer of this same co-move matrix). Do not mistake this alias for the final
    cluster taxonomy."""
    if cluster_model is None:
        return _COLD
    cluster_id = intent.event_id
    tokens = [intent.token_id]
    tokens += [p.token_id for p in portfolio.positions if p.cluster_id == cluster_id]
    return cluster_model.view(tokens)


def _to_trade_intent(intent, *, matrix_cold):
    # The ERS populates the risk keys (NOT Hermes-trusted). resolution_source + cluster_id come
    # from the proposal's ids (slice-2 placeholders); matrix_cold is driven by the co-move
    # ClusterView (matrix_cold == not warm, so cold positions keep the <=3 count gate).
    return TradeIntent(
        token_id=intent.token_id, condition_id=intent.condition_id, event_id=intent.event_id,
        resolution_source=intent.condition_id, cluster_id=intent.event_id,
        p=intent.p, max_price=intent.max_price, size_usd_suggestion=intent.size_usd_suggestion,
        matrix_cold=matrix_cold,
    )


def _fold(portfolio, trade_intent, decision):
    pos = OpenPosition(
        condition_id=trade_intent.condition_id, event_id=trade_intent.event_id,
        resolution_source=trade_intent.resolution_source, cluster_id=trade_intent.cluster_id,
        worst_case_risk=decision.stake_usd, matrix_cold=trade_intent.matrix_cold,
        token_id=trade_intent.token_id, entry_price=decision.price_exec, frozen=False,
    )
    return Portfolio(nav=portfolio.nav, positions=portfolio.positions + (pos,))
