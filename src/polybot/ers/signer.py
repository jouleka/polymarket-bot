"""The Signer Protocol (S4 / POL-6).

Formalizes the signer SEAM so the ERS's signer (signer_A) and the out-of-band supervisor's
signer (signer_B) are structurally-distinct injected dependencies behind ONE contract. The
type system documents that the supervisor's signer is NOT the wedged ERS's. ``PaperSigner``
(ers/service.py) is the shadow implementation; the real Rust signer (POL-4) is a future
implementation behind the same Protocol. ``@runtime_checkable`` so structural isinstance()
checks work in tests + wiring.

The de-risk surface (DESIGN §4):
  place             -- the entry order (existing)
  flatten           -- exit the named open positions (existing)
  cancel_all        -- cancel WORKING/unfilled ENTRY orders; KEEP the GTD exit brackets
  place_gtd_bracket -- stage a protective standing exit at entry (the passive backstop)
  run_canary        -- sign+place+cancel a min-size order to prove signing health; NEVER blind-retry
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Signer(Protocol):
    def place(self, intent, decision) -> None: ...
    def flatten(self, positions) -> None: ...
    def cancel_all(self) -> None: ...
    def place_gtd_bracket(self, position, *, exit_price, expiry) -> None: ...
    def run_canary(self) -> bool: ...
