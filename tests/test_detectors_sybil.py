"""S7 / POL-9 — sybil clustering over the Polygon funder graph (union-find)."""

from polybot.detectors.sybil import cluster_map


def test_wallets_with_a_shared_funder_cluster_together():
    m = cluster_map([("wB", "F1"), ("wA", "F1")])
    assert m["wA"] == m["wB"]


def test_transitive_clustering_via_shared_funders():
    # A-F1, B-F1, B-F2, C-F2 -> A, B, C collapse to one cluster.
    m = cluster_map([("A", "F1"), ("B", "F1"), ("B", "F2"), ("C", "F2")])
    assert m["A"] == m["B"] == m["C"]


def test_isolated_wallets_are_separate_clusters():
    m = cluster_map([("A", "F1"), ("B", "F2")])
    assert m["A"] != m["B"]


def test_cluster_id_is_deterministic_canonical_member():
    # the canonical id is the min wallet in the component, regardless of input order.
    a = cluster_map([("z", "F"), ("a", "F"), ("m", "F")])
    assert a["z"] == a["a"] == a["m"] == "a"
