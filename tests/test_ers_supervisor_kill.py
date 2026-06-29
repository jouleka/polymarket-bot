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
