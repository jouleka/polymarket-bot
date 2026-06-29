"""Tests for the out-of-band supervisor + the wedged-process acceptance gate (S4.3 / POL-6).

Two layers:
  * FAST pure units (this block + Task 3): decide() dead-man timing with an injected
    clock + a file Heartbeat in tmp_path -- no process, no real sleep.
  * The ONE subprocess-backed ACCEPTANCE GATE (Task 4): a real ERS child accepts >=1
    position (so signer_A.gtd_exits is non-empty), beats a file Heartbeat, then WEDGES;
    the parent supervisor detects the stale FILE heartbeat, hard-kills the child PID, and
    fires signer_B.cancel_all/flatten on its OWN distinct signer.

Fate isolation: the supervisor is a separate process, holds a DISTINCT signer, and
watches a FILE heartbeat -- it shares nothing with the loop it guards.
"""

from decimal import Decimal

from polybot.ers.caps import RiskCaps
from polybot.ers.heartbeat import Heartbeat
from polybot.ers.service import PaperSigner
from polybot.ers.supervisor import OutOfBandSupervisor


def _caps_dms(seconds):
    # A consistent RiskCaps with the dead-man timeout overridden. dead_man_switch_timeout_seconds
    # is an S4.2 field; overriding it alone keeps every other _verify invariant satisfied.
    return RiskCaps(dead_man_switch_timeout_seconds=seconds)


def test_decide_ok_when_heartbeat_is_fresh(tmp_path):
    caps = _caps_dms(5)
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    hb.beat()                                  # stamped at t=100
    signer_b = PaperSigner()
    sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=lambda: 102.0)
    assert sup.decide(now=102.0) == "OK"       # 2s old, under the 5s dead-man timeout


def test_decide_flatten_and_kill_when_stale_past_timeout(tmp_path):
    caps = _caps_dms(5)
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    hb.beat()
    signer_b = PaperSigner()
    sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=lambda: 106.0)
    assert sup.decide(now=106.0) == "FLATTEN_AND_KILL"   # 6s old, past the 5s timeout


def test_decide_flatten_and_kill_when_never_beaten(tmp_path):
    # No beat ever -> +inf age -> fail closed to FLATTEN_AND_KILL (never assume alive).
    caps = _caps_dms(5)
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)   # file never written
    sup = OutOfBandSupervisor(signer=PaperSigner(), heartbeat=hb, caps=caps, clock=lambda: 100.0)
    assert sup.decide(now=100.0) == "FLATTEN_AND_KILL"


def test_decide_boundary_exactly_at_timeout_is_ok(tmp_path):
    # age == timeout is still alive (is_alive uses <=); one tick past is the kill.
    caps = _caps_dms(5)
    hb = Heartbeat(str(tmp_path / "hb"), clock=lambda: 100.0)
    hb.beat()
    sup = OutOfBandSupervisor(signer=PaperSigner(), heartbeat=hb, caps=caps, clock=lambda: 105.0)
    assert sup.decide(now=105.0) == "OK"


# --- Task 3: on_wedge de-risks on signer_B (NOT signer_A) + SIGKILLs the PID ----------------

import os
import signal
import time
import multiprocessing as mp

from polybot.ers.validator import OpenPosition


def _sleep_forever():
    while True:
        time.sleep(3600)


def test_on_wedge_kills_pid_and_derisks_only_on_signer_b(tmp_path):
    # signer_A is the (untouched) ERS signer; signer_B is the supervisor's OWN signer.
    signer_a, signer_b = PaperSigner(), PaperSigner()
    assert signer_b is not signer_a

    caps = _caps_dms(5)
    hb = Heartbeat(str(tmp_path / "hb"))
    sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=time.monotonic)

    child = mp.Process(target=_sleep_forever)
    child.start()
    try:
        open_positions = (
            OpenPosition("m", "e", "s", "c", Decimal("12"), False,
                         token_id="t1", entry_price=Decimal("0.50"), frozen=False),
        )
        sup.on_wedge(child.pid, open_positions)

        # (a) the child PID was hard-killed
        child.join(timeout=5)
        assert child.exitcode is not None
        assert child.exitcode == -signal.SIGKILL   # killed by SIGKILL, not a clean exit

        # (b) de-risk landed on signer_B's OWN seam ...
        assert signer_b.cancelled_all          # cancel_all() recorded
        assert signer_b.flattened == [("t1",)] # flatten(open) recorded the token
        # ... and signer_A (the wedged ERS signer) was NOT touched
        assert signer_a.cancelled_all == []
        assert signer_a.flattened == []
    finally:
        if child.is_alive():
            os.kill(child.pid, signal.SIGKILL)
            child.join(5)


# --- Task 4: WedgedSigner + the subprocess-backed ACCEPTANCE GATE ----------------------------

from polybot.core.clock import MonotonicStamper
from polybot.ers.controller import ERSController
from polybot.ers.gtd import derive_bracket
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController, RUNNING
from polybot.ers.supervisor import WedgedSigner
from polybot.ingestion.orderbook import LocalBook


# --- the same canonical fixtures the ERS service tests use ---
def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())

SHORT_TIMEOUT = 1   # seconds; the dead-man window for the gate (real but bounded)


def _gtd_for(decision, position, *, caps, standing_exit_total):
    # The canonical opt-in GTD-bracket derivation (DESIGN §3 S4.2): stage a protective standing
    # exit on each ACCEPT so signer_A.gtd_exits is non-empty -- the passive backstop the gate
    # proves SURVIVES the wedge. `expiry` is bound here (a GTD order needs one).
    return derive_bracket(decision, position, caps=caps, expiry=1700,
                          standing_exit_total=standing_exit_total)


def _wedged_ers_child(db_path, hb_path, gtd_path, ready_path):
    """A REAL ERS child: accept >=1 position (staging a GTD bracket on signer_A), beat the
    file heartbeat ONCE, signal ready, then WEDGE forever inside the signer so the loop never
    beats again. Runs in a forked subprocess -- no shared in-memory state with the parent."""
    import json as _json
    # A genuinely-wedged interpreter can swallow SIGTERM/SIGINT (the whole reason the supervisor
    # uses SIGKILL). Model that here: IGNORE both so ONLY SIGKILL can take this child down. This
    # makes the gate itself PROVE SIGKILL-necessity -- a SIGTERM would no longer kill the child.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    stamper = MonotonicStamper()
    store = IntentStore(db_path, stamper)
    store.propose_trade("i1", **_P)

    signer_a = WedgedSigner(wedge_after=1)        # places the 1st order, BLOCKS on the 2nd
    caps = RiskCaps(dead_man_switch_timeout_seconds=SHORT_TIMEOUT)
    controller = SafetyController(caps=caps, store=store, clock=lambda: 0.0)
    controller.set_state(RUNNING, reason="gate_test")   # leave HALTED -> no ACCEPT, no GTD bracket
    # Stamp the heartbeat in the SAME clock domain the parent reads it in (time.monotonic). A wall
    # clock here vs monotonic in the parent would compute a nonsense (negative) age -> never stale.
    hb = Heartbeat(hb_path, clock=time.monotonic)
    ers = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=caps,
                        signer=signer_a, controller=controller, heartbeat=hb,
                        gtd_for=_gtd_for, clock=lambda: 0.0)

    ers.run_cycle()        # heartbeat.beat() + process_pending -> ACCEPT i1 -> signer_a.gtd_exits non-empty

    # Persist the staged GTD brackets so the PARENT (separate process) can assert they survive.
    with open(gtd_path, "w") as fh:
        _json.dump([{"token_id": g["token_id"]} for g in signer_a.gtd_exits], fh)
    open(ready_path, "w").close()   # tell the parent the heartbeat + GTD bracket are on disk

    # Now WEDGE: a second cycle blocks forever inside WedgedSigner.place; the loop never beats again.
    store.propose_trade("i2", **dict(_P, token_id="t1"))
    ers.run_cycle()        # blocks inside signer_a.place (wedge_after=1 already consumed)


def test_supervisor_hard_kills_wedged_child_and_flattens_on_signer_b(tmp_path):
    db_path = str(tmp_path / "i.db")
    hb_path = str(tmp_path / "hb")
    gtd_path = str(tmp_path / "gtd.json")
    ready = str(tmp_path / "ready")

    child = mp.Process(target=_wedged_ers_child, args=(db_path, hb_path, gtd_path, ready))
    child.start()
    try:
        # 1. Wait (bounded poll, NO blind sleep) for the child to have staged the GTD + heartbeat.
        deadline = time.monotonic() + 5
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert os.path.exists(ready), "child never reached ready -> it failed to ACCEPT/stage"

        # 2. The pre-staged GTD bracket exists on signer_A (recorded to disk by the child).
        import json as _json
        with open(gtd_path) as fh:
            staged = _json.load(fh)
        assert staged and staged[0]["token_id"] == "t1", "child did not stage a GTD bracket"

        # 3. Let the FILE heartbeat genuinely go stale past the dead-man timeout (one small real wait).
        hb = Heartbeat(hb_path)
        time.sleep(SHORT_TIMEOUT + 0.2)
        assert not hb.is_alive(now=time.monotonic(), timeout=SHORT_TIMEOUT)

        # 4. The supervisor (its OWN signer_B) decides + acts.
        signer_a_unused, signer_b = PaperSigner(), PaperSigner()
        assert signer_b is not signer_a_unused
        caps = RiskCaps(dead_man_switch_timeout_seconds=SHORT_TIMEOUT)
        sup = OutOfBandSupervisor(signer=signer_b, heartbeat=hb, caps=caps, clock=time.monotonic)
        assert sup.decide(now=time.monotonic()) == "FLATTEN_AND_KILL"

        open_positions = (
            OpenPosition("m1", "e1", "m1", "e1", Decimal("12"), False,
                         token_id="t1", entry_price=Decimal("0.50"), frozen=False),
        )
        sup.on_wedge(child.pid, open_positions)

        # 5. The child REALLY died BY SIGKILL (not a clean SIGTERM exit). The child IGNORES
        #    SIGTERM/SIGINT, so a -signal.SIGKILL exitcode proves the supervisor's hard kill (not a
        #    softer signal) is what took the wedged process down -- the SIGKILL rationale, asserted.
        child.join(timeout=5)
        assert child.exitcode == -signal.SIGKILL

        # 6. De-risk fired on the supervisor's OWN signer_B (cancel working entries + flatten).
        assert signer_b.cancelled_all
        assert signer_b.flattened == [("t1",)]

        # 7. The staged GTD bracket is still on disk -- venue-side standing exits are OUT of the
        #    kill path's reach and persist a wedge (this proves persistence past the wedge, NOT the
        #    cancel-vs-keep semantics: on_wedge's cancel_all fired on the PARENT's separate signer_B
        #    in a different process and physically cannot touch this file). The cancel_all-leaves-
        #    exits invariant is proven separately by test_cancel_all_keeps_the_gtd_exit_brackets.
        with open(gtd_path) as fh:
            survived = _json.load(fh)
        assert survived and survived[0]["token_id"] == "t1"
    finally:
        if child.is_alive():
            os.kill(child.pid, signal.SIGKILL)
            child.join(5)
