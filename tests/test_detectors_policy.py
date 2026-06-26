"""S7 / POL-9 — decision policy (FOLLOW hard-off; default AVOID/FLAG; D1 pull-quote seam)."""

from polybot.detectors.classify import INSIDER_LIKE, LUCKY, MARKET_MAKER, NOISE, SHARP
from polybot.detectors.composite import CRITICAL, HIGH, LOW, MED
from polybot.detectors.policy import (
    AVOID,
    FLAG_ONLY,
    FOLLOW,
    FOLLOW_ENABLED,
    decide,
)


def test_follow_is_hard_disabled_in_code():
    assert FOLLOW_ENABLED is False


def test_follow_is_never_emitted_across_the_whole_signal_matrix():
    for cls in (SHARP, LUCKY, MARKET_MAKER, INSIDER_LIKE, NOISE):
        for band in (LOW, MED, HIGH, CRITICAL):
            for pq in (True, False):
                d = decide(composite_band=band, classification=cls, pull_quotes=pq)
                assert d.action in (AVOID, FLAG_ONLY)
                assert d.action != FOLLOW


def test_high_or_critical_band_avoids():
    assert decide(composite_band=HIGH, classification=NOISE, pull_quotes=False).action == AVOID
    assert decide(composite_band=CRITICAL, classification=NOISE, pull_quotes=False).action == AVOID


def test_insider_like_avoids_even_at_a_low_band():
    assert decide(composite_band=LOW, classification=INSIDER_LIKE, pull_quotes=False).action == AVOID


def test_a_quiet_signal_is_flag_only():
    assert decide(composite_band=LOW, classification=NOISE, pull_quotes=False).action == FLAG_ONLY


def test_a_textbook_sharp_wallet_is_at_most_flagged():
    assert decide(composite_band=LOW, classification=SHARP, pull_quotes=False).action == FLAG_ONLY


def test_pull_quotes_passes_through_the_maker_seam():
    assert decide(composite_band=LOW, classification=NOISE, pull_quotes=True).pull_quotes is True
