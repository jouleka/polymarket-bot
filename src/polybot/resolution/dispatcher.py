"""Durable ordered delivery of immutable resolution terminals."""

from polybot.resolution.errors import SettlementConflict


class ResolutionDispatcher:
    def __init__(self, store, forecast_ledger, maker_ledger, shadow_ledger):
        self._store = store
        self._targets = {
            "FORECAST": forecast_ledger,
            "MAKER": maker_ledger,
            "SHADOW": shadow_ledger,
        }

    def drain(self, limit):
        """Apply and acknowledge at most ``limit`` pending outbox records."""
        self._store.require_healthy()
        acknowledged = 0
        for record in self._store.pending_outbox(limit):
            try:
                changed = self._targets[record.role].apply_terminal(record.terminal)
            except SettlementConflict as exc:
                self._store.halt(f"target settlement conflict: {exc}")
                raise
            self._after_apply(record, changed)
            self._store.acknowledge(
                record.sequence, record.terminal.terminal_id, record.role
            )
            acknowledged += 1
        return acknowledged

    def _after_apply(self, record, changed):
        """Failure-injection seam after the target transaction commits."""
