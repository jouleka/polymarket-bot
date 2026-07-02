"""ERSController -- the long-lived runloop / cadence driver (S4.1 scaffold / POL-6).

NONE exists today: process_pending is per-call pure, and S3 had no loop owner. This scaffold owns
the SafetyController, wraps process_pending, and exposes the cadence hook (run_cycle) that later
sub-slices extend (L7 evaluate is already wired via the breaker= passthrough; S4.2 adds the
signing canary, S4.5 the reconcile). It starts effectively HALTED: the held SafetyController is
HALTED on construction, so the first cycle never trades until a clean transition (S4.5) flips it
to RUNNING.

Each run_cycle: beat the heartbeat (fate-isolated file; if wired) THEN drive process_pending with
the controller consulted FIRST. The beat-before-process order matters: the out-of-band supervisor
(S4.3) watches the heartbeat, so a cycle that is about to process must first prove liveness.
Clocks are injected for deterministic TDD.
"""

from polybot.ers.anomaly import HALT
from polybot.ers.safety import HALTED, PAUSED, RUNNING
from polybot.ers.service import process_pending


class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 clock):
        self._store = store
        self._book_for = book_for
        self._caps = caps
        self._signer = signer
        self._controller = controller   # the SafetyController (starts HALTED)
        self._breaker = breaker
        self._pipeline = pipeline
        self._heartbeat = heartbeat
        # gtd_for (S4.2 seam): an opt-in callable that pre-stages a protective GTD exit bracket on
        # each ACCEPT (passed straight through to process_pending). gtd_for=None (the default) ==
        # today's behavior -- no GTD staging -- so the S4.1 controller tests stay green.
        self._gtd_for = gtd_for
        # fill_sink (S4.5a seam): an opt-in recording callable (make_fill_sink(store)) passed
        # straight through to process_pending so every ACCEPT appends a durable fill. fill_sink=None
        # (the default) == today's behavior -- no fills recorded -- so the S4.1 tests stay green.
        self._fill_sink = fill_sink
        # anomaly (S4.4a seam): the opt-in L5 AnomalyMonitor consulted each cycle AHEAD of
        # process_pending. anomaly=None (the default) == today's behavior byte-for-byte.
        self._anomaly = anomaly
        # lossbreakers (S4.7d seam): the opt-in realized-loss breakers consulted each cycle
        # AFTER the L5 anomaly block. lossbreakers=None (the default) == today byte-for-byte.
        self._lossbreakers = lossbreakers
        self._clock = clock
        # The working portfolio is threaded across cycles (S4.5 rebuilds it from reconcile on
        # boot; for the scaffold it starts empty at this NAV and folds each cycle's ACCEPTs).
        self._portfolio = self._empty_portfolio()

    def _empty_portfolio(self):
        from polybot.ers.validator import Portfolio
        return Portfolio(nav=self._caps.nav)

    def run_cycle(self):
        """One cadence tick: beat (if wired) -> L5 anomaly consult (if wired) ->
        process_pending(controller=...). Returns the updated portfolio (threaded for the
        next cycle)."""
        if self._heartbeat is not None:
            self._heartbeat.beat()
        if self._anomaly is not None:
            # L5 (S4.4): ALWAYS evaluated when wired (keeps the monitor's per-token
            # prev-state warm every cycle). On HALT: the gate closes FIRST (set_state audits
            # the transition), THEN the one-shot de-risk + its own audit row.
            state = self._anomaly.evaluate(self._portfolio.positions, self._book_for)
            # EDGE-triggered: act only from a LIVE loop (RUNNING/PAUSED) -- never re-fire on
            # an existing HALTED (no audit spam, no cancel_all churn against the standing GTD
            # exits) and never preempt FLATTENING (a stronger de-risk already in flight; it
            # settles HALTED on its own).
            if state.action == HALT and self._controller.state() in (RUNNING, PAUSED):
                self._controller.set_state(HALTED, reason=state.triggers[0])
                try:
                    self._signer.cancel_all()
                    self._store.record_op_event(kind="cancel_all", reason=state.triggers[0],
                                                detail=",".join(state.triggers))
                except Exception as exc:
                    # A raising signer must NOT unwind the halt or kill the cycle -- audit the
                    # failure; the pre-staged GTD exits are the backstop.
                    self._store.record_op_event(kind="cancel_all", reason=state.triggers[0],
                                                detail=f"FAILED: {exc}")
        if self._lossbreakers is not None:
            # S4.7d: realized-loss breakers, consulted every cycle. Frozen positions (row 74)
            # are excluded from the realized counters via the live Portfolio's frozen flags.
            frozen = frozenset(p.token_id for p in self._portfolio.positions if p.frozen)
            self._lossbreakers.evaluate(frozen_tokens=frozen)
        # THE S4.7 re-plumb: read the SWAPPABLE caps from the SafetyController EVERY cycle so
        # a ramp-DOWN swap_caps lands on the very next cycle's validator/GTD derivation.
        # self._caps remains only the construction-time NAV source for the scaffold portfolio.
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio,
            caps=self._controller.active_caps(),
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink)
        return self._portfolio
