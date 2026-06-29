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


class WedgedSigner:
    """Test double that BLOCKS to wedge a real ERS child (S4.3 acceptance gate).

    Implements the Signer seam. The first ``wedge_after`` place() calls behave like a
    PaperSigner (record + stage a GTD bracket so signer_A.gtd_exits is non-empty); the NEXT
    place() BLOCKS forever (``time.sleep`` loop), so the ERS loop never returns to beat the
    heartbeat again -> the file heartbeat goes stale -> the out-of-band supervisor must
    hard-kill the wedged process. flatten()/cancel_all() exist for protocol completeness.
    """

    def __init__(self, wedge_after=1):
        self._wedge_after = wedge_after
        self._placed = 0
        self.placed = []
        self.flattened = []
        self.cancelled_all = []
        self.gtd_exits = []

    def place(self, intent, decision):
        if self._placed >= self._wedge_after:
            while True:               # genuinely wedged: never returns
                time.sleep(3600)
        self._placed += 1
        self.placed.append({"intent_id": intent.intent_id, "token_id": intent.token_id,
                            "stake_usd": decision.stake_usd, "price_exec": decision.price_exec})

    def place_gtd_bracket(self, position, *, exit_price, expiry):
        self.gtd_exits.append({"token_id": position.token_id, "exit_price": exit_price,
                               "expiry": expiry, "size": position.worst_case_risk})

    def flatten(self, positions):
        self.flattened.append(tuple(p.token_id for p in positions))

    def cancel_all(self):
        self.cancelled_all.append(len(self.placed))

    def run_canary(self):
        return True
