"""S5 / POL-7 — base-rate prior engine (curated reference classes + longshot shrink)."""

from decimal import Decimal

from polybot.calibration.prior import DEFAULT_REFERENCE_CLASSES, PriorEngine


def test_base_rate_applies_the_longshot_shrink():
    e = PriorEngine(reference_classes={"x": Decimal("0.90")}, longshot_lambda=Decimal("0.9"))
    assert e.base_rate("x") == Decimal("0.86")  # 0.5 + 0.9*(0.90-0.5)


def test_base_rate_no_shrink_when_lambda_is_one():
    e = PriorEngine(reference_classes={"x": Decimal("0.90")}, longshot_lambda=Decimal("1"))
    assert e.base_rate("x") == Decimal("0.90")


def test_base_rate_unknown_class_is_none():
    assert PriorEngine().base_rate("no_such_class") is None


def test_longshot_shrink_pulls_both_extremes_toward_half():
    e = PriorEngine(reference_classes={"lo": Decimal("0.05"), "hi": Decimal("0.95")},
                    longshot_lambda=Decimal("0.8"))
    assert e.base_rate("lo") > Decimal("0.05")   # longshot pulled up toward 0.5
    assert e.base_rate("hi") < Decimal("0.95")   # favorite pulled down toward 0.5


def test_classify_matches_a_keyword_else_none():
    e = PriorEngine()
    assert e.classify("Will the incumbent win the election?") == "incumbent_reelection"
    assert e.classify("Some market about the weather tomorrow") is None


def test_prior_for_text_classifies_then_looks_up():
    e = PriorEngine(longshot_lambda=Decimal("1"))  # no shrink, easy to compare
    assert e.prior_for("Will the incumbent be re-elected?") == DEFAULT_REFERENCE_CLASSES["incumbent_reelection"]
    assert e.prior_for("an unrelated question") is None


def test_classify_first_keyword_in_map_order_wins_on_co_occurrence():
    # review L2: precedence is the keyword-map insertion order; pin it so a reader/operator
    # extending the map knows two co-occurring keywords resolve to the earlier-listed class.
    e = PriorEngine()
    assert e.classify("the incumbent is the favorite to win") == "incumbent_reelection"
