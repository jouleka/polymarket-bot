"""The Signer Protocol (S4.2 / POL-6): the structural contract behind signer_A (ERS) and
signer_B (the out-of-band supervisor). A runtime-checkable Protocol documents in the type
system that the supervisor's signer is a DISTINCT instance with the SAME de-risk surface."""
from polybot.ers.signer import Signer


def test_signer_protocol_is_runtime_checkable_and_lists_the_derisk_surface():
    # Runtime-checkable so isinstance() structural checks work in tests + wiring.
    assert getattr(Signer, "_is_runtime_protocol", False) is True
    # The de-risk + canary surface the kill path depends on is named on the Protocol.
    for method in ("place", "flatten", "cancel_all", "place_gtd_bracket", "run_canary"):
        assert hasattr(Signer, method), f"Signer Protocol is missing {method}"


def test_object_missing_a_method_is_not_a_structural_signer():
    class _PartialSigner:
        def place(self, intent, decision): ...
        def flatten(self, positions): ...
        # no cancel_all / place_gtd_bracket / run_canary
    assert not isinstance(_PartialSigner(), Signer)


def test_object_with_full_surface_is_a_structural_signer():
    class _FullSigner:
        def place(self, intent, decision): ...
        def flatten(self, positions): ...
        def cancel_all(self): ...
        def place_gtd_bracket(self, position, *, exit_price, expiry): ...
        def run_canary(self): ...
    assert isinstance(_FullSigner(), Signer)
