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
from polybot.ers.lossbreaker import HALT as LOSS_HALT, PAUSE as LOSS_PAUSE
from polybot.ers.ramp import step_daily, step_weekly
from polybot.ers.safety import HALTED, PAUSED, REASON_RAMP_DOWN, RUNNING
from polybot.ers.service import process_pending


class ERSController:
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 telegram=None, reconciler=None, shadow_planner=None, clock):
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
        # telegram (S4.6d seam): the opt-in L8 TelegramController drained at the TOP of
        # run_cycle (ahead of even beat/anomaly) so an operator KILL dominates the cycle.
        # telegram=None (the default) == pre-S4.6 byte-for-byte.
        self._telegram = telegram
        # reconciler (S9d / POL-11 seam): the opt-in RestartReconciler adopted ONCE at boot() —
        # NOT per-cycle. reconciler=None (the default) == today byte-for-byte: boot() is a no-op,
        # the controller stays HALTED with the empty construction portfolio, and run_cycle is
        # untouched. The DORMANT wallet=None shadow path flips HALTED->RUNNING on boot() (D6).
        self._reconciler = reconciler
        # POL-16 opt-in ACCEPT adapter. None preserves the pre-POL-16 loop exactly;
        # a wired planner returns a canonical filled paper execution or None.
        self._shadow_planner = shadow_planner
        self._clock = clock
        # The working portfolio is threaded across cycles (S4.5 rebuilds it from reconcile on
        # boot; for the scaffold it starts empty at this NAV and folds each cycle's ACCEPTs).
        self._portfolio = self._empty_portfolio()

    def boot(self):
        """Adopt the RestartReconciler ONCE before the run loop (deploy calls this once). When
        wired, reconcile_on_boot() drives the (only automatic) HALTED->RUNNING transition and
        returns the rebuilt Portfolio, which becomes the threaded working portfolio. reconciler=
        None -> no-op (returns None; stays HALTED, empty portfolio == today). run_cycle is not
        touched by this seam."""
        if self._reconciler is not None:
            self._portfolio = self._reconciler.reconcile_on_boot()
            return self._portfolio
        return None

    def _empty_portfolio(self):
        from polybot.ers.validator import Portfolio
        return Portfolio(nav=self._caps.nav)

    def run_cycle(self):
        """One cadence tick: beat (if wired) -> L5 anomaly consult (if wired) ->
        process_pending(controller=...). Returns the updated portfolio (threaded for the
        next cycle)."""
        # S4.6d: drain authenticated Telegram commands FIRST -- ahead of even the heartbeat
        # beat / L5 anomaly / loss consults -- so an operator KILL dominates THIS cycle (the
        # HALTED verdict then blocks every pending intent). telegram=None == today (no drain).
        if self._telegram is not None:
            self._telegram.drain()
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
            ls = self._lossbreakers.evaluate(frozen_tokens=frozen)
            for step in ls.ramp_steps:
                # Idempotent tighten-only ratchet (DESIGN §6.7): applied in ANY op-state --
                # re-application is a hash-identical no-op inside swap_caps (no audit spam),
                # and tightening while halted is harmless and desirable.
                step_fn = step_weekly if step == "weekly" else step_daily
                self._controller.swap_caps(step_fn(self._controller.active_caps()),
                                           reason=REASON_RAMP_DOWN)
            if ls.action == LOSS_HALT and self._controller.state() in (RUNNING, PAUSED):
                # EDGE-triggered halt-first one-shot (the S4.4 pattern verbatim): close the
                # gate, THEN one best-effort cancel_all; a raising signer never unwinds the
                # halt or kills the cycle -- the pre-staged GTD exits are the backstop.
                self._controller.set_state(HALTED, reason=ls.triggers[0])
                try:
                    self._signer.cancel_all()
                    self._store.record_op_event(kind="cancel_all", reason=ls.triggers[0],
                                                detail=",".join(ls.triggers))
                except Exception as exc:
                    self._store.record_op_event(kind="cancel_all", reason=ls.triggers[0],
                                                detail=f"FAILED: {exc}")
            elif ls.action == LOSS_PAUSE and self._controller.state() == RUNNING:
                # Sticky pause (Fork 4): the streak counter resets on a win; the PAUSED
                # op-state does NOT -- recovery is operator RESUME. Fires from the live
                # trading state only (never downgrades a halt; never re-audits a pause).
                self._controller.set_state(PAUSED, reason=ls.triggers[0])
        # THE S4.7 re-plumb: read the SWAPPABLE caps from the SafetyController EVERY cycle so
        # a ramp-DOWN swap_caps lands on the very next cycle's validator/GTD derivation.
        # self._caps remains only the construction-time NAV source for the scaffold portfolio.
        self._portfolio = process_pending(
            self._store, book_for=self._book_for, portfolio=self._portfolio,
            caps=self._controller.active_caps(),
            signer=self._signer, breaker=self._breaker, pipeline=self._pipeline,
            controller=self._controller, gtd_for=self._gtd_for, fill_sink=self._fill_sink,
            shadow_planner=self._shadow_planner)
        return self._portfolio
