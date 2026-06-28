"""Propose-only facade (S6 / POL-8) — the load-bearing safety boundary.

Hermes (a frozen-model harness with NO keys, undeployed in S6) is handed ONLY
an instance of this facade. The entire safety model is structural and lives in
CODE, not in a prompt:

  * The facade COMPOSES an ``IntentStore`` (it does NOT subclass it), so it
    inherits no ``record_decision`` / ``pending`` method.
  * The store reference is held in a NAME-MANGLED private attribute
    (``self.__store`` -> ``_ProposeOnlyFacade__store``); there is no public
    ``store`` attribute to reach through.
  * The only write surface is ``propose_trade(...)``, which delegates the
    store's INSERT-only call. It has no ``status`` parameter, so a
    confused-deputy Hermes can at worst enqueue a ``PROPOSED`` row.
  * Read surface: ``get`` / ``audit_log`` (own proposals + the immutable audit)
    plus the 4 Hermes read tools (``get_market`` / ``get_book`` / ``get_ledger``
    / ``get_flags``), each delegating to an INJECTED read-only callable.

The deterministic ERS (NOT Hermes) is what polls ``pending()``, runs the
validator, and calls ``record_decision`` -- none of which the facade exposes.
A ``dir()``/attribute sweep test (``test_ers_facade.py``) makes the
"no place/flatten/record_decision/pending, no public store, no signer path"
guarantee load-bearing so careless future wiring cannot regress it.
"""


class ProposeOnlyFacade:
    def __init__(self, store, *, market_reader=None, book_reader=None,
                 ledger_reader=None, flags_reader=None):
        # Name-mangled private: no public attribute exposes the IntentStore, so
        # Hermes cannot reach record_decision / pending / the audit-mutation path.
        self.__store = store
        self.__market_reader = market_reader
        self.__book_reader = book_reader
        self.__ledger_reader = ledger_reader
        self.__flags_reader = flags_reader

    # --- the ONE write: INSERT-only, no `status` param (the chokepoint) ---
    def propose_trade(self, intent_id, *, token_id, condition_id, event_id, side,
                      target_price, max_price, size_usd_suggestion, p, p_confidence,
                      resolution_summary="", thesis="", citations=()):
        return self.__store.propose_trade(
            intent_id, token_id=token_id, condition_id=condition_id,
            event_id=event_id, side=side, target_price=target_price,
            max_price=max_price, size_usd_suggestion=size_usd_suggestion,
            p=p, p_confidence=p_confidence, resolution_summary=resolution_summary,
            thesis=thesis, citations=citations,
        )

    # --- reads of the facade's own proposal store (no mutation surface) ---
    def get(self, intent_id):
        return self.__store.get(intent_id)

    def audit_log(self):
        return self.__store.audit_log()

    # --- the 4 Hermes read tools: delegate to injected read-only callables ---
    def get_market(self, *args, **kwargs):
        return self.__market_reader(*args, **kwargs)

    def get_book(self, *args, **kwargs):
        return self.__book_reader(*args, **kwargs)

    def get_ledger(self, *args, **kwargs):
        return self.__ledger_reader(*args, **kwargs)

    def get_flags(self, *args, **kwargs):
        return self.__flags_reader(*args, **kwargs)
