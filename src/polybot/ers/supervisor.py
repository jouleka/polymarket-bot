"""Out-of-band L6 supervisor + the wedged-loop test doubles (S4.3 / POL-6).

Fork 1/2 (DESIGN-S4 §0): the supervisor is a SEPARATE OS process holding its OWN signer
(signer_B, distinct from the ERS's signer_A) and watching a FILE Heartbeat. It must
survive a wedged trading-loop interpreter, so it shares nothing with the loop it guards.

``decide(now)`` is the PURE dead-man decision (clock-injected, deterministic): a heartbeat
stale past ``caps.dead_man_switch_timeout_seconds`` -> "FLATTEN_AND_KILL", else "OK". It
fails CLOSED -- a never-written / unreadable heartbeat reads as +inf age -> kill.

``on_wedge`` is the action half: hard-kill the wedged ERS PID (SIGKILL -- a wedged
interpreter can swallow SIGTERM), THEN de-risk on signer_B (cancel WORKING ENTRY orders +
flatten the open set). The pre-staged GTD EXIT brackets on signer_A are the PASSIVE
backstop and are intentionally NOT cancelled here. Live cancelAll/credential separation is
POL-4-deferred; this is the shadow PaperSigner proof.
"""

import os
import signal
import time

OK = "OK"
FLATTEN_AND_KILL = "FLATTEN_AND_KILL"


class OutOfBandSupervisor:
    def __init__(self, *, signer, heartbeat, caps, clock=None):
        # `signer` is signer_B -- a DISTINCT instance from the ERS's signer_A (fate isolation).
        self._signer = signer
        self._heartbeat = heartbeat
        self._caps = caps
        self._clock = clock or time.monotonic

    def decide(self, now):
        timeout = self._caps.dead_man_switch_timeout_seconds
        if self._heartbeat.is_alive(now, timeout=timeout):
            return OK
        return FLATTEN_AND_KILL   # stale / never-beaten -> fail closed

    def on_wedge(self, ers_pid, open_positions):
        """Hard-kill the wedged ERS, then de-risk on the supervisor's OWN signer.

        Order is load-bearing: kill FIRST (stop the wedged loop from doing anything more),
        THEN cancel working entries + flatten on signer_B. The GTD exit brackets staged on
        signer_A are left standing (the passive backstop)."""
        self._hard_kill(ers_pid)
        self._signer.cancel_all()
        self._signer.flatten(open_positions)

    @staticmethod
    def _hard_kill(pid):
        # SIGKILL: a genuinely-wedged interpreter can ignore SIGTERM; the whole point is fate
        # isolation, so do not negotiate. ProcessLookupError == already dead == success.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
