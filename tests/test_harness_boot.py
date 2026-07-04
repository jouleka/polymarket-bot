"""S9 / POL-11 — the ERSController(reconciler=…) boot seam (additive; reconciler=None == today)."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


def test_reconciler_none_boot_is_a_noop_and_leaves_controller_halted(tmp_path):
    # reconciler=None (the default) -> boot() is a no-op: it returns None, the held SafetyController
    # stays HALTED (the construction default), and the portfolio is unchanged (empty at NAV). This
    # proves the seam is byte-for-byte inert when unwired -- the whole existing test suite relies on
    # it. NOT passing reconciler at all must construct exactly as today.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    try:
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=PaperSigner(), controller=ctl, clock=lambda: 0)  # no reconciler
        assert ctl.state() == _safety.HALTED           # boot default, untouched
        result = rc.boot()
        assert result is None                          # no-op returns None
        assert ctl.state() == _safety.HALTED           # STILL halted -- boot did nothing
        # The threaded portfolio is the empty construction portfolio (NAV only, no positions).
        final = rc.run_cycle()
        assert isinstance(final, Portfolio)
        assert final.positions == ()                   # nothing adopted
    finally:
        store.close()
