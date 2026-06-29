"""Startup self-test (S4.2 / POL-6): refuse to start on a tampered signed-caps content_hash, a
wrong pUSD address, or a struct-hash mismatch. Fail-closed (DESIGN §6): default under ambiguity
is DO NOT TRADE. ERC-20 allowance + the real sign-canary are documented SEAMS (POL-4)."""
import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.startup_selftest import (
    PUSD_ADDRESS, StartupSelfTestError, verify_or_refuse,
)


def test_pusd_address_constant_is_the_canonical_collateral():
    assert PUSD_ADDRESS == "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


def test_verify_passes_on_the_signed_caps():
    caps = RiskCaps()
    # The signed hash is the hash of the very caps we hand in -> matches -> no raise.
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash())  # returns None


def test_verify_refuses_on_a_tampered_caps_hash():
    caps = RiskCaps()
    with pytest.raises(StartupSelfTestError, match="caps|hash|content_hash"):
        verify_or_refuse(caps, expected_caps_hash="deadbeef" * 8)


def test_verify_refuses_on_a_caps_that_no_longer_matches_its_signed_hash():
    # A different (still-consistent) envelope vs the originally-signed hash -> mismatch -> refuse.
    signed = RiskCaps().content_hash()
    tightened = RiskCaps(consecutive_loss=2)            # consistent but DIFFERENT -> different hash
    with pytest.raises(StartupSelfTestError, match="caps|hash"):
        verify_or_refuse(tightened, expected_caps_hash=signed)


def test_verify_refuses_on_a_wrong_pusd_address():
    caps = RiskCaps()
    with pytest.raises(StartupSelfTestError, match="pUSD|address|collateral"):
        verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                         pusd_address="0x0000000000000000000000000000000000000000")


def test_verify_refuses_on_a_struct_hash_mismatch():
    caps = RiskCaps()
    expected = {"order_struct": "0xaaa", "domain": "0xbbb"}
    observed = {"order_struct": "0xaaa", "domain": "0xWRONG"}
    with pytest.raises(StartupSelfTestError, match="struct|hash"):
        verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                         struct_hashes=(expected, observed))


def test_verify_passes_with_matching_struct_hashes():
    caps = RiskCaps()
    same = {"order_struct": "0xaaa", "domain": "0xbbb"}
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash(),
                     struct_hashes=(same, dict(same)))  # equal -> no raise


def test_struct_hashes_none_is_a_documented_seam_not_a_failure():
    # struct_hashes=None means "no struct hashes to check yet" (POL-4 seam) -> must NOT raise.
    caps = RiskCaps()
    verify_or_refuse(caps, expected_caps_hash=caps.content_hash(), struct_hashes=None)
