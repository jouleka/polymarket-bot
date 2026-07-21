"""Tests for the ERS poll-loop (S3 / POL-5 slice 2).

process_pending wires the chokepoint to the validator: poll PROPOSED intents, RE-FETCH the
live book (never trust the proposed price), run evaluate_intent vs the current portfolio,
record_decision + audit, fold each ACCEPT into the working portfolio (so cross-intent caps
hold), and call the signer SEAM on ACCEPT (a PaperSigner stub in slice 2; the real Rust
signer is S2/POL-4). These pin: ACCEPT path (status + paper order + fold), SKIP/REJECT (no
order), live-book re-fetch (stale -> REJECT), missing book (fail-closed REJECT), and the
cross-intent fold contract.
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers.breaker import DrawdownBreaker
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.market_meta import (
    MarketMetadata,
    MarketMetadataUnavailable,
    ResolutionSubjectMetadata,
    StubMarketMeta,
)
from polybot.ers.service import PaperSigner, process_pending
from polybot.ers.validator import ClusterView, OpenPosition, Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_accept_records_status_places_paper_order_and_folds_portfolio(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")  # per_trade cap
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")


def test_skip_records_status_and_places_no_order(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, p="0.50"))  # p == price -> no edge
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "SKIPPED" and store.get("i1").decision_reason == "no_edge"
        assert signer.placed == []


def test_re_fetches_the_live_book_and_rejects_a_stale_one(tmp_path):
    # The proposal carries a target_price, but the ERS re-prices off the LIVE book and
    # refuses a stale one -- never trusts the proposed price.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        stale = _book("0.50")
        stale.mark_stale()
        signer = PaperSigner()
        process_pending(store, book_for={"t1": stale}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "book_stale"
        assert signer.placed == []


def test_missing_book_is_fail_closed_reject(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={}.get,  # no book for t1
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "no_book"
        assert signer.placed == []


def test_folds_accepts_so_cross_intent_total_open_holds(tmp_path):
    # Two intents that each individually fit; accepting the first consumes the total_open
    # headroom, so the second must SKIP. Proves the loop threads the portfolio between intents.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, token_id="t1", condition_id="ma", event_id="ea"))
        store.propose_trade("i2", **dict(_P, token_id="t2", condition_id="mb", event_id="eb"))
        start = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition("mz", "ez", "sz", "cz", Decimal("50"), False),))  # $50 at risk -> $10 headroom
        books = {"t1": _book("0.50"), "t2": _book("0.50")}
        process_pending(store, book_for=books.get, portfolio=start, caps=RiskCaps(), signer=PaperSigner())

        assert store.get("i1").status == "ACCEPTED" and store.get("i1").decision_stake_usd == Decimal("10")
        assert store.get("i2").status == "SKIPPED"  # folding i1 left $0 total_open headroom


def test_a_raising_intent_is_isolated_and_the_batch_continues(tmp_path):
    # A malformed intent (here: its live-book fetch raises) must NOT wedge the FIFO queue
    # head -- it is failed closed to REJECT(internal_error) + audited, and the rest process.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("bad", **dict(_P, token_id="boom"))
        store.propose_trade("good", **dict(_P, token_id="t1", condition_id="mb", event_id="eb"))

        def book_for(token_id):
            if token_id == "boom":
                raise RuntimeError("rpc blew up")
            return _book("0.50")

        signer = PaperSigner()
        process_pending(store, book_for=book_for, portfolio=Portfolio(nav=Decimal("300")),
                        caps=RiskCaps(), signer=signer)

        assert store.get("bad").status == "REJECTED" and store.get("bad").decision_reason == "internal_error"
        assert store.get("good").status == "ACCEPTED"
        assert [o["intent_id"] for o in signer.placed] == ["good"]


# --- slice-3: L7 breaker gating + co-move ClusterView wiring + mark-field fold ---------------

class _FakeClusterModel:
    """Returns a fixed ClusterView regardless of token_ids -- pins that the service applies the
    model's verdict (ClusterModel.view itself is covered in test_ers_comove)."""

    def __init__(self, view):
        self._view = view

    def view(self, token_ids):
        return self._view


def _open(token, entry, risk, *, cluster="cz"):
    return OpenPosition("m", "e", "s", cluster, Decimal(risk), False,
                        token_id=token, entry_price=Decimal(entry))


def test_l7_freeze_rejects_new_intents_without_placing(tmp_path):
    # an open position marked into the freeze band (drawdown ~$19.20) -> the breaker freezes adds,
    # so an otherwise-acceptable PROPOSED intent is REJECTED(l7_freeze) and nothing is placed.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        caps = RiskCaps()
        portfolio = Portfolio(nav=Decimal("300"), positions=(_open("P", "0.50", "24"),))
        books = {"t1": _book("0.50"), "P": _book("0.12", bid="0.08")}  # P mid 0.10 -> drawdown 19.2
        signer = PaperSigner()
        process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps, signer=signer,
                        breaker=DrawdownBreaker(caps, clock=lambda: 0))

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "l7_freeze"
        assert signer.placed == []


def test_l7_flatten_signals_exit_and_blocks_new_intents(tmp_path):
    # two positions marked to a >$30 portfolio drawdown -> FLATTEN: the seam is signalled to exit
    # and new intents are REJECTED(l7_flatten).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        caps = RiskCaps()
        positions = (_open("P1", "0.50", "18"), _open("P2", "0.50", "18"))
        portfolio = Portfolio(nav=Decimal("300"), positions=positions)
        books = {"t1": _book("0.50"), "P1": _book("0.06", bid="0.04"), "P2": _book("0.06", bid="0.04")}
        signer = PaperSigner()
        process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps, signer=signer,
                        breaker=DrawdownBreaker(caps, clock=lambda: 0))

        assert store.get("i1").status == "REJECTED" and store.get("i1").decision_reason == "l7_flatten"
        assert signer.placed == []
        assert signer.flattened  # the exit was signalled through the seam


def test_accept_folds_the_l7_mark_fields(tmp_path):
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)  # token t1
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=PaperSigner())
        pos = final.positions[0]
        assert pos.token_id == "t1"
        assert pos.entry_price == Decimal("0.50")  # = the executable price the ERS re-priced at
        assert pos.frozen is False


def test_warm_cluster_model_applies_the_per_cluster_cap(tmp_path):
    # a warm co-move verdict (rho=1) + an existing $4 position in the same cluster (cluster_id =
    # event_id placeholder "e1") -> cluster_cap $12 - $4 = $8 binds, and the new position folds
    # matrix_cold=False (warm leaves the cold count gate).
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, token_id="t1", condition_id="m1", event_id="e1"))
        existing = OpenPosition("mx", "e1", "sx", "e1", Decimal("4"), False,
                                token_id="P", entry_price=Decimal("0.50"))
        portfolio = Portfolio(nav=Decimal("300"), positions=(existing,))
        cm = _FakeClusterModel(ClusterView(warm=True, rho=Decimal("1")))
        final = process_pending(store, book_for={"t1": _book("0.50")}.get, portfolio=portfolio,
                                caps=RiskCaps(), signer=PaperSigner(), cluster_model=cm)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("8")
        assert store.get("i1").decision_reason == "per_cluster_cap"
        assert final.positions[-1].matrix_cold is False


# --- S6: HermesPipeline wiring ---------------------------------------------------------------
# These reuse the module-level _book / _P / _store helpers already defined at the top of this file.

def test_pipeline_none_is_exactly_the_slice3_accept_path(tmp_path):
    # The S6 seam is purely additive: with pipeline omitted (None), process_pending behaves
    # identically to slice-3 -- the i1 ACCEPT, $12 per_trade stake, paper place, and fold all hold.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, pipeline=None)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")


# --- S6 local fakes + builders (the wiring-under-test calls these collaborators) -------------
from polybot.ers.service import HermesPipeline
from polybot.fusion.component_log import ComponentLog
# Capture the GENUINE fuse() at import time, BEFORE any test monkeypatches fusion.engine.fuse to a
# fake. _real_fuse_capture (8g) needs the real fold; importing it later would grab the fake (the
# monkeypatch in _pipeline replaces the module attribute first).
from polybot.fusion.engine import fuse as _GENUINE_FUSE


class _Verdict:
    def __init__(self, refused, reason, corroborated):
        self.refused = refused
        self.reason = reason
        self.corroborated = corroborated
        self.primary_groups = ()


class _DetectorVerdict:
    def __init__(self, action="FLAG_ONLY", p_flow=Decimal("0")):
        self.action = action
        self.pull_quotes = False
        self.p_flow = p_flow
        self.reasons = ()


class _FakeDetectors:
    def __init__(self, verdict=None):
        self._verdict = verdict or _DetectorVerdict()
        self.calls = []

    def evaluate(self, intent, *, inputs):
        self.calls.append((intent.intent_id, inputs))
        return self._verdict


class _FakeFusionResult:
    def __init__(self, p_final, components, w_news_effective):
        self.p_final = p_final
        self.components = components
        self.w_news_effective = w_news_effective


class _FakeCalibGate:
    """k_for returns a fixed k (Decimal); clamp_p returns a fake AnchorResult or raises (anchor_error)."""
    def __init__(self, *, k=Decimal("0"), clamp_to=None, raises=None):
        self._k = k
        self._clamp_to = clamp_to
        self._raises = raises
        self.clamp_calls = []
        self.clamp_metadata_calls = []

    def k_for(self, category):
        return self._k

    def clamp_p(self, p, market_mid, *, question_text, seconds_to_resolution, corroborated):
        self.clamp_calls.append((p, market_mid, corroborated))
        self.clamp_metadata_calls.append((question_text, seconds_to_resolution))
        if self._raises is not None:
            raise self._raises
        target = p if self._clamp_to is None else self._clamp_to
        return _AnchorResult(target)


class _AnchorResult:
    def __init__(self, p_clamped):
        self.p_clamped = p_clamped
        self.shrunk = False
        self.reason = "within_band"


class _StubMeta(StubMarketMeta):
    def __init__(self, category="unknown", seconds=10**12):
        self._cat = category
        self._secs = seconds

    def metadata_for(self, intent):
        return MarketMetadata(self._cat, intent.resolution_summary, self._secs)

    # Legacy accessors remain so this fixture can expose whether service.py still calls them.
    def category_for(self, intent):
        return self._cat

    def question_text_for(self, intent):
        return intent.resolution_summary

    def seconds_to_resolution_for(self, intent):
        return self._secs


def _pipeline(tmp_path, monkeypatch, *, detectors=None, truth=None, calib=None, meta=None,
              fusion_result=None, evidence_categories=None):
    """Build a HermesPipeline with fakes, monkeypatching the two module-level collaborators
    (fusion.engine.fuse and truthgate.gate.verify -- the function-local import sites in the loop)
    so we drive the loop precisely."""
    from polybot.core.clock import MonotonicStamper
    from polybot.calibration.ledger import ForecastLedger

    stamper = MonotonicStamper(clock=lambda: 1)  # deterministic; the stamper itself enforces strict-mono
    ledger = ForecastLedger(str(tmp_path / "f.db"), stamper)
    clog = ComponentLog(str(tmp_path / "c.db"), stamper=stamper)

    # Patch the truth-gate import target used inside _process_intent_pipeline.
    import polybot.truthgate.gate as gate_mod
    monkeypatch.setattr(gate_mod, "verify",
                        lambda *a, **k: truth or _Verdict(False, None, True), raising=True)
    # Patch the fusion fuse() the same way (local import resolves to fusion.engine.fuse).
    import polybot.fusion.engine as fusion_mod
    fr = fusion_result or _FakeFusionResult(
        Decimal("0.70"),
        {"p_news": Decimal("0.9"), "p_base": Decimal("0.5"),
         "p_micro": Decimal("0.5"), "p_flow": Decimal("0.5")},
        0.20)
    monkeypatch.setattr(fusion_mod, "fuse", lambda *a, **k: fr, raising=True)

    pipeline_args = dict(
        calib_gate=calib or _FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.70")),
        fusion_config=object(),
        truth_gate_config=object(),
        detectors=detectors or _FakeDetectors(),
        forecast_ledger=ledger,
        component_log=clog,
        market_meta=meta or _StubMeta(),
        allowlist=(),
        event_store=object(),
        stamper=stamper,
    )
    if evidence_categories is not None:
        pipeline_args["evidence_categories"] = evidence_categories
    pipe = HermesPipeline(**pipeline_args)
    return pipe, ledger, clog


def test_pipeline_detector_avoid_rejects_before_sizing(tmp_path, monkeypatch):
    # A defensive detector AVOID verdict must REJECT(detector_avoid) BEFORE fusion/clamp/sizing,
    # and place no order. (calib_gate.clamp_p is never reached -> no clamp call recorded.)
    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch,
                                   detectors=_FakeDetectors(_DetectorVerdict(action="AVOID")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "detector_avoid"
        assert signer.placed == []
        assert pipe.calib_gate.clamp_calls == []   # never sized -- rejected before fusion/clamp
        assert ledger.all() == []                  # not a genuine estimate -> no forecast logged


def test_pipeline_truth_gate_same_source_collusion_rejects_no_signer_no_forecast(tmp_path, monkeypatch):
    # An injection signature (truth-gate refuses with same_source_collusion) must REJECT, never
    # reach the signer, and record NO forecast (refused evidence is not a genuine estimate).
    from polybot.truthgate.gate import REASON_SAME_SOURCE
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        truth=_Verdict(refused=True, reason=REASON_SAME_SOURCE, corroborated=False))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "same_source_collusion"
        assert signer.placed == []
        assert pipe.calib_gate.clamp_calls == []
        assert ledger.all() == []
        assert clog.all() == ()


def test_pipeline_truth_gate_refuse_maps_truth_gate_refuse_reason(tmp_path, monkeypatch):
    # Zero allowlisted primaries -> truth_gate_refuse (distinct from same_source_collusion).
    from polybot.truthgate.gate import REASON_TRUTH_GATE_REFUSE
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        truth=_Verdict(refused=True, reason=REASON_TRUTH_GATE_REFUSE, corroborated=False))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").decision_reason == "truth_gate_refuse"
        assert signer.placed == [] and ledger.all() == []


# POL-14: one real metadata result, typed fail-closed rejection before logging.


class _RecordingMeta(StubMarketMeta):
    def __init__(self, result=None, raises=None):
        self.result = result or MarketMetadata("politics", "Gamma canonical question", 123)
        self.raises = raises
        self.calls = []

    def metadata_for(self, intent):
        self.calls.append((intent.condition_id, intent.token_id))
        if self.raises is not None:
            raise self.raises
        return self.result


def test_only_explicit_stub_market_meta_may_write_legacy_forecast(tmp_path, monkeypatch):
    class DuckTypedMeta:
        def metadata_for(self, intent):
            return MarketMetadata("politics", "apparently valid", 123)

    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch, meta=DuckTypedMeta())

    import polybot.fusion.engine as fusion_mod

    def forbidden_fusion(*args, **kwargs):
        raise AssertionError("fusion ran without canonical resolution identity")

    monkeypatch.setattr(fusion_mod, "fuse", forbidden_fusion, raising=True)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(
            store, book_for={"t1": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )

        assert store.get("i1").decision_reason == "resolution_identity_unavailable"
        assert ledger.all() == [] and clog.all() == () and signer.placed == []


@pytest.mark.parametrize("mismatch", ["event_id", "condition_id", "category", "token_id"])
def test_typed_resolution_subject_must_match_the_intent_before_component_write(
        tmp_path, monkeypatch, mismatch):
    condition_id = "0x" + "ab" * 32
    intent_values = dict(_P, token_id="101", condition_id=condition_id)

    class MismatchedMeta:
        def metadata_for(self, intent):
            return MarketMetadata("politics", "canonical question", 123)

        def resolution_subject_for(self, intent):
            values = dict(
                event_id="e1", condition_id=condition_id, category="politics",
                token_id="101", outcome_slot=0, sibling_token_ids=("101", "202"),
            )
            if mismatch == "event_id":
                values["event_id"] = "other-event"
            elif mismatch == "condition_id":
                values["condition_id"] = "0x" + "cd" * 32
            elif mismatch == "category":
                values["category"] = "sports"
            else:
                values["token_id"] = "202"
                values["outcome_slot"] = 1
            return ResolutionSubjectMetadata(**values)

    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch, meta=MismatchedMeta())
    with _store(str(tmp_path / f"{mismatch}.db")) as store:
        store.propose_trade("i1", **intent_values)
        signer = PaperSigner()
        process_pending(
            store, book_for={"101": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )

        assert store.get("i1").decision_reason == "resolution_identity_unavailable"
        assert ledger.all() == [] and clog.all() == () and signer.placed == []


def test_ers_terminal_race_never_writes_forecast_or_reaches_signing(tmp_path, monkeypatch):
    def add_receipt(ledger, terminal_id):
        ledger._conn.execute(
            "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
            "VALUES (?, ?, ?)",
            ("m1", terminal_id, b"terminal"),
        )
        ledger._conn.commit()

    known_dir = tmp_path / "known"
    known_dir.mkdir()
    pipe, ledger, clog = _pipeline(known_dir, monkeypatch)
    add_receipt(ledger, "known-terminal")
    with _store(str(known_dir / "i.db")) as store:
        store.propose_trade("known", **_P)
        signer = PaperSigner()
        process_pending(
            store, book_for={"t1": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )
        assert store.get("known").decision_reason == "market_resolved"
        assert ledger.all() == [] and clog.all() == () and signer.placed == []

    race_dir = tmp_path / "race"
    race_dir.mkdir()
    pipe, ledger, clog = _pipeline(race_dir, monkeypatch)
    original_record = clog.record

    def record_then_resolve(*args, **kwargs):
        inserted = original_record(*args, **kwargs)
        add_receipt(ledger, "racing-terminal")
        return inserted

    clog.record = record_then_resolve
    with _store(str(race_dir / "i.db")) as store:
        store.propose_trade("racing", **_P)
        signer = PaperSigner()
        final = process_pending(
            store, book_for={"t1": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )
        assert store.get("racing").decision_reason == "market_resolved"
        assert ledger.all() == [] and len(clog.all()) == 1
        assert signer.placed == [] and final.positions == ()


def test_ers_post_forecast_terminal_race_cannot_reach_signing(tmp_path, monkeypatch):
    calib = _FakeCalibGate(k=Decimal("1"), clamp_to=Decimal("0.90"))
    pipe, ledger, _clog = _pipeline(tmp_path, monkeypatch, calib=calib)

    def resolve_during_calibration(category):
        ledger._conn.execute(
            "INSERT INTO resolution_receipts(condition_id, terminal_id, payload) "
            "VALUES (?, ?, ?)",
            ("m1", "racing-terminal", b"terminal"),
        )
        ledger._conn.commit()
        return Decimal("1")

    calib.k_for = resolve_during_calibration
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(
            store, book_for={"t1": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )

        assert ledger.get("i1") is not None  # the race deliberately wins after this commit
        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "market_resolved"
        assert signer.placed == [] and final.positions == ()


def test_pipeline_metadata_unavailable_maps_distinct_reason_and_logs_nothing(tmp_path, monkeypatch):
    meta = _RecordingMeta(raises=MarketMetadataUnavailable("missing Gamma metadata"))
    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch, meta=meta)

    import polybot.fusion.engine as fusion_mod

    def forbidden_fusion(*args, **kwargs):
        raise AssertionError("fusion ran before the metadata availability gate")

    monkeypatch.setattr(fusion_mod, "fuse", forbidden_fusion, raising=True)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "market_meta_unavailable"
        assert meta.calls == [("m1", "t1")]
        assert signer.placed == []
        assert ledger.all() == []
        assert clog.all() == ()
        assert pipe.calib_gate.clamp_calls == []


def test_pipeline_rejects_category_without_reviewed_evidence_before_any_write(
        tmp_path, monkeypatch):
    meta = _RecordingMeta(MarketMetadata("sports", "Gamma sports question", 123))
    pipe, ledger, clog = _pipeline(
        tmp_path,
        monkeypatch,
        meta=meta,
        evidence_categories=frozenset({
            "politics", "geopolitics", "crypto", "finance", "econ",
        }),
    )

    import polybot.fusion.engine as fusion_mod

    def forbidden_fusion(*args, **kwargs):
        raise AssertionError("fusion ran for an unsupported evidence category")

    monkeypatch.setattr(fusion_mod, "fuse", forbidden_fusion, raising=True)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(
            store, book_for={"t1": _book("0.50")}.get,
            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer,
            pipeline=pipe,
        )

        assert store.get("i1").decision_reason == "evidence_category_unsupported"
        assert ledger.all() == [] and clog.all() == () and signer.placed == []


@pytest.mark.parametrize("bug", [
    RuntimeError("implementation bug"),
    KeyError("implementation lookup bug"),
    IndexError("implementation index bug"),
    TypeError("implementation type bug"),
    ValueError("implementation value bug"),
    AttributeError("implementation attribute bug"),
])
def test_pipeline_unexpected_metadata_bug_stays_internal_error(tmp_path, monkeypatch, bug):
    meta = _RecordingMeta(raises=bug)
    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch, meta=meta)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=PaperSigner(), pipeline=pipe)
        assert store.get("i1").decision_reason == "internal_error"
        assert ledger.all() == [] and clog.all() == ()


def test_pipeline_consumes_one_metadata_object_and_threads_gamma_values(tmp_path, monkeypatch):
    meta = _RecordingMeta(MarketMetadata("crypto", "Gamma question, not proposal", 321))
    calib = _FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.70"))
    pipe, ledger, clog = _pipeline(tmp_path, monkeypatch, meta=meta, calib=calib)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, resolution_summary="Hermes proposal summary"))
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=PaperSigner(), pipeline=pipe)

        assert meta.calls == [("m1", "t1")]  # one internally-consistent lookup
        assert calib.clamp_metadata_calls == [("Gamma question, not proposal", 321)]
        assert ledger.get("i1").category == "crypto"
        assert len(clog.all()) == 1


def test_pipeline_clamp_p_raise_maps_to_distinct_anchor_error(tmp_path, monkeypatch):
    # A non-finite anchor makes calib_gate.clamp_p raise ValueError. It MUST be caught explicitly
    # and mapped to the DISTINCT reason "anchor_error" -- never swallowed into "internal_error".
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch,
        calib=_FakeCalibGate(k=Decimal("0"), raises=ValueError("anchor_gate: non-finite p")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert store.get("i1").decision_reason == "anchor_error"  # NOT "internal_error"
        assert signer.placed == []
        assert ledger.all() == []   # raised before record_forecast -> no estimate logged


def test_pipeline_substitutes_fused_clamped_p_into_the_validator(tmp_path, monkeypatch):
    # Proposal's raw p=0.50 (== price -> no edge). The pipeline fuses+clamps to 0.90, which the
    # validator sizes off -> ACCEPT (not the SKIP no_edge the raw p would give). Pin that the
    # posterior, not Hermes's raw p, drove the validator. Use k=1 so sizing isn't zeroed.
    fr = _FakeFusionResult(Decimal("0.90"),
                           {"p_news": Decimal("0.95"), "p_base": Decimal("0.50"),
                            "p_micro": Decimal("0.50"), "p_flow": Decimal("0.50")}, 0.20)
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch, fusion_result=fr,
        calib=_FakeCalibGate(k=Decimal("1"), clamp_to=Decimal("0.90")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, p="0.50"))  # raw p == 0.50 == price -> would be no_edge
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, pipeline=pipe)

        assert store.get("i1").status == "ACCEPTED"            # posterior 0.90 has edge over price 0.50
        assert store.get("i1").decision_stake_usd == Decimal("12")  # per_trade cap binds at k=1
        assert pipe.calib_gate.clamp_calls[0][0] == Decimal("0.90")  # fused p_final fed to clamp_p
        assert len(final.positions) == 1
        # the forecast records the clamped posterior, not the raw 0.50
        assert ledger.get("i1").p == Decimal("0.90")


# --- S4.2 (POL-6): GTD bracket staging on ACCEPT via opt-in gtd_for -------------------------


def test_gtd_bracket_is_staged_for_each_accept(tmp_path):
    # On ACCEPT the ERS pre-stages a protective GTD exit bracket on the signer right after place.
    from polybot.ers.gtd import derive_bracket
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        gtd_for = lambda decision, position, *, caps, standing_exit_total: derive_bracket(
            decision, position, caps=caps, expiry=1700, standing_exit_total=standing_exit_total)
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, gtd_for=gtd_for)
        assert store.get("i1").status == "ACCEPTED"
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        # The protective standing exit was staged for the accepted position.
        assert len(signer.gtd_exits) == 1
        assert signer.gtd_exits[0]["token_id"] == "t1"
        assert signer.gtd_exits[0]["size"] == Decimal("12")     # == the per_trade-capped stake


def test_no_gtd_staging_when_gtd_for_is_none(tmp_path):
    # gtd_for=None (the default) == today's behavior: no GTD brackets staged. Guards the 469.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(), signer=signer)
        assert store.get("i1").status == "ACCEPTED"
        assert signer.gtd_exits == []


def test_pipeline_records_forecast_and_components_even_when_k0_skips(tmp_path, monkeypatch):
    # k=0 -> frac_eff=0 -> stake below floor -> SKIP(below_min_floor). The estimate is STILL a
    # genuine forecast, so record_forecast + ComponentLog.record happen BEFORE evaluate_intent --
    # calibration grades the estimate, not whether we could afford to act on it (DESIGN §2).
    fr = _FakeFusionResult(Decimal("0.80"),
                           {"p_news": Decimal("0.90"), "p_base": Decimal("0.50"),
                            "p_micro": Decimal("0.50"), "p_flow": Decimal("0.50")}, 0.20)
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch, fusion_result=fr,
        calib=_FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.80")))
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "SKIPPED"
        assert store.get("i1").decision_reason == "below_min_floor"  # k=0 zeroes the stake
        assert signer.placed == []
        # estimate logged regardless of the SKIP:
        rec = ledger.get("i1")
        assert rec is not None and rec.p == Decimal("0.80") and rec.category == "unknown"
        assert rec.market_mid == Decimal("0.255")  # midpoint of bid 0.01 / ask 0.50
        comps = clog.all()
        assert len(comps) == 1  # one per-signal row logged


def test_pipeline_corroboration_threads_into_fusion_and_anchor(tmp_path, monkeypatch):
    # Real FusionEngine.fuse this time (un-patch it). corroborated=True -> w_news_effective=0.20;
    # corroborated=False -> w_news_effective=0.0 (Hermes informational-only). The same corroborated
    # bool also reaches clamp_p (anchor band width). Pin both via the ComponentLog + clamp_calls.
    import polybot.fusion.engine as fusion_mod

    def _run(corroborated):
        # Each run gets an ISOLATED store dir: _pipeline's ForecastLedger/ComponentLog are keyed
        # by forecast_id ("i1"), so sharing one dir across both runs would let the second run's
        # idempotent INSERT-OR-IGNORE collide with the first -- the flip we're pinning would be
        # masked by the first run's already-logged row.
        run_dir = tmp_path / f"run_{corroborated}"
        run_dir.mkdir()
        pipe, ledger, clog = _pipeline(
            run_dir, monkeypatch,
            truth=_Verdict(refused=False, reason=None, corroborated=corroborated),
            calib=_FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.50")))
        # un-patch fuse: use the REAL fold so w_news_effective is genuinely derived.
        monkeypatch.setattr(fusion_mod, "fuse", _real_fuse_capture(pipe), raising=True)
        with _store(str(run_dir / "i.db")) as store:
            store.propose_trade("i1", **dict(_P, p="0.95"))
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=PaperSigner(), pipeline=pipe)
        return clog, pipe

    clog_t, pipe_t = _run(True)
    clog_f, pipe_f = _run(False)
    # w_news_effective recorded in the component log flips with corroboration:
    assert clog_t.all()[0].w_news_effective == 0.20
    assert clog_f.all()[0].w_news_effective == 0.0
    # the same corroborated bool reaches clamp_p:
    assert pipe_t.calib_gate.clamp_calls[0][2] is True
    assert pipe_f.calib_gate.clamp_calls[0][2] is False


def _real_fuse_capture(pipe):
    # Rebind the pipeline's fusion_config to a real FusionConfig and call the GENUINE fuse() (captured
    # at import time, above), so the w_news gating is genuinely exercised (not a fake constant).
    from polybot.fusion.engine import FusionConfig
    cfg = FusionConfig(w_news=0.20, w_base=0.30, w_micro=0.0, w_flow=0.0, clip_logodds=2.0)
    object.__setattr__(pipe, "fusion_config", cfg)
    return lambda mid, **kw: _GENUINE_FUSE(mid, **{**kw, "config": cfg})


def test_pipeline_non_finite_p_news_rejects_with_no_orphan_in_either_store(tmp_path, monkeypatch):
    # Hermes CAN supply a non-finite p (Decimal("NaN") round-trips through propose_trade). It enters
    # the REAL fuse as p_news (a non-_in_unit signal -> 0 delta, so p_final stays finite and the
    # clamp succeeds), but component_log.record fails-loud on the non-finite raw p_news. The chain
    # must REJECT cleanly with NO orphan: BOTH the forecast ledger AND the component log stay empty.
    # (Pre-fix, record_forecast ran first -> a committed forecast row with no component = an orphan.)
    import polybot.fusion.engine as fusion_mod
    pipe, ledger, clog = _pipeline(
        tmp_path, monkeypatch, calib=_FakeCalibGate(k=Decimal("0"), clamp_to=Decimal("0.50")))
    # REAL fold so components["p_news"] genuinely carries the NaN (the fake would mask it).
    monkeypatch.setattr(fusion_mod, "fuse", _real_fuse_capture(pipe), raising=True)
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **dict(_P, p="NaN"))  # non-finite p round-trips to Decimal("NaN")
        assert store.get("i1").p.is_finite() is False  # the orphan precondition really holds
        signer = PaperSigner()
        process_pending(store, book_for={"t1": _book("0.50")}.get,
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, pipeline=pipe)

        assert store.get("i1").status == "REJECTED"
        assert signer.placed == []
        assert ledger.all() == []   # NO orphan forecast row
        assert clog.all() == ()     # and no component row either


# --- S4.1: SafetyController loop gate (controller= kwarg) -------------------------------------
from polybot.ers import safety as _safety
from polybot.ers.safety import SafetyController


def _running_controller(tmp_path, **kw):
    """A controller already transitioned to RUNNING (so it does not block the loop)."""
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0, **kw)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl, store


def test_controller_none_is_exactly_todays_accept_path(tmp_path):
    # The S4.1 seam is purely additive: controller omitted (None) => identical to slice-3/S6.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, controller=None)

        assert store.get("i1").status == "ACCEPTED"
        assert store.get("i1").decision_stake_usd == Decimal("12")
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1 and final.positions[0].worst_case_risk == Decimal("12")


def test_running_controller_lets_the_accept_path_through(tmp_path):
    # A RUNNING controller imposes no op-block -> the loop falls through to the normal ACCEPT.
    ctl, ctl_store = _running_controller(tmp_path)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i1").status == "ACCEPTED"
            assert [o["token_id"] for o in signer.placed] == ["t1"]
    finally:
        ctl_store.close()


def _halted_controller(tmp_path):
    store = IntentStore(str(tmp_path / "ctl.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # starts HALTED
    return ctl, store


def test_halted_controller_blocks_ahead_of_an_otherwise_clean_loop(tmp_path):
    # A HALTED controller blocks EVERY pending intent with unclean_restart, before any sizing.
    ctl, ctl_store = _halted_controller(tmp_path)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            signer = PaperSigner()
            process_pending(store, book_for={"t1": _book("0.50")}.get,
                            portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                            signer=signer, controller=ctl)
            assert store.get("i1").status == "REJECTED"
            assert store.get("i1").decision_reason == "unclean_restart"
            assert signer.placed == []
    finally:
        ctl_store.close()


def test_op_flatten_dominates_an_l7_freeze(tmp_path):
    # The controller is FLATTENING; the L7 breaker (if it ran) would only FREEZE_ADDS. Op-FLATTEN
    # must dominate: the reason is op_flatten (NOT l7_freeze), and the breaker is never consulted.
    ctl, ctl_store = _halted_controller(tmp_path)
    ctl.set_state(_safety.FLATTENING, reason=_safety.REASON_OP_FLATTEN)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            caps = RiskCaps()
            # A position marked into the L7 FREEZE band (drawdown ~$19.20) -- the breaker WOULD
            # set l7_freeze, but the op-state blocks first.
            portfolio = Portfolio(nav=Decimal("300"), positions=(_open("P", "0.50", "24"),))
            books = {"t1": _book("0.50"), "P": _book("0.12", bid="0.08")}
            signer = PaperSigner()
            process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps,
                            signer=signer, controller=ctl,
                            breaker=DrawdownBreaker(caps, clock=lambda: 0))
            assert store.get("i1").status == "REJECTED"
            assert store.get("i1").decision_reason == "op_flatten"  # NOT l7_freeze
            assert signer.placed == []
            # Op-FLATTEN de-risked via the controller (flatten signalled on the ERS's signer).
            assert signer.flattened  # the op-flatten exit was signalled through the seam
    finally:
        ctl_store.close()


def test_explicit_kill_dominates_an_l7_flatten(tmp_path):
    # The controller is HALTED via an explicit KILL; even a position that WOULD trip the L7
    # FLATTEN must be blocked under the op reason (the op-state is read first; the breaker is
    # never consulted). Pin that the kill reason dominates and no l7_flatten leaks through.
    ctl, ctl_store = _halted_controller(tmp_path)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    ctl.set_state(_safety.HALTED, reason=_safety.REASON_L8_KILL)
    try:
        with _store(str(tmp_path / "i.db")) as store:
            store.propose_trade("i1", **_P)
            caps = RiskCaps()
            positions = (_open("P1", "0.50", "18"), _open("P2", "0.50", "18"))
            portfolio = Portfolio(nav=Decimal("300"), positions=positions)
            books = {"t1": _book("0.50"),
                     "P1": _book("0.06", bid="0.04"), "P2": _book("0.06", bid="0.04")}
            signer = PaperSigner()
            process_pending(store, book_for=books.get, portfolio=portfolio, caps=caps,
                            signer=signer, controller=ctl,
                            breaker=DrawdownBreaker(caps, clock=lambda: 0))
            # HALTED (via KILL) blocks; the reason is the specific kill reason (l8_kill).
            assert store.get("i1").decision_reason == _safety.REASON_L8_KILL
            assert store.get("i1").decision_reason != "l7_flatten"
            assert signer.placed == []
            # HALTED does NOT itself de-risk (only FLATTENING does), so the breaker's flatten
            # never ran -- nothing was signalled to exit.
            assert signer.flattened == []
            # The kill is in the op-audit trail.
            assert any(r["reason"] == _safety.REASON_L8_KILL for r in ctl_store.op_audit_log())
    finally:
        ctl_store.close()


# --- S4.5a (POL-6): durable fills ledger via the fill_sink seam ------------------------------
from polybot.ers.service import make_fill_sink


def test_no_fill_recorded_when_fill_sink_is_none(tmp_path):
    # fill_sink=None (the default) == today's behavior: an ACCEPT places + folds but writes NO
    # fills row. Guards the 520 baseline -- the seam is purely additive.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, fill_sink=None)
        assert store.get("i1").status == "ACCEPTED"
        assert [o["token_id"] for o in signer.placed] == ["t1"]
        assert len(final.positions) == 1
        assert store.fills_log() == []   # NO durable fill recorded


def test_wired_fill_sink_records_one_fill_per_accept_decimal_exact(tmp_path):
    # A make_fill_sink(store) wired sink records exactly one fill per ACCEPT, with shares =
    # worst_case_risk / entry_price (Decimal-exact), side="BUY", and the folded position's ids.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)  # token t1, condition m1, event e1
        signer = PaperSigner()
        final = process_pending(store, book_for={"t1": _book("0.50")}.get,
                                portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                                signer=signer, fill_sink=make_fill_sink(store))
        assert store.get("i1").status == "ACCEPTED"
        pos = final.positions[-1]   # stake $12 @ entry 0.50
        fills = store.fills_log()
        assert len(fills) == 1
        f = fills[0]
        assert f["intent_id"] == "i1" and f["token_id"] == "t1"
        assert f["condition_id"] == "m1" and f["event_id"] == "e1"
        assert f["side"] == "BUY"
        assert f["price_exec"] == Decimal("0.50") == pos.entry_price
        assert f["worst_case_risk"] == Decimal("12") == pos.worst_case_risk
        # shares = worst_case_risk / entry_price = 12 / 0.50 = 24 (Decimal-exact, no float)
        assert f["shares"] == Decimal("24") and isinstance(f["shares"], Decimal)


def test_fill_sink_records_nothing_on_a_reject(tmp_path):
    # A REJECT (missing book -> no_book) never reaches the ACCEPT branch, so the wired sink writes
    # no fill -- recording is strictly on ACCEPT.
    with _store(str(tmp_path / "i.db")) as store:
        store.propose_trade("i1", **_P)
        signer = PaperSigner()
        process_pending(store, book_for={}.get,  # no book for t1 -> REJECT(no_book)
                        portfolio=Portfolio(nav=Decimal("300")), caps=RiskCaps(),
                        signer=signer, fill_sink=make_fill_sink(store))
        assert store.get("i1").status == "REJECTED"
        assert store.fills_log() == []
