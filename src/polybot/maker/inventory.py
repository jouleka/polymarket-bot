"""Maker inventory + adverse-selection mark-out (S8 / POL-10).

The honest bleed-meter (master design §6: "the 'safe' strategy bleeds invisibly"). Every fill
is a frozen, fail-loud-validated record; net positions fold BUY(+)/SELL(-) per token; adverse
selection is the SIGNED mark-out of inventory (Fork 2 -- mark-to-mid interim, mark-to-resolution
at settle), so a two-sided maker's hit ASK books correctly. A None/NaN/out-of-range mark fails
CLOSED to the worst-case adverse (mark 0 for a BUY, 1 for a SELL) -- a bad feed never books a
phantom gain. Pure; marks arrive as an injected ``mark_for(token_id)`` callable.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MakerFill:
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal

    def __post_init__(self):
        self._verify()

    def _verify(self):
        # Fail LOUD: a malformed fill is data corruption (mirrors toxicity's negative-size guard).
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side}")
        if not self.shares.is_finite() or self.shares <= 0:
            raise ValueError(f"shares must be finite > 0, got {self.shares}")
        for name in ("price_exec", "fill_mid"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be finite in [0, 1], got {value}")


_SGN = {"BUY": Decimal(1), "SELL": Decimal(-1)}


def net_inventory(fills):
    """token_id -> (net_shares, avg_cost) folding BUY(+)/SELL(-).

    net_shares = sum sgn*shares ; avg_cost = (sum sgn*shares*price_exec) / net_shares when
    net_shares != 0 else Decimal(0) (a flattened token has no cost basis left).
    """
    net = {}
    cost = {}
    for fill in fills:
        sgn = _SGN[fill.side]
        net[fill.token_id] = net.get(fill.token_id, Decimal(0)) + sgn * fill.shares
        cost[fill.token_id] = cost.get(fill.token_id, Decimal(0)) + sgn * fill.shares * fill.price_exec
    return {
        token_id: (shares, cost[token_id] / shares if shares != 0 else Decimal(0))
        for token_id, shares in net.items()
    }
