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
from polybot.ers.market_meta import MarketRegistry, StubMarketMeta
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


def _build_pipeline(tmp_path, stamper, event_store, *, market_meta=None):
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
        market_meta=market_meta or StubMarketMeta(),
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


# POL-14 whole-slice composition: real registry metadata reaches the real S6 ledger.
def test_e2e_real_market_registry_replaces_unknown_bucket(tmp_path):
    token = "7704407378332423580507141839985172615515196706624243524491048428567892599013"
    sibling = "1959412866692185789324499315644486550124994570117004262795754352991182983341"
    registry = MarketRegistry.from_gamma_snapshots(
        [{
            "conditionId": "m1",
            "question": "Will Bitcoin reach the Gamma threshold?",
            "endDate": "2100-01-01T00:00:00Z",
            "clobTokenIds": f'["{token}", "{sibling}"]',
            "events": [{"id": "ev-market"}],
        }],
        [{"id": "ev-market", "tags": [
            {"id": "120", "label": "Finance"},
            {"id": "21", "label": "Crypto"},
        ]}],
        clock=lambda: 0,
    )

    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        _seed(evstore, stamper, source="fed-press", event_id="c1")
        _seed(evstore, stamper, source="sec-press", event_id="c2")
        pipe, ledger, clog = _build_pipeline(
            tmp_path, stamper, evstore, market_meta=registry)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            ProposeOnlyFacade(store).propose_trade(
                "i1", token_id=token, condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.8",
                resolution_summary="Hermes tries to call this politics",
                thesis="...", citations=("c1", "c2"))
            signer = PaperSigner()
            deep = _book("0.50", ask_size="100000", bid="0.49", bid_size="100000")
            process_pending(store, book_for={token: deep}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("i1").status == "SKIPPED"  # crypto bucket is cold -> k=0
            assert ledger.get("i1").category == "crypto"  # reviewed tags, not proposal text
            assert len(clog.all()) == 1
            assert signer.placed == []


def test_e2e_injection_proposal_rejected_same_source_collusion_never_signs(tmp_path):
    # THE LOAD-BEARING INJECTION PROBE. Indirect-prompt-injection signature: ONE fresh allowlisted
    # primary supplies the only p-moving citation (NO independent corroboration) AND a THIN, WIDE
    # book reads as a mid that that same fresh source could have pushed. The REAL truth-gate refuses
    # (same_source_collusion); the signer is NEVER reached and NO forecast/component is logged
    # (refused evidence is not a genuine estimate).
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        # A single fresh primary source (the injection vector) -- NO independent corroboration.
        _seed(evstore, stamper, source="fed-press", event_id="inj")
        pipe, ledger, clog = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "inj1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.95", size_usd_suggestion="100",
                p="0.99", p_confidence="0.9", resolution_summary="Will X happen?",
                thesis="...", citations=("inj",))
            signer = PaperSigner()
            # THIN + WIDE book: ask 0.70 x 10 = $7 depth and bid 0.66 x 10 = $6.6 depth, both well
            # below thin_book_depth_usd=$50; spread 0.70-0.66 = 0.04 >= thin_book_move=0.02. So
            # _is_thin_pushed(book, config) is True and, with exactly ONE fresh clean group, the
            # injection+pre-position signature the gate refuses is satisfied.
            thin = _book("0.70", ask_size="10", bid="0.66", bid_size="10")
            process_pending(store, book_for={"t1": thin}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("inj1").status == "REJECTED"
            assert store.get("inj1").decision_reason == "same_source_collusion"
            assert signer.placed == []           # the safety claim: never reached the signer
            assert ledger.get("inj1") is None     # refused -> no forecast logged
            assert clog.all() == ()               # ... and no component row either


def test_e2e_uncorroborated_proposal_is_mid_and_prior_only(tmp_path):
    # A single allowlisted primary -> NOT refused, but corroborated=False -> w_news_effective=0:
    # Hermes is informational-only and the posterior reduces to mid + base-rate prior (inside the
    # anchor band). A DEEP, healthy book so the same-source thin-book clause does NOT trip (present-
    # but-uncorroborated, not refused). The estimate is still logged; k=0 -> SKIP. Pin
    # w_news_effective == 0.0 and corroborated False on the recorded component row.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        _seed(evstore, stamper, source="fed-press", event_id="solo")
        pipe, ledger, clog = _build_pipeline(tmp_path, stamper, evstore)
        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "u1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.7", resolution_summary="Will the favorite win?",
                thesis="...", citations=("solo",))
            signer = PaperSigner()
            # DEEP, tight book so _is_thin_pushed is False -> single fresh source is present-
            # uncorroborated (NOT same_source_collusion).
            deep = _book("0.50", ask_size="100000", bid="0.49", bid_size="100000")
            process_pending(store, book_for={"t1": deep}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("u1").status == "SKIPPED"     # k=0 paper-only
            assert store.get("u1").decision_reason == "below_min_floor"
            assert signer.placed == []
            rec = ledger.get("u1")
            assert rec is not None                          # estimate logged (not refused)
            row = clog.all()[0]
            assert row.w_news_effective == 0.0              # Hermes informational-only
            assert row.corroborated is False


def test_e2e_detector_avoid_proposal_rejected_before_sizing(tmp_path):
    # A detector AVOID (toxic flow inputs) must REJECT(detector_avoid) BEFORE fusion/clamp/sizing
    # and place no order -- the defensive pre-gate. The loop calls detectors.evaluate with
    # DetectorInputs() zeros at S6 MVP (-> FLAG_ONLY), so to drive a genuine AVOID end-to-end we
    # wrap the REAL DetectorOrchestrator in a thin shim that forwards a toxic DetectorInputs set --
    # the SAME verified AVOID fixture as tests/test_detectors_orchestrator.py (classification=
    # INSIDER_LIKE forces AVOID even at a LOW band). The shim only swaps the inputs; the real
    # toxicity -> composite -> policy.decide chain produces the AVOID verdict.
    stamper = MonotonicStamper()
    with EventStore(str(tmp_path / "ev.db")) as evstore:
        _seed(evstore, stamper, source="fed-press", event_id="c1")
        _seed(evstore, stamper, source="sec-press", event_id="c2")

        class _AvoidOrchestrator:
            def __init__(self, inner):
                self._inner = inner

            def evaluate(self, intent, *, inputs):
                # Toxic flow: heavy one-sided buy imbalance + an INSIDER_LIKE classification (the
                # verified AVOID fixture). Forwarded into the REAL orchestrator's evaluate().
                toxic = DetectorInputs(buy_size=Decimal("900"), sell_size=Decimal("10"),
                                       baseline_mean=Decimal("0.2"), baseline_std=Decimal("0.05"),
                                       classification=INSIDER_LIKE, catalyst_present=False)
                return self._inner.evaluate(intent, inputs=toxic)

        ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
        clog = ComponentLog(str(tmp_path / "c.db"), stamper=stamper)
        pipe = HermesPipeline(
            calib_gate=CalibrationGate(ledger, PriorEngine(), CalibrationConfig()),
            fusion_config=_fusion_config(),
            truth_gate_config=_truth_config(),
            detectors=_AvoidOrchestrator(DetectorOrchestrator(DetectorConfig())),
            forecast_ledger=ledger,
            component_log=clog,
            market_meta=StubMarketMeta(),
            allowlist=DEFAULT_ALLOWLIST,
            event_store=evstore,
            stamper=stamper,
        )

        with IntentStore(str(tmp_path / "i.db"), stamper) as store:
            facade = ProposeOnlyFacade(store)
            facade.propose_trade(
                "d1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                target_price="0.50", max_price="0.60", size_usd_suggestion="100",
                p="0.95", p_confidence="0.8", resolution_summary="Will X?", thesis="...",
                citations=("c1", "c2"))
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, pipeline=pipe)

            assert store.get("d1").status == "REJECTED"
            assert store.get("d1").decision_reason == "detector_avoid"
            assert signer.placed == []
            assert ledger.get("d1") is None        # rejected before the estimate -> no forecast
            assert clog.all() == ()
