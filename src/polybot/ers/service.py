"""ERS service poll-loop (S3 / POL-5 slice 2).

Wires the chokepoint to the validator: poll PROPOSED intents, RE-FETCH the live book per
intent (never trust the proposed price), run ``evaluate_intent`` against the current
portfolio, record the decision + audit, fold each ACCEPT into the working portfolio so
cross-intent caps hold, and call the signer SEAM on ACCEPT. The ERS is the ONLY component
that ever signs -- never Hermes. The signer here is a paper stub; the real signer
(Polymarket/rs-clob-client-v2 sidecar) replaces it in S2/POL-4.
"""

from decimal import Decimal

from polybot.ers.validator import (
    Decision,
    OpenPosition,
    Portfolio,
    TradeIntent,
    evaluate_intent,
)


class PaperSigner:
    """Signer-seam stub: records the orders the ERS WOULD place (shadow), with no keys or
    network, so the loop runs end-to-end in shadow (S9). The real Rust signer replaces it."""

    def __init__(self):
        self.placed = []

    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})


def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1)):
    """Process every PROPOSED intent in FIFO order; return the updated portfolio. Each
    ACCEPT is folded into the portfolio BEFORE the next intent is evaluated, so the
    holistic at-risk ceiling holds across the batch (the validator's cross-intent contract)."""
    for intent in store.pending():
        trade_intent = _to_trade_intent(intent)
        try:
            book = book_for(trade_intent.token_id)
            if book is None:
                # No live book to re-price against -> fail closed (never size off the proposal).
                decision = Decision("REJECT", None, None, "no_book")
            else:
                decision = evaluate_intent(trade_intent, book, portfolio, caps, calib_score=calib_score)
        except Exception:
            # One malformed intent must not wedge the FIFO queue head (cf. data_api / polygon
            # skip-bad-item resilience): fail it closed + audit, and keep processing the rest.
            decision = Decision("REJECT", None, None, "internal_error")
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision.stake_usd)
    return portfolio


def _to_trade_intent(intent):
    # The ERS populates the risk keys (NOT Hermes-trusted). Slice-2 placeholders:
    # resolution_source + cluster come from the proposal's ids, and matrix_cold=True is
    # fail-closed (correlations are UNKNOWN until the learned co-move matrix lands in slice 3).
    return TradeIntent(
        token_id=intent.token_id, condition_id=intent.condition_id, event_id=intent.event_id,
        resolution_source=intent.condition_id, cluster_id=intent.event_id,
        p=intent.p, max_price=intent.max_price, size_usd_suggestion=intent.size_usd_suggestion,
        matrix_cold=True,
    )


def _fold(portfolio, trade_intent, stake):
    pos = OpenPosition(
        condition_id=trade_intent.condition_id, event_id=trade_intent.event_id,
        resolution_source=trade_intent.resolution_source, cluster_id=trade_intent.cluster_id,
        worst_case_risk=stake, matrix_cold=trade_intent.matrix_cold,
    )
    return Portfolio(nav=portfolio.nav, positions=portfolio.positions + (pos,))
