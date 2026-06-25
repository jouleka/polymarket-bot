"""Gamma market normalizer.

Polymarket's Gamma API returns ``clobTokenIds`` / ``outcomes`` / ``outcomePrices``
as JSON-encoded strings (not native arrays), and the convention is index 0 = Yes,
index 1 = No. This module parses and validates that into a canonical ``Market``.
"""

import json
from decimal import Decimal

from polybot.core.models import Market, Outcome


def _parse_encoded_list(value):
    """Gamma encodes list fields as JSON strings; accept an already-parsed list too."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _require_strings(values, field, condition_id):
    """Gamma encodes these fields as JSON strings; a non-string element means the
    upstream format changed (or a price arrived as a lossy float) -> fail loud."""
    for value in values:
        if not isinstance(value, str):
            raise ValueError(
                f"Gamma market {condition_id} {field} is not a string "
                f"({value!r}, type {type(value).__name__}) - HALT on format change"
            )


def _assert_yes_no_order(names, condition_id):
    """For any market touching Yes/No, enforce the canonical [0]=Yes, [1]=No order.

    If "yes" or "no" appears among the labels, the market must be exactly
    ["Yes", "No"] (case/whitespace-insensitive); anything else (reversed, a Yes
    paired with a non-No, an extra outcome) fails loud rather than letting the
    index-0=Yes assumption go unverified. Genuinely non-Yes/No markets (e.g.
    NegRisk candidate lists) carry no yes/no label and are left untouched.
    """
    labels = [n.strip().lower() for n in names]
    if ("yes" in labels or "no" in labels) and labels != ["yes", "no"]:
        raise ValueError(
            f"Yes/No market {condition_id} outcomes not in canonical [Yes, No] order: {names}"
        )


def normalize_market(raw):
    token_ids = _parse_encoded_list(raw["clobTokenIds"])
    names = _parse_encoded_list(raw["outcomes"])
    prices = _parse_encoded_list(raw["outcomePrices"])

    if not len(token_ids) == len(names) == len(prices):
        raise ValueError(
            f"Gamma market {raw.get('conditionId')} has mismatched array lengths: "
            f"clobTokenIds={len(token_ids)}, outcomes={len(names)}, "
            f"outcomePrices={len(prices)}"
        )

    _require_strings(token_ids, "token_id", raw.get("conditionId"))
    _require_strings(prices, "price", raw.get("conditionId"))
    _assert_yes_no_order(names, raw.get("conditionId"))

    outcomes = tuple(
        Outcome(name=name, token_id=str(token_id), price=Decimal(str(price)))
        for name, token_id, price in zip(names, token_ids, prices)
    )

    return Market(
        condition_id=raw["conditionId"],
        question=raw.get("question", ""),
        slug=raw.get("slug", ""),
        outcomes=outcomes,
        active=bool(raw.get("active", False)),
        closed=bool(raw.get("closed", False)),
    )
