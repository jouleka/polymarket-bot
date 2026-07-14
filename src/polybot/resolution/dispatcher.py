"""Durable ordered delivery of immutable resolution terminals."""


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
        acknowledged = 0
        for record in self._store.pending_outbox(limit):
            self._targets[record.role].apply_terminal(record.terminal)
            self._store.acknowledge(
                record.sequence, record.terminal.terminal_id, record.role
            )
            acknowledged += 1
        return acknowledged
