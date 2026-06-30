"""RestartReconciler — the boot state machine (S4.5d / POL-6).

At process boot the SafetyController is HALTED(unclean_restart). reconcile_on_boot() is the ONLY
automatic HALTED->RUNNING path: it folds the durable internal fills ledger (replayed, NO settle
grace), the CLOB /positions leg, and the authoritative on-chain leg, three-way-reconciles, rebuilds
the Portfolio, and transitions RUNNING *only* on a clean (OK/DORMANT) result. Anything else stays
HALTED(unclean_restart) -> a human reconciles. Crash = HOLD. wallet=None => DORMANT (pure shadow,
no chain truth) => treated as clean => RUNNING, portfolio rebuilt from the internal ACCEPTED set.

The live on-chain-confirmed ∩ ACCEPTED rebuild is DEFERRED to POL-4 (a funded clean-box wallet);
the per-cycle running-cadence reconcile is DEFERRED to S4.4. This slice produces the pure result
and drives the boot transition only.
"""

from polybot.ers.reconcile import (
    DORMANT,
    OK,
    clob_balances,
    internal_balances,
    onchain_balances,
)
from polybot.ers.safety import (
    HALTED,
    REASON_RESTART_RECONCILED,
    REASON_UNCLEAN_RESTART,
    RUNNING,
)
from polybot.ers.validator import OpenPosition, Portfolio


class RestartReconciler:
    def __init__(self, *, store, event_store, reconciler, controller, caps, clock, wallet=None):
        # clock: a 0-arg callable returning a monotonic-ns now (time.monotonic_ns in prod; a fixed
        # int in tests) -- SAME domain as MonotonicStamper.stamp(), so the reconciler's settle-window
        # arithmetic is unit-consistent.
        self._store = store
        self._event_store = event_store
        self._reconciler = reconciler
        self._controller = controller
        self._caps = caps
        self._clock = clock
        self._wallet = wallet

    def reconcile_on_boot(self):
        # Replayed rows: in_session=False => latest_fill_at=None => NO settle-window grace (a prior
        # monotonic epoch is NOT comparable to this process's now; an unconfirmed pre-restart fill is
        # fail-closed DIVERGED, never SETTLING).
        internal = internal_balances(self._store.fills_log(), in_session=False)
        envs = self._event_store.all()
        clob = clob_balances(envs)
        onchain = onchain_balances(envs, wallet=self._wallet)
        result = self._reconciler.reconcile(
            internal, clob, onchain, wallet=self._wallet, now=self._clock())
        portfolio = self._rebuild_portfolio()
        if result.status in (OK, DORMANT):
            # The ONLY automatic HALTED->RUNNING transition.
            self._controller.set_state(RUNNING, reason=REASON_RESTART_RECONCILED)
        else:
            # DIVERGED / SETTLING / orphan -> stay HALTED; a human must reconcile. Never auto-resume.
            self._controller.set_state(HALTED, reason=REASON_UNCLEAN_RESTART)
        return portfolio

    def _rebuild_portfolio(self):
        # DORMANT/shadow path: rebuild from the internal ACCEPTED rows (there is no chain to confirm
        # against in shadow). The live on-chain-confirmed ∩ ACCEPTED rebuild is DEFERRED to POL-4.
        positions = tuple(
            OpenPosition(
                condition_id=r.condition_id, event_id=r.event_id,
                resolution_source=r.condition_id, cluster_id=r.event_id,
                worst_case_risk=r.decision_stake_usd, token_id=r.token_id,
                entry_price=r.decision_price_exec, matrix_cold=True, frozen=False)
            for r in self._store.accepted()
        )
        return Portfolio(nav=self._caps.nav, positions=positions)
