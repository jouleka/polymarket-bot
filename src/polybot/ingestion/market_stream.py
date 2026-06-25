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
from decimal import Decimal

from polybot.ingestion.orderbook import LocalBook

# Documented market-channel events we recognize but don't book yet: skip, don't HALT.
_BENIGN_IGNORED = {"last_trade_price", "tick_size_change"}

# Fields every live price_change entry must carry to be bookable+routable; their
# absence is a format change, not a transient malformation -> HALT (fail loud).
# best_bid/best_ask are included because they are the SOLE gap detector: if the
# venue ever drops/renames them, that must HALT loudly, not silently degrade the
# detector into "every frame is a gap" (perpetual stale + reconnect storm).
_PRICE_CHANGE_REQUIRED = ("asset_id", "price", "side", "size", "best_bid", "best_ask")

# Decimal-valued fields the venue sends as strings. A non-string (e.g. a lossy JSON
# float) is a format change AND would desync apply() (Decimal(x)) from verify() --
# the Gamma normalizer takes the same reject-non-string stance -> HALT.
_PRICE_CHANGE_NUMERIC = ("price", "size", "best_bid", "best_ask")


@dataclass(frozen=True)
class Observation:
    asset_id: str
    event_type: str
    observed_at: int
    message: dict


class MarketStream:
    def __init__(self, stamper, sink=None, asset_ids=None, detector=None, synthetic_sink=None):
        self._stamper = stamper
        self._sink = sink
        # Optional synthetic-event detector (liquidity-evaporation / large-print from
        # book deltas) + its OWN sink. A separate sink (a distinct source) keeps these
        # derived events out of the clob-ws replay stream, so reconstruction stays
        # exact and ingest stays strictly fail-loud.
        self._detector = detector
        self._synthetic_sink = synthetic_sink
        self._books = {}  # asset_id -> LocalBook
        # The shard's subscription set, if known. Lets ingest distinguish a tracked
        # asset whose snapshot is merely in-flight (archive its pre-snapshot deltas --
        # the store cannot be backfilled) from a truly-untracked sibling leg the frame
        # also carries (skip, to avoid a phantom book and cross-shard duplicate rows).
        self._tracked = set(asset_ids) if asset_ids is not None else None
        self._resync_requested = False
        self._clean_progress = False

    def book_for(self, asset_id):
        return self._books.get(asset_id)

    def mark_all_stale(self):
        """Flag every book untrustworthy (e.g. on a socket disconnect)."""
        for book in self._books.values():
            book.mark_stale()

    def consume_resync_request(self):
        """Whether a mid-stream sequence gap asked for a resync, cleared on read.

        A divergence detected by ``LocalBook.verify_top_of_book`` marks the book
        stale and sets this flag; the socket reads it after each frame and
        re-subscribes (== resync) to pull a fresh snapshot. Read-and-clear so any
        number of gaps seen since the last check collapse to ONE resubscribe.
        """
        if self._resync_requested:
            self._resync_requested = False
            self._clean_progress = False  # a gap supersedes clean progress this dispatch
            return True
        return False

    def consume_clean_progress(self):
        """Whether a price_change applied CLEANLY (no gap) since the last check,
        cleared on read. The socket uses this to reset its consecutive-resync storm
        counter: only a reconciling delta -- not a book snapshot -- proves the
        reconstruction is tracking the venue, so isolated gaps that recover never
        accrue toward the fail-loud resync HALT.
        """
        if self._clean_progress:
            self._clean_progress = False
            return True
        return False

    def ingest(self, message):
        event_type = message["event_type"]
        if event_type in _BENIGN_IGNORED:
            return None
        if event_type == "price_change":
            return self._ingest_price_change(message)
        if event_type != "book":
            raise ValueError(f"unknown WS event_type: {event_type!r}")

        observed_at = self._stamper.stamp()  # stamp at dispatch, before book mutation
        asset_id = message["asset_id"]
        book = self._books.setdefault(asset_id, LocalBook())
        book.apply_book(message)
        if self._sink is not None:
            self._sink(Observation(asset_id, event_type, observed_at, message))
        return observed_at

    def _ingest_price_change(self, message):
        """Live price_change frames carry NO top-level asset_id: they hold a
        ``price_changes`` list whose entries each name their own asset_id (one
        frame fans out across a market's legs). Group by asset, apply each tracked
        asset's level changes, cross-check the resulting top-of-book against the
        venue's best_bid/best_ask (mid-stream gap detection), and emit one
        Observation per tracked asset (each a distinct point-in-time store row).
        """
        changes = message.get("price_changes")
        if not isinstance(changes, list):
            raise ValueError(
                f"price_change frame missing a 'price_changes' list "
                f"(keys={sorted(message)}) - HALT on format change"
            )
        grouped, order = self._group_by_asset(changes)

        stamps = []
        for asset_id in order:
            entries = grouped[asset_id]
            book = self._books.get(asset_id)
            if book is None:
                # No snapshot baseline yet. If this is a SUBSCRIBED asset (its snapshot
                # is merely in-flight), archive the raw delta -- the store cannot be
                # backfilled -- but do NOT apply it (a delta with no baseline is not a
                # trustworthy book; the imminent snapshot supersedes it). A truly
                # untracked sibling leg is skipped: no phantom book, and no duplicate
                # row racing the shard that actually subscribed to it.
                if self._tracked is not None and asset_id in self._tracked and self._sink is not None:
                    observed_at = self._stamper.stamp()
                    self._sink(Observation(
                        asset_id, "price_change", observed_at,
                        self._per_asset_message(message, asset_id, entries),
                    ))
                    stamps.append(observed_at)
                continue
            observed_at = self._stamper.stamp()  # stamp before mutation, per tracked asset
            if self._detector is not None:
                before_top = book.top_of_book()
                level_changes = [
                    (c["side"], Decimal(c["price"]),
                     book.size_at(c["side"], c["price"]), Decimal(c["size"]))
                    for c in entries
                ]
            book.apply_price_change(entries)
            # Gap-check against the LAST entry's top-of-book (the resulting state after
            # applying every level change for this asset in arrival order). NOTE: we
            # require best_bid/best_ask on EVERY entry (fail-loud); if the venue is ever
            # observed to omit them on intermediate multi-entry rows, relax that to the
            # last entry only -- no real multi-entry-same-asset frame has been captured yet.
            last = entries[-1]
            if not book.verify_top_of_book(last["best_bid"], last["best_ask"]):  # required (HALT-guarded)
                self._resync_requested = True  # diverged -> force a resync snapshot
            else:
                self._clean_progress = True  # reconciling delta -> resets the resync-storm counter
            if self._sink is not None:
                self._sink(Observation(
                    asset_id, "price_change", observed_at,
                    self._per_asset_message(message, asset_id, entries),
                ))
            if self._detector is not None:
                self._emit_synthetic(self._detector.detect(
                    asset_id, level_changes, before_top, book.top_of_book(), observed_at))
            stamps.append(observed_at)
        return stamps

    def _emit_synthetic(self, events):
        # Each synthetic event is its own point-in-time observation -> its own
        # canonical (globally-unique, monotonic) stamp, distinct from the triggering
        # price_change. Routed to the dedicated synthetic_sink (a separate source).
        if self._synthetic_sink is None:
            return
        for event in events:
            stamp = self._stamper.stamp()
            event["observed_at"] = stamp
            self._synthetic_sink(Observation(event["asset_id"], event["event_type"], stamp, event))

    @staticmethod
    def _group_by_asset(changes):
        grouped, order = {}, []
        for entry in changes:
            if not isinstance(entry, dict) or any(k not in entry for k in _PRICE_CHANGE_REQUIRED):
                raise ValueError(
                    f"price_change entry missing one of {_PRICE_CHANGE_REQUIRED} "
                    f"({entry!r}) - HALT on format change"
                )
            for field in _PRICE_CHANGE_NUMERIC:
                if not isinstance(entry[field], str):
                    raise ValueError(
                        f"price_change {field} is not a string "
                        f"({entry[field]!r}, type {type(entry[field]).__name__}) "
                        f"- HALT on format change (lossy numeric)"
                    )
            asset_id = entry["asset_id"]
            if asset_id not in grouped:
                grouped[asset_id] = []
                order.append(asset_id)
            grouped[asset_id].append(entry)
        return grouped, order

    @staticmethod
    def _per_asset_message(frame, asset_id, entries):
        # A per-asset, lossless slice for the no-backfill store: this asset's level
        # changes plus the frame's market + timestamp (the latter -> published_at).
        return {
            "event_type": "price_change",
            "asset_id": asset_id,
            "market": frame.get("market"),
            "timestamp": frame.get("timestamp"),
            "price_changes": entries,
        }
