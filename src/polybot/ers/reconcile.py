"""S4.5 (POL-6) three-way reconciliation: leg parsers + pure reconciler.

The ERS independently checks its own belief of what it holds (the durable `fills`
ledger) against two external truths -- Polymarket's Data-API `/positions` (advisory)
and the authoritative on-chain ERC-1155 CTF balance -- and HALTS rather than trade on
an unexplained divergence. This module owns the pure leg parsers (S4.5b) that fold
already-fetched rows into per-`token_id` balance maps; the `ThreeWayReconciler` (S4.5c)
and `RestartReconciler` (S4.5d) consume them. Fail-closed throughout: a malformed row
is skipped (never silently "agrees"); money is Decimal; addresses compare lowercased.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal

OK = "OK"
DIVERGED = "DIVERGED"
SETTLING = "SETTLING"
DORMANT = "DORMANT"

# Raw ERC-1155 `value` -> shares = Decimal(value) / Decimal(10**6). Pinned constant;
# empirical verification of the 6-decimal scaling is deferred to POL-4 (a real receipt).
_SHARE_DECIMALS = 6


@dataclass(frozen=True)
class Balance:
    token_id: str
    shares: Decimal
    # Monotonic-ns stamp of the most-recent IN-SESSION fill; None == replayed/pre-restart
    # (a prior monotonic epoch is not comparable to this process's now -> no settle grace).
    latest_fill_at: int | None = None


@dataclass(frozen=True)
class Divergence:
    token_id: str
    internal_shares: Decimal
    onchain_shares: Decimal
    dollars: Decimal


@dataclass(frozen=True)
class ReconResult:
    status: str                    # OK | DIVERGED | SETTLING | DORMANT
    divergences: tuple             # tuple[Divergence, ...]
    onchain_confirmed_exposure: Decimal
    settling_tokens: tuple         # tuple[str, ...]
    triggers: tuple                # tuple[str, ...]


def internal_balances(fills_log, *, in_session=True):
    """Fold the durable fills rows into {token_id: Balance}. shares = sum(+shares if
    side == "BUY" else -shares). latest_fill_at = max(at) among that token's rows when
    in_session, else None (replayed rows get no settle-window grace -> fail-closed)."""
    shares: dict[str, Decimal] = {}
    latest: dict[str, int] = {}
    for row in fills_log:
        token = row["token_id"]
        signed = row["shares"] if row["side"] == "BUY" else -row["shares"]
        shares[token] = shares.get(token, Decimal(0)) + signed
        at = row["at"]
        if token not in latest or at > latest[token]:
            latest[token] = at
    return {
        token: Balance(token_id=token, shares=total,
                       latest_fill_at=(latest[token] if in_session else None))
        for token, total in shares.items()
    }
