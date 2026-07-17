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

    def apply_price_change(self, changes):
        """Apply a list of per-asset level changes from a live ``price_change`` frame.

        Each change is a dict with ``price`` / ``size`` / ``side`` (a size of 0
        removes the level). The live venue format carries these as the entries of
        the frame's ``price_changes`` list, one or more per asset; the dispatcher
        groups them by asset and hands this book its own entries. Extra keys
        (``best_bid`` / ``best_ask`` / ``hash``) are not booked here — top-of-book
        consistency is checked separately by ``verify_top_of_book``.
        """
        for change in changes:
            price = Decimal(change["price"])
            size = Decimal(change["size"])
            book = self._side_book(change["side"])
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def verify_top_of_book(self, best_bid, best_ask):
        """Mid-stream sequence-gap detection.

        Each live ``price_change`` entry carries the venue's *authoritative*
        resulting top-of-book (``best_bid`` / ``best_ask``). After applying the
        deltas, our reconstructed best bid/ask MUST equal them; if it doesn't we
        dropped or misapplied a delta and the book has silently diverged. The
        venue ``hash`` would catch deep-book divergence too, but its exact
        serialization is not recomputable from the public stream (empirically
        confirmed), whereas this top-of-book check is recompute-free and validates
        exactly the state the ERS sizes off (midpoint). On divergence: mark the
        book stale (``midpoint()`` then returns None) so the gap forces a resync.

        ``best_bid`` / ``best_ask`` are venue price strings; ``""`` or ``None``
        denotes an empty side. Returns True when in sync, False when diverged.
        """
        if (self._matches(self.best_bid(), best_bid, empty_sentinels=(Decimal(0),))
                and self._matches(
                    self.best_ask(), best_ask,
                    empty_sentinels=(Decimal(0), Decimal(1)),
                )):
            return True
        self.mark_stale()
        return False

    @staticmethod
    def _matches(reconstructed, venue_price, *, empty_sentinels):
        # venue_price is a required entry field, HALT-guarded to a string upstream.
        # "" / None denote an empty side. Prices live strictly inside (0, 1), so
        # boundary values can be venue empty-side sentinels: bid uses 0; live
        # evidence also shows ask=1 when the snapshot has no asks. Ask retains the
        # historical 0 sentinel for compatibility. Parse with Decimal() exactly like
        # apply_price_change so apply and verify never disagree on numeric meaning.
        venue = None
        if venue_price not in (None, ""):
            parsed = Decimal(venue_price)
            venue = None if parsed in empty_sentinels else parsed
        return reconstructed == venue  # Decimal value-equality; both-None == empty side

    def best_bid(self):
        return max(self._bids) if self._bids else None

    def best_ask(self):
        return min(self._asks) if self._asks else None

    def midpoint(self):
        bid, ask = self.best_bid(), self.best_ask()
        if self._stale or bid is None or ask is None or bid >= ask:
            return None  # stale, empty side, or crossed/locked => no usable midpoint
        return (bid + ask) / 2

    def top_of_book(self):
        """Best bid/ask prices and their resting sizes (None on an empty side) --
        the depth snapshot the synthetic liquidity-evaporation detector compares
        before vs after a delta. Unlike midpoint() this is not stale-gated; it is a
        structural observation, not a price the ERS sizes off."""
        bid, ask = self.best_bid(), self.best_ask()
        return (bid, self._bids[bid] if bid is not None else None,
                ask, self._asks[ask] if ask is not None else None)

    def size_at(self, side, price):
        """Resting size at a price level on a side (Decimal 0 if the level is absent)."""
        return self._side_book(side).get(Decimal(price), Decimal(0))

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
