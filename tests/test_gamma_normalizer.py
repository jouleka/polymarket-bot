"""Tests for the Gamma market normalizer (POL-3 / S1).

Gamma returns clobTokenIds / outcomes / outcomePrices as JSON-encoded strings,
with index 0 = Yes, index 1 = No. token_id is a huge decimal ERC-1155 id that
must be preserved exactly as a string (int conversion loses precision).
"""

from decimal import Decimal

import pytest

from polybot.ingestion.gamma import normalize_market


def _binary_market(**overrides):
    raw = {
        "conditionId": "0xabc123",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        # 77-digit ERC-1155 token ids, JSON-encoded as a string by Gamma:
        "clobTokenIds": (
            '["71321045679252212594626385532706912750332728571942532289631379312455583992563",'
            ' "52114319501245915516055106046884209969926127482827954674443846427813813222426"]'
        ),
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.62", "0.38"]',
        "active": True,
        "closed": False,
    }
    raw.update(overrides)
    return raw


def test_normalizes_binary_market_with_yes_first():
    market = normalize_market(_binary_market())

    assert market.condition_id == "0xabc123"
    assert market.slug == "will-x-happen"
    assert [o.name for o in market.outcomes] == ["Yes", "No"]

    yes = market.outcomes[0]
    assert yes.token_id == (
        "71321045679252212594626385532706912750332728571942532289631379312455583992563"
    )
    assert yes.price == Decimal("0.62")

    no = market.outcomes[1]
    assert no.token_id == (
        "52114319501245915516055106046884209969926127482827954674443846427813813222426"
    )
    assert no.price == Decimal("0.38")


def test_rejects_yes_no_market_in_wrong_order():
    # If Gamma ever returns a Yes/No pair reversed, fail loud rather than
    # silently mapping index 0 to "No".
    raw = _binary_market(outcomes='["No", "Yes"]')

    with pytest.raises(ValueError, match="Yes/No"):
        normalize_market(raw)


def test_rejects_binary_market_with_yes_but_non_no_partner():
    # A "Yes" label paired with anything other than "No" means our index-0=Yes
    # assumption is unverified — fail loud rather than trust it.
    raw = _binary_market(outcomes='["Yes", "Maybe"]')

    with pytest.raises(ValueError, match="Yes/No"):
        normalize_market(raw)


def test_accepts_yes_no_with_surrounding_whitespace():
    market = normalize_market(_binary_market(outcomes='["Yes ", " No"]'))

    assert [o.name.strip() for o in market.outcomes] == ["Yes", "No"]


def test_rejects_reversed_yes_no_even_with_whitespace():
    raw = _binary_market(outcomes='[" No", "Yes "]')

    with pytest.raises(ValueError, match="Yes/No"):
        normalize_market(raw)


def test_rejects_non_string_token_ids():
    # Gamma encodes token ids as JSON strings; a numeric element signals a
    # format change and must HALT rather than be silently coerced.
    raw = _binary_market(clobTokenIds=[713210, 521143])

    with pytest.raises(ValueError, match="token_id"):
        normalize_market(raw)


def test_rejects_float_prices_to_avoid_precision_loss():
    raw = _binary_market(outcomePrices=[0.62, 0.38])

    with pytest.raises(ValueError, match="price"):
        normalize_market(raw)


def test_rejects_ragged_market_arrays():
    # Two outcomes/token_ids but only one price: zip() would silently drop the
    # second outcome, so normalization must fail loud instead.
    raw = _binary_market(outcomePrices='["0.62"]')

    with pytest.raises(ValueError, match="length"):
        normalize_market(raw)
