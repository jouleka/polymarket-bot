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
from dataclasses import dataclass
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


def clob_balances(envelopes):
    """Fold Data-API /positions Envelopes into {token_id: Balance}. Keep only
    source == "data-api" with an event_id starting "/positions:"; key by item["asset"]
    (the decimal token_id), shares = Decimal(str(item["size"])). Any missing field or
    parse error on a row -> skip THAT row (fail-closed; a bad row never "agrees")."""
    out: dict[str, Balance] = {}
    for env in envelopes:
        if env.source != "data-api" or not env.event_id.startswith("/positions:"):
            continue
        try:
            item = json.loads(env.content)
            token = item["asset"]
            shares = Decimal(str(item["size"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue  # malformed row: skip, never fold a bad value as agreement
        prev = out.get(token)
        total = shares + (prev.shares if prev else Decimal(0))
        out[token] = Balance(token_id=token, shares=total, latest_fill_at=None)
    return out


def onchain_balances(envelopes, *, wallet):
    """Fold Polygon ERC-1155 transfer Envelopes into {token_id: Balance}, or return
    the DORMANT sentinel None when wallet is None (pure shadow: no chain truth).

    Keep source == "polygon-chain"; on event kind in {transfer_single, transfer_batch}
    credit +value where to == wallet and debit -value where from == wallet (addresses
    compared lowercased), netting per token_id. shares = net / 10**_SHARE_DECIMALS. A
    row that fails to parse is skipped (fail-closed; a bad row never "agrees")."""
    if wallet is None:
        return None
    wallet = wallet.lower()
    net: dict[str, int] = {}
    for env in envelopes:
        if env.source != "polygon-chain":
            continue
        try:
            event = json.loads(env.content)["event"]
            kind = event["kind"]
            if kind == "transfer_single":
                pairs = [(event["token_id"], event["value"])]
            elif kind == "transfer_batch":
                pairs = list(zip(event["token_ids"], event["values"]))
            else:
                continue
            frm = event["from"].lower()
            to = event["to"].lower()
        except (ValueError, KeyError, TypeError, AttributeError):
            continue  # malformed row: skip, never fold a bad value as agreement
        sign = 0
        if to == wallet:
            sign += 1
        if frm == wallet:
            sign -= 1
        if sign == 0:
            continue
        for token, raw in pairs:
            try:
                net[token] = net.get(token, 0) + sign * int(raw)
            except (ValueError, TypeError):
                continue  # non-integer value -> fail-closed: drop this entry
    scale = Decimal(10 ** _SHARE_DECIMALS)
    return {
        token: Balance(token_id=token, shares=Decimal(value) / scale, latest_fill_at=None)
        for token, value in net.items()
    }
