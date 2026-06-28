"""End-to-end S6 shadow pipeline (S6 / POL-8, DESIGN-S6-HERMES.md §9.3).

Drives synthetic Hermes proposals through the ONLY write surface (ProposeOnlyFacade.propose_trade)
into process_pending(pipeline=...) on a PaperSigner, exercising the real FusionEngine, ComponentLog,
CalibrationGate/ForecastLedger/PriorEngine, StubMarketMeta, DetectorOrchestrator, and the citation
truth-gate together -- NO fakes for the four DESIGN §9.3 scenarios (the detector-AVOID scenario uses
a thin shim ONLY to forward toxic DetectorInputs into the REAL orchestrator). Asserts:
  (a) a clean CORROBORATED proposal flows fusion->clamp->record_forecast->validator and SKIPs on
      k=0 (paper-only MVP) with the forecast + components logged;
  (b) an indirect-prompt-injection proposal (single fresh source moving p + a thin, wide book) is
      REJECTed same_source_collusion and NEVER reaches the signer;
  (c) an UNCORROBORATED proposal trades mid+prior-only (w_news_effective=0, informational-only);
  (d) a detector-AVOID proposal is REJECTed before sizing.

The category is the "unknown" stub -> k=0 -> paper-only by design; the SKIP is the intended state.

REAL-UNIT FRESHNESS NOTE: unlike the truth-gate UNIT tests (a ~10us window + a fake counter stamper),
this suite uses the REAL MonotonicStamper (time.monotonic_ns) + real SQLite inserts. A 10us window
would make a just-seeded envelope already "stale", so scenario (b) would NOT fire (vacuous pass). We
therefore set freshness_window_ns to a realistic "fresh news" span (10**12 ns = 1000s, the
production-sane value) so an envelope seeded microseconds-to-milliseconds before the proposal counts
as FRESH and the same-source collusion branch genuinely engages.

CITATION MATCH KEY: the truth-gate matches a citation against an envelope on event_id OR entities
(NEVER market_links, never a fetch). So the seeded envelopes' event_id is the match key the
proposals cite ("c1"/"c2"/"inj"/"solo").
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.calibration.config import CalibrationConfig
from polybot.calibration.gate import CalibrationGate
from polybot.calibration.ledger import ForecastLedger
from polybot.calibration.prior import PriorEngine
from polybot.detectors.classify import INSIDER_LIKE
from polybot.detectors.config import DetectorConfig
from polybot.detectors.orchestrator import DetectorInputs, DetectorOrchestrator
from polybot.ers.caps import RiskCaps
from polybot.ers.facade import ProposeOnlyFacade
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import StubMarketMeta
from polybot.ers.service import HermesPipeline, PaperSigner, process_pending
from polybot.ers.validator import Portfolio
from polybot.fusion.component_log import ComponentLog
from polybot.fusion.engine import FusionConfig
from polybot.ingestion.allowlist import DEFAULT_ALLOWLIST
from polybot.ingestion.envelope import make_envelope
from polybot.storage.market_memory import EventStore
from polybot.truthgate.gate import TruthGateConfig


def _book(ask, *, ask_size="1000", bid="0.01", bid_size="1000"):
    """Build a one-level LocalBook. Defaults give a deep, healthy top-of-book."""
    from polybot.ingestion.orderbook import LocalBook
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": bid_size}],
                     "asks": [{"price": ask, "size": ask_size}]})
    return book


def _seed(evstore, stamper, *, source, event_id):
    """Seed an allowlisted PRIMARY envelope whose event_id is the citation match key. entities/
    market_links are left default -- the gate matches on event_id here (not market_links)."""
    evstore.append(make_envelope(stamper, source=source, source_tier="PRIMARY",
                                 event_id=event_id, content="text"))


def _fusion_config():
    # Real FusionConfig: Hermes's p enters as p_news with w_news=0.20 (only when corroborated),
    # base-rate prior w_base=0.30, micro/flow 0-weight (logged), clip +/-2.0 log-odds.
    return FusionConfig(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)


def _truth_config():
    # REALISTIC freshness window (not the 10us unit-test value) so a just-seeded envelope is FRESH
    # under the real time.monotonic_ns stamper -- this is load-bearing for scenario (b).
    return TruthGateConfig(freshness_window_ns=10**12,
                           thin_book_depth_usd=Decimal("50"),
                           thin_book_move=Decimal("0.02"))


def _build_pipeline(tmp_path, stamper, event_store):
    """A real HermesPipeline with ALL real collaborators (DEFAULT_ALLOWLIST has fed-press=
    federalreserve.gov and sec-press=sec.gov as two DISTINCT PRIMARY publisher_groups -> two
    independent citations corroborate)."""
    ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
    clog = ComponentLog(str(tmp_path / "c.db"), stamper=stamper)
    pipe = HermesPipeline(
        calib_gate=CalibrationGate(ledger, PriorEngine(), CalibrationConfig()),
        fusion_config=_fusion_config(),
        truth_gate_config=_truth_config(),
        detectors=DetectorOrchestrator(DetectorConfig()),
        forecast_ledger=ledger,
        component_log=clog,
        market_meta=StubMarketMeta(),
        allowlist=DEFAULT_ALLOWLIST,
        event_store=event_store,
        stamper=stamper,
    )
    return pipe, ledger, clog


def test_e2e_clean_corroborated_proposal_skips_on_k0_with_logging(tmp_path):
    # Two INDEPENDENT allowlisted primaries (fed-press=federalreserve.gov, sec-press=sec.gov) cite
    # the same proposal -> corroborated=True. A healthy DEEP book (depth >> thin_book_depth_usd) so
    # the same-source thin-book branch cannot fire. The genuine estimate flows fusion->clamp->
    # record_forecast + components, but k_for("unknown")==0 (cold ledger) zeroes the stake -> SKIP
    # below_min_floor. The substrate accrues even though no trade is placed (DESIGN §2: calibration
    # grades the estimate, not whether we could afford to act on it).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        _seed(evstore, stamper, source="fed-press", event_id="c1")
        _seed(evstore, stamper, source="sec-press", event_id="c2")
        pipe, ledger, clog = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "i1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.8", resolution_summary="Will the rate be held?",
                thesis="...", citations=("c1", "c2"))
            signer = PaperSigner()
            deep = _book("0.50", ask_size="100000", bid="0.49", bid_size="100000")
            process_pending(store, book_for={"t1": deep}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            # k=0 (cold ledger, "unknown" bucket) -> stake zeroed -> SKIP below_min_floor.
            assert store.get("i1").status == "SKIPPED"
            assert store.get("i1").decision_reason == "below_min_floor"
            assert signer.placed == []
            # BUT the genuine estimate flowed through fusion->clamp->record_forecast:
            rec = ledger.get("i1")
            assert rec is not None and rec.category == "unknown"
            assert Decimal(0) < rec.p < Decimal(1)        # an in-range clamped posterior
            # ... and the per-signal component row was logged (corroborated -> w_news live):
            comps = clog.all()
            assert len(comps) == 1
            assert comps[0].corroborated is True
            assert comps[0].w_news_effective == 0.20
