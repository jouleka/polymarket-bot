"""ERS service poll-loop (S3 / POL-5 slices 2 + 3; S6 / POL-8 HermesPipeline wiring).

Wires the chokepoint to the validator + the safety breaker, and -- when a HermesPipeline is
supplied -- to the full S6 re-derivation chain (defensive detectors, citation truth-gate, signal
fusion, anchor clamp, per-intent calibration k, forecast + per-signal component logging). The ERS
is the ONLY component that ever signs -- never Hermes. Hermes can at worst enqueue a PROPOSED row
through ProposeOnlyFacade; this loop independently re-derives price, size, caps, corroboration, and
the anchored posterior. The signer here is a paper stub; the real signer is S2/POL-4.

S6 contract (DESIGN-S6-HERMES.md §2/§3): pipeline=None -> behavior is EXACTLY slice-3 (the existing
tests stay green). pipeline supplied -> the S6 chain plus the POL-14 metadata gate engage and
calib_score is IGNORED in favor of the per-intent k = pipeline.calib_gate.k_for(category).
"""

from dataclasses import dataclass
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
from polybot.fusion.engine import FusionError
from polybot.resolution.errors import ConditionAlreadyTerminal
from polybot.ers.market_meta import (
    MarketMetadataUnavailable,
    ResolutionSubjectMetadata,
    StubMarketMeta,
)
from polybot.detectors.orchestrator import DetectorInputs, REASON_DETECTOR_AVOID

_COLD = ClusterView(warm=False, rho=None)  # fail-closed default when no co-move model is wired

# New S6/POL-14 Decision.reason codes (free-form strings; no validator change).
REASON_ANCHOR_ERROR = "anchor_error"
REASON_MARKET_META_UNAVAILABLE = "market_meta_unavailable"
REASON_RESOLUTION_IDENTITY_UNAVAILABLE = "resolution_identity_unavailable"
REASON_MARKET_RESOLVED = "market_resolved"


@dataclass(frozen=True)
class HermesPipeline:
    """The S6 re-derivation context (DESIGN §3). Optional, defaulting None in process_pending -- the
    same additive-seam pattern as cluster_model / breaker. When provided, the per-intent k from
    calib_gate.k_for(category) supersedes the batch calib_score (which is retained for back-compat)."""
    calib_gate: object            # CalibrationGate: k_for(category) -> Decimal{0,1}; clamp_p(...) -> AnchorResult
    fusion_config: object         # fusion.engine.FusionConfig
    truth_gate_config: object     # truthgate.gate.TruthGateConfig
    detectors: object             # detectors.orchestrator.DetectorOrchestrator
    forecast_ledger: object       # calibration.ledger.ForecastLedger
    component_log: object         # fusion.component_log.ComponentLog
    market_meta: object           # ers.market_meta.MarketRegistry (StubMarketMeta only in explicit legacy tests)
    allowlist: object             # iterable of ingestion.news.Source (truth-gate independence surface)
    event_store: object           # storage.market_memory.EventStore (sanitized citations only)
    stamper: object               # the ONE shared core.clock.MonotonicStamper (now_ns for the gate)


def process_pending(store, *, book_for, portfolio, caps, signer, calib_score=Decimal(1),
                    cluster_model=None, breaker=None, pipeline=None, controller=None,
                    gtd_for=None, fill_sink=None):
    """Process every PROPOSED intent in FIFO order; return the updated portfolio.

    Runs the L7 breaker FIRST (when wired): FLATTEN signals the exit + blocks adds (l7_flatten),
    FREEZE_ADDS blocks adds (l7_freeze). Each surviving intent is processed inside a per-intent
    try/except so one malformed intent can't wedge the FIFO queue. On ACCEPT the signer is called
    THEN the portfolio is folded before the next intent (the cross-intent caps contract). When
    pipeline is None this is exactly slice-3; when supplied, the S6 chain engages.

    gtd_for (S4.2): optional callable ``(decision, position, *, caps, standing_exit_total) ->
    Bracket`` -- when supplied, a protective GTD exit bracket is pre-staged for every ACCEPT.
    ``gtd_for=None`` (the default) == today's behavior: no GTD staging, the 469 baseline holds."""
    # 1. Op-state gate (S4.1): consulted FIRST so a KILL/PAUSE/op-FLATTEN op-state dominates the
    #    L7 breaker. controller=None => exactly today's behavior (the existing tests stay green).
    #    Precedence: KILL/PAUSE/op_flatten (controller) > l7_flatten > l7_freeze > none.
    block_reason = None
    if controller is not None:
        op = controller.verdict(portfolio, signer)
        if op.block_reason is not None:
            # The controller already fired any de-risk (op-FLATTEN -> signer.flatten/cancel_all)
            # inside verdict(); here we just dominate the loop with its block_reason.
            block_reason = op.block_reason

    # 2. L7 drawdown breaker (EXISTING, unchanged) -- only consulted if the op-state did NOT
    #    already block, so op_flatten can never be overwritten by a weaker l7_freeze/none.
    if block_reason is None and breaker is not None:
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
            elif pipeline is None:
                decision, trade_intent = _process_intent_slice3(
                    intent, book_for, portfolio, caps, calib_score, cluster_model)
            else:
                decision, trade_intent = _process_intent_pipeline(
                    intent, book_for, portfolio, caps, cluster_model, pipeline)
        except Exception:
            # One malformed intent must not wedge the FIFO queue head: fail it closed + audit,
            # and keep processing the rest.
            decision = Decision("REJECT", None, None, "internal_error")
            trade_intent = None
        store.record_decision(intent.intent_id, decision)
        if decision.verdict == "ACCEPT":
            signer.place(intent, decision)
            portfolio = _fold(portfolio, trade_intent, decision)
            if gtd_for is not None:
                # Pre-stage the protective GTD exit for the just-folded position (the passive
                # backstop), enforcing caps.gtd_bracket_aggregate via the derivation. The folded
                # position is the last one. NOTE (shadow limitation): standing sums the append-only
                # gtd_exits, which is never decremented on exit/flatten -- it's CUMULATIVE, not
                # currently-standing, so over a long shadow run it can over-approximate and
                # fail-CLOSED (refuse a legitimate new bracket). Safe (never over-stages); the live
                # POL-4 signer must track currently-STANDING exits, not the cumulative total.
                position = portfolio.positions[-1]
                standing = sum((Decimal(b["size"]) for b in signer.gtd_exits), Decimal(0))
                bracket = gtd_for(decision, position, caps=caps, standing_exit_total=standing)
                signer.place_gtd_bracket(position, exit_price=bracket.exit_price,
                                         expiry=bracket.expiry)
            if fill_sink is not None:
                # Durable INTERNAL leg of the S4.5 reconcile: record the just-folded position.
                # fill_sink=None (the default) => no fills row => byte-for-byte today's behavior.
                fill_sink(intent, decision, portfolio.positions[-1])
    return portfolio


def make_fill_sink(store):
    """Return the recording callable wired into process_pending(fill_sink=...) so every ACCEPT
    appends a durable fill (the internal reconcile leg). Long convention: side is ALWAYS "BUY";
    shares = worst_case_risk / entry_price (notional / entry). entry_price > 0 holds on any ACCEPT,
    so the division is exact and never divides by zero."""
    def _sink(intent, decision, position):
        store.record_fill(
            intent_id=intent.intent_id, token_id=position.token_id,
            condition_id=position.condition_id, event_id=position.event_id, side="BUY",
            shares=(position.worst_case_risk / position.entry_price),
            price_exec=position.entry_price, worst_case_risk=position.worst_case_risk)
    return _sink


def _process_intent_slice3(intent, book_for, portfolio, caps, calib_score, cluster_model):
    """The unchanged slice-3 per-intent path (pipeline=None). Returns (decision, trade_intent)."""
    # The co-move ClusterView is p-INDEPENDENT, so it's computed once here (not as a late "step")
    # and reused for the final _to_trade_intent(p_override=...) after fusion/clamp -- no step was
    # dropped; the design's later ordinal is just where its matrix_cold flag is consumed.
    cluster = _cluster_view(cluster_model, intent, portfolio)
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm)
    book = book_for(trade_intent.token_id)
    if book is None:
        # No live book to re-price against -> fail closed (never size off the proposal).
        return Decision("REJECT", None, None, "no_book"), trade_intent
    decision = evaluate_intent(trade_intent, book, portfolio, caps,
                               calib_score=calib_score, cluster=cluster)
    return decision, trade_intent


def _process_intent_pipeline(intent, book_for, portfolio, caps, cluster_model, pipeline):
    """The S6 per-intent chain plus POL-14 metadata gate. Returns (decision, trade_intent).

    Order is load-bearing: cheap/structural refusals (no_book, detector_avoid, truth-gate) come
    BEFORE any genuine estimate, so a refused proposal records NO forecast (DESIGN §2). A clean
    estimate records a forecast + per-signal components BEFORE evaluate_intent, so a SKIP on k=0
    still logs the estimate -- calibration grades estimates, not execution."""
    from polybot.fusion.engine import fuse  # local import keeps the module import-light + cycle-free
    from polybot.truthgate.gate import verify as truth_verify

    cluster = _cluster_view(cluster_model, intent, portfolio)
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm)

    # 1. Single live book re-fetch, shared by truth-gate / fusion / anchor / evaluate_intent.
    book = book_for(trade_intent.token_id)
    if book is None:
        return Decision("REJECT", None, None, "no_book"), trade_intent

    # 2. Defensive detector pre-gate (FOLLOW off). AVOID -> REJECT before any sizing.
    verdict = pipeline.detectors.evaluate(intent, inputs=DetectorInputs())
    if verdict.action == "AVOID":
        return Decision("REJECT", None, None, REASON_DETECTOR_AVOID), trade_intent

    # 3. Citation truth-gate over the sanitized EventStore + the live book (never fetches a URL).
    truth = truth_verify(intent.citations, event_store=pipeline.event_store, book=book,
                         allowlist=pipeline.allowlist, now_ns=pipeline.stamper.stamp(),
                         config=pipeline.truth_gate_config)
    if truth.refused:
        return Decision("REJECT", None, None, truth.reason), trade_intent

    # 4. Fusion prior + anchor reference is the live mid; degenerate -> book_stale.
    mid = book.midpoint()
    if mid is None:
        return Decision("REJECT", None, None, "book_stale"), trade_intent

    # 5. Resolve the trusted condition+token pair ONCE. Known provider gaps are a distinct audited
    #    rejection before fusion or any non-backfillable forecast/component write. Unexpected
    #    implementation failures are deliberately not swallowed here: the outer per-intent guard
    #    maps those to internal_error.
    try:
        metadata = pipeline.market_meta.metadata_for(intent)
    except MarketMetadataUnavailable:
        return Decision("REJECT", None, None, REASON_MARKET_META_UNAVAILABLE), trade_intent
    resolution_subject = None
    if not isinstance(pipeline.market_meta, StubMarketMeta):
        subject_for = getattr(pipeline.market_meta, "resolution_subject_for", None)
        if not callable(subject_for):
            return Decision(
                "REJECT", None, None, REASON_RESOLUTION_IDENTITY_UNAVAILABLE
            ), trade_intent
        try:
            resolution_subject = subject_for(intent)
        except MarketMetadataUnavailable:
            return Decision(
                "REJECT", None, None, REASON_RESOLUTION_IDENTITY_UNAVAILABLE
            ), trade_intent
        if not isinstance(resolution_subject, ResolutionSubjectMetadata):
            return Decision(
                "REJECT", None, None, REASON_RESOLUTION_IDENTITY_UNAVAILABLE
            ), trade_intent
    category = metadata.category
    question_text = metadata.question_text
    seconds = metadata.seconds_to_resolution
    if resolution_subject is not None and (
            resolution_subject.category != category
            or resolution_subject.event_id != intent.event_id
            or resolution_subject.condition_id != intent.condition_id
            or resolution_subject.token_id != intent.token_id):
        return Decision(
            "REJECT", None, None, REASON_RESOLUTION_IDENTITY_UNAVAILABLE
        ), trade_intent

    # 6. Weighted log-odds fusion. Hermes's p enters ONLY as p_news, w_news live iff corroborated.
    #    p_base/p_micro/p_flow are ERS-derived; at MVP p_base = mid (no base-rate model wired here
    #    beyond the anchor's prior), p_micro/p_flow carry zero weight (logged, not weighted).
    fusion_result = fuse(mid, p_news=intent.p, p_base=mid, p_micro=mid,
                         p_flow=verdict.p_flow if Decimal(0) < verdict.p_flow < Decimal(1) else mid,
                         corroborated=truth.corroborated, config=pipeline.fusion_config)

    # 7. Anchor clamp, wrapped so a non-finite anchor maps to a DISTINCT anchor_error (not internal).
    try:
        anchor = pipeline.calib_gate.clamp_p(
            fusion_result.p_final, mid, question_text=question_text,
            seconds_to_resolution=seconds, corroborated=truth.corroborated)
    except (ValueError, FusionError):
        return Decision("REJECT", None, None, REASON_ANCHOR_ERROR), trade_intent
    p_clamped = anchor.p_clamped

    try:
        pipeline.forecast_ledger.require_condition_open(intent.condition_id)
    except ConditionAlreadyTerminal:
        return Decision("REJECT", None, None, REASON_MARKET_RESOLVED), trade_intent

    # 8. Record the genuine estimate: per-signal components THEN the forecast (the calibration
    #    substrate). Components FIRST: component_log fails-loud on a non-finite raw p_news (Hermes
    #    can supply Decimal("NaN")), so doing it first aborts BEFORE any forecast row is written ->
    #    no orphan. record_forecast can't raise on the happy path (p_clamped is always finite/in
    #    range; its INSERT OR IGNORE returns False on a dup, never raises).
    forecast_id = intent.intent_id
    components = fusion_result.components
    pipeline.component_log.record(
        forecast_id, p_news=components["p_news"], p_base=components["p_base"],
        p_micro=components["p_micro"], p_flow=components["p_flow"],
        w_news_effective=fusion_result.w_news_effective, corroborated=truth.corroborated, mid=mid)
    try:
        pipeline.forecast_ledger.record_forecast(
            forecast_id, category=category, condition_id=intent.condition_id,
            p=p_clamped, market_mid=mid,
            event_id=None if resolution_subject is None else resolution_subject.event_id,
            token_id=None if resolution_subject is None else resolution_subject.token_id,
            outcome_slot=None if resolution_subject is None else resolution_subject.outcome_slot,
            sibling_token_ids=(
                None if resolution_subject is None else resolution_subject.sibling_token_ids
            ))
    except ConditionAlreadyTerminal:
        return Decision("REJECT", None, None, REASON_MARKET_RESOLVED), trade_intent

    # 9. Per-intent calibration k (Decimal{0,1}); supersedes the batch calib_score. k=0 -> paper-only.
    k = pipeline.calib_gate.k_for(category)

    # 10-12. Substitute the anchored posterior into the TradeIntent and size with the UNCHANGED
    #        validator (calib_score=k). evaluate_intent / validator dataclasses are untouched.
    trade_intent = _to_trade_intent(intent, matrix_cold=not cluster.warm, p_override=p_clamped)
    decision = evaluate_intent(trade_intent, book, portfolio, caps, calib_score=k, cluster=cluster)
    return decision, trade_intent


def _cluster_view(cluster_model, intent, portfolio, *, cluster_id_of=None):
    """The learned co-move verdict for this intent's cluster. A None model -> fail-closed cold. The
    cluster spans the intent's token + every open position sharing its cluster_id.

    cluster_id_of is a one-line PLUGGABLE hook (Fork 8C): it defaults to ``intent.event_id`` (the
    slice-2/3 placeholder that fails SAFE -- over-couples within an event), so the real latent-cluster
    slice swaps the function without re-editing the loop. Do not mistake this alias for the final
    cluster taxonomy."""
    if cluster_model is None:
        return _COLD
    if cluster_id_of is None:
        cluster_id_of = lambda i: i.event_id
    cluster_id = cluster_id_of(intent)
    tokens = [intent.token_id]
    tokens += [p.token_id for p in portfolio.positions if p.cluster_id == cluster_id]
    return cluster_model.view(tokens)


def _to_trade_intent(intent, *, matrix_cold, p_override=None):
    # The ERS populates the risk keys (NOT Hermes-trusted). resolution_source + cluster_id come
    # from the proposal's ids (slice-2 placeholders); matrix_cold is driven by the co-move
    # ClusterView. p_override (the fused+anchored posterior, S6) substitutes intent.p before the
    # validator sizes -- so the validator never sizes off Hermes's raw p when the pipeline is active.
    return TradeIntent(
        token_id=intent.token_id, condition_id=intent.condition_id, event_id=intent.event_id,
        resolution_source=intent.condition_id, cluster_id=intent.event_id,
        p=intent.p if p_override is None else p_override,
        max_price=intent.max_price, size_usd_suggestion=intent.size_usd_suggestion,
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


class PaperSigner:
    """Signer-seam stub: records the orders the ERS WOULD place (shadow), the FLATTEN exits the
    L7/op-FLATTEN path WOULD signal, the working-entry cancels (kill path), and the pre-staged GTD
    EXIT brackets (the passive backstop) -- no keys or network, so the loop runs end-to-end in
    shadow (S9). Satisfies the ers.signer.Signer Protocol. The real Rust signer + real venue
    de-risking (POL-4) replace it.

    Cancel-vs-keep (DESIGN §9): cancel_all() cancels WORKING/unfilled ENTRY orders and leaves the
    GTD EXIT brackets STANDING -- a cancelAll that also killed the protective exits would INCREASE
    risk on a wedge. The live POL-4 signer must implement that entry-vs-exit distinction.
    """

    def __init__(self):
        self.placed = []
        self.flattened = []
        self.cancelled_all = []   # cancel_all() appends a marker (count of cancels issued)
        self.gtd_exits = []       # place_gtd_bracket(...) appends the standing protective exit

    def place(self, intent, decision):
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

    def flatten(self, positions):
        # Shadow: record which positions the breaker / op-FLATTEN asked to exit.
        self.flattened.append(tuple(p.token_id for p in positions))

    def cancel_all(self):
        # Shadow: cancel WORKING/unfilled ENTRY orders. Deliberately does NOT touch gtd_exits --
        # the protective GTD exit brackets are the passive backstop and must SURVIVE the kill.
        self.cancelled_all.append({"cancelled": "working_entries"})

    def place_gtd_bracket(self, position, *, exit_price, expiry):
        # Shadow: record a pre-staged protective standing exit (good-til-date). size = the
        # position's worst-case risk (notional for a long), the dollars the exit protects.
        self.gtd_exits.append({"token_id": position.token_id, "exit_price": exit_price,
                               "expiry": expiry, "size": position.worst_case_risk})

    def run_canary(self):
        # Shadow: a sign+place+cancel min-size canary returns True (real signing is POL-4).
        # NEVER blind-retries -- a real canary failure must HALT signing (S4.4), not loop.
        return True
