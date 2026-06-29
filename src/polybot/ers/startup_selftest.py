"""Refuse-to-start self-test (S4.2 / POL-6).

Promotes RiskCaps.content_hash() to a real boot gate: the bot REFUSES TO START unless the
signed risk-caps hash matches, the pUSD collateral address is the canonical one, and (when
supplied) the EIP-712 order/domain struct hashes match. Fail-closed (DESIGN §6): the default
under ANY mismatch is DO NOT TRADE -- raise StartupSelfTestError, never silently proceed.

DOCUMENTED SEAMS (POL-4 / deploy, NOT silently skipped): the on-chain ERC-20 ALLOWANCE check
(needs a funded wallet) and the REAL sign-canary (needs the live Rust signer). They are absent
here by construction; struct_hashes=None means "no struct hashes wired yet" -- the codeable
checks (caps hash, pUSD address) still gate startup unconditionally.
"""

# Polymarket pUSD (the V2 CLOB collateral). Pinned so a wrong/poisoned config refuses to start.
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"


class StartupSelfTestError(Exception):
    """Raised to REFUSE startup on any signed-caps / address / struct-hash mismatch."""


def verify_or_refuse(caps, *, expected_caps_hash, pusd_address=PUSD_ADDRESS, struct_hashes=None):
    """Raise StartupSelfTestError unless the boot environment matches the signed envelope.

    - caps.content_hash() must equal expected_caps_hash (tamper-evidence on the signed caps).
    - pusd_address must equal the canonical PUSD_ADDRESS (the collateral the bot will spend).
    - struct_hashes, when not None, is an (expected, observed) pair of dicts that must be equal
      (EIP-712 order/domain hashes). None = a documented POL-4 seam, NOT a failure.

    Returns None on success.
    """
    actual_hash = caps.content_hash()
    if actual_hash != expected_caps_hash:
        raise StartupSelfTestError(
            f"signed caps content_hash mismatch: expected {expected_caps_hash}, got {actual_hash}"
        )
    if pusd_address != PUSD_ADDRESS:
        raise StartupSelfTestError(
            f"pUSD collateral address mismatch: expected {PUSD_ADDRESS}, got {pusd_address}"
        )
    if struct_hashes is not None:
        expected_structs, observed_structs = struct_hashes
        if expected_structs != observed_structs:
            raise StartupSelfTestError(
                f"EIP-712 struct hash mismatch: expected {expected_structs}, got {observed_structs}"
            )
    return None
