"""Sybil clustering over the Polygon funder graph (S7 / POL-9).

Wallets sharing a common funder collapse to one cluster (union-find connected components). This
proves COORDINATION, not guilt -- it is used to (a) net sybils when assessing smart money and (b)
score coordinated entry (D4). Pure. The canonical cluster id is the lexicographically-min wallet
in the component (deterministic, input-order-independent).
"""

from collections import defaultdict


def cluster_map(funding_edges):
    """``funding_edges``: iterable of ``(wallet, funder)``. Returns ``{wallet: canonical_cluster_id}``
    where wallets sharing any funder (transitively) map to the same id."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            parent[hi] = lo  # smaller wallet is the canonical root (deterministic)

    funder_wallets = defaultdict(list)
    wallets = []
    for wallet, funder in funding_edges:
        find(wallet)  # register
        wallets.append(wallet)
        funder_wallets[funder].append(wallet)

    for grouped in funder_wallets.values():
        for other in grouped[1:]:
            union(grouped[0], other)

    return {wallet: find(wallet) for wallet in wallets}
