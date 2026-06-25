"""CLOB market-channel dispatcher.

Routes WS ``book`` / ``price_change`` frames to a per-asset LocalBook, stamps a
monotonic observed_at at dispatch (after decode — close enough to receive for
ordering at S1), and emits an Observation to an optional sink. The async socket
loop (connect / pong / reconnect+resync) wraps this; this part is pure and
synchronous so it can be tested without any network.

Note: each event gets its own stamp, so a batched array frame yields several
distinct observed_at values despite arriving together — intentional for total
ordering, but revisit if batch-atomic timestamps are ever needed.
"""


from dataclasses import dataclass

from polybot.ingestion.orderbook import LocalBook

_HANDLERS = {
    "book": "apply_book",
    "price_change": "apply_price_change",
}

# Documented market-channel events we recognize but don't book yet: skip, don't HALT.
_BENIGN_IGNORED = {"last_trade_price", "tick_size_change"}


@dataclass(frozen=True)
class Observation:
    asset_id: str
    event_type: str
    observed_at: int
    message: dict


class MarketStream:
    def __init__(self, stamper, sink=None):
        self._stamper = stamper
        self._sink = sink
        self._books = {}  # asset_id -> LocalBook

    def book_for(self, asset_id):
        return self._books.get(asset_id)

    def ingest(self, message):
        event_type = message["event_type"]
        if event_type in _BENIGN_IGNORED:
            return None
        if event_type not in _HANDLERS:
            raise ValueError(f"unknown WS event_type: {event_type!r}")

        observed_at = self._stamper.stamp()  # stamp at dispatch, before book mutation
        asset_id = message["asset_id"]
        book = self._books.setdefault(asset_id, LocalBook())
        getattr(book, _HANDLERS[event_type])(message)

        if self._sink is not None:
            self._sink(Observation(asset_id, event_type, observed_at, message))
        return observed_at
