"""Local order book for one CLOB asset (token_id).

Reconstructed from the WS market channel: a full ``book`` snapshot replaces state;
incremental ``price_change`` events update individual levels (size 0 removes a
level). Prices/sizes are kept as Decimal for exact tick/midpoint math.
"""


from decimal import Decimal

_BUY_SIDES = {"buy", "bid"}
_SELL_SIDES = {"sell", "ask"}


class LocalBook:
    def __init__(self):
        self._bids = {}  # Decimal price -> Decimal size
        self._asks = {}
        self._stale = True  # no snapshot baseline yet -> not trustworthy

    def apply_book(self, message):
        """Full snapshot: replace state entirely (a reconnect resync resets)."""
        self._bids = self._levels(message.get("bids", []))
        self._asks = self._levels(message.get("asks", []))
        self._stale = False  # fresh, verified baseline

    def mark_stale(self):
        """Flag the book untrustworthy (e.g. on disconnect, until a resync snapshot)."""
        self._stale = True

    def is_stale(self):
        return self._stale

    def apply_price_change(self, message):
        # Staleness flag now guards untrusted books (no baseline / post-disconnect):
        # midpoint() returns None when stale. STILL TODO: mid-stream sequence-gap
        # detection — Polymarket price_change carries a book hash/timestamp, so a
        # single dropped delta between snapshots is not yet detected (mark_stale on
        # hash mismatch once the exact frame hash format is confirmed live).
        for change in message.get("changes", []):
            price = Decimal(change["price"])
            size = Decimal(change["size"])
            book = self._side_book(change["side"])
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def best_bid(self):
        return max(self._bids) if self._bids else None

    def best_ask(self):
        return min(self._asks) if self._asks else None

    def midpoint(self):
        bid, ask = self.best_bid(), self.best_ask()
        if self._stale or bid is None or ask is None or bid >= ask:
            return None  # stale, empty side, or crossed/locked => no usable midpoint
        return (bid + ask) / 2

    @staticmethod
    def _levels(levels):
        parsed = {Decimal(level["price"]): Decimal(level["size"]) for level in levels}
        return {price: size for price, size in parsed.items() if size != 0}

    def _side_book(self, side):
        normalized = side.strip().lower()
        if normalized in _BUY_SIDES:
            return self._bids
        if normalized in _SELL_SIDES:
            return self._asks
        raise ValueError(f"unknown order side: {side!r}")
