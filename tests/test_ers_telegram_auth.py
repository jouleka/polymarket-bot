"""S4.6a — the L8 auth core (telegram_auth.py) + the new safety.py reason constants."""
from polybot.ers import safety as _safety


def test_new_l8_reason_constants_exist_with_exact_values():
    # Kills: mutation deleting/renaming any of the 4 NEW S4.6 reason constants, or drifting a value.
    assert _safety.REASON_L8_RESUME == "l8_resume"
    assert _safety.REASON_L8_LOWER_CAPS == "l8_lower_caps"
    assert _safety.REASON_L8_BLACKLIST == "l8_blacklist"
    assert _safety.REASON_L8_ALERTS_DOWN == "l8_alerts_down"


def test_preexisting_l8_reason_constants_unchanged():
    # Kills: mutation that accidentally edits the S4.1 constants while adding the new ones.
    assert _safety.REASON_L8_KILL == "l8_kill"
    assert _safety.REASON_L8_PAUSED == "l8_paused"
    assert _safety.REASON_OP_FLATTEN == "op_flatten"


import dataclasses

import pytest

from polybot.ers.telegram_auth import RawMessage, AuthResult


def test_rawmessage_is_frozen_dataclass_with_exact_fields():
    # Kills: mutation making RawMessage mutable, or dropping/renaming a field, or reordering sig off bytes.
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=b"\x00\x01")
    assert dataclasses.is_dataclass(raw)
    assert raw.chat_id == "c1" and raw.command == "KILL" and raw.payload == ""
    assert raw.nonce == "1" and raw.sig == b"\x00\x01"
    names = [f.name for f in dataclasses.fields(raw)]
    assert names == ["chat_id", "command", "payload", "nonce", "sig"]


def test_rawmessage_is_immutable():
    # Kills: mutation dropping frozen=True (an untrusted inbound record must not be mutable in place).
    raw = RawMessage(chat_id="c1", command="KILL", payload="", nonce="1", sig=b"")
    with pytest.raises(dataclasses.FrozenInstanceError):
        raw.command = "RESUME"


def test_authresult_defaults_and_fields():
    # Kills: mutation changing AuthResult field defaults (command/payload=None, chat_id="") or their order.
    r = AuthResult(True, "ok")
    assert r.ok is True and r.reason == "ok"
    assert r.command is None and r.payload is None and r.chat_id == ""
    names = [f.name for f in dataclasses.fields(r)]
    assert names == ["ok", "reason", "command", "payload", "chat_id"]


def test_authresult_is_immutable():
    # Kills: mutation dropping frozen=True on AuthResult.
    r = AuthResult(False, "l8_bad_sig", chat_id="c1")
    assert r.chat_id == "c1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = True


from polybot.ers.telegram_auth import TelegramTransport


def test_transport_protocol_is_runtime_checkable_on_duck_typed_fake():
    # Kills: mutation dropping @runtime_checkable (isinstance against a Protocol would raise TypeError).
    class _FakeTransport:
        def poll(self):
            return []

        def send(self, text):
            return True

    assert isinstance(_FakeTransport(), TelegramTransport)


def test_transport_protocol_rejects_object_missing_send():
    # Kills: mutation renaming/removing `send` from the Protocol (an incomplete transport would pass).
    class _PollOnly:
        def poll(self):
            return []

    assert not isinstance(_PollOnly(), TelegramTransport)


from polybot.ers.telegram_auth import SecretHolder


def test_secret_holder_current_returns_initial_secret():
    # Kills: mutation making current() return a constant / the wrong field.
    holder = SecretHolder(b"seed-secret")
    assert holder.current() == b"seed-secret"


def test_secret_holder_rotate_swaps_current():
    # Kills: mutation making rotate() a no-op (cross-restart-replay defense relies on the swap).
    holder = SecretHolder(b"old")
    assert holder.current() == b"old"
    holder.rotate(b"new")
    assert holder.current() == b"new"


from polybot.ers import telegram_auth as _auth


def test_command_set_is_exactly_the_six_safety_verbs():
    # Kills: mutation adding an OPEN/PLACE verb to the command set, or dropping a safety verb.
    assert _auth._COMMAND_SET == frozenset(
        {"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"}
    )


def test_command_set_is_a_frozenset():
    # Kills: mutation making the command set a mutable set (a runtime .add of "OPEN" would then be possible).
    assert isinstance(_auth._COMMAND_SET, frozenset)


def test_command_set_excludes_open_trade_verbs():
    # Kills: mutation that widens the set; an explicit pin that no trade verb is dispatchable.
    for forbidden in ("OPEN", "PLACE", "OPEN_TRADE", "SIGN", "SUBMIT", "BUY", "SELL"):
        assert forbidden not in _auth._COMMAND_SET


def test_refusal_reason_constants_exact_values():
    # Kills: mutation drifting any of the five auth-refusal reason strings.
    assert _auth.REASON_MALFORMED == "l8_malformed"
    assert _auth.REASON_BAD_CHAT == "l8_bad_chat"
    assert _auth.REASON_UNKNOWN_CMD == "l8_unknown_cmd"
    assert _auth.REASON_BAD_SIG == "l8_bad_sig"
    assert _auth.REASON_REPLAY == "l8_replay"


from polybot.ers.telegram_auth import canonical_message


def test_canonical_message_is_pipe_joined_fixed_order():
    # Kills: mutation reordering fields, changing the separator, or dropping the payload from the MAC input.
    raw = RawMessage(chat_id="c1", command="KILL", payload="p", nonce="7", sig=b"ignored")
    assert canonical_message(raw) == b"c1|KILL|p|7"


def test_canonical_message_encodes_each_field_and_omits_sig():
    # Kills: mutation that folds sig into the canonical bytes, or str()s the whole tuple instead of encoding fields.
    raw = RawMessage(chat_id="chat", command="RESUME", payload="", nonce="42", sig=b"\xff\xff")
    canonical = canonical_message(raw)
    assert canonical == b"chat|RESUME||42"     # empty payload -> two adjacent separators
    assert b"\xff" not in canonical            # the signature is NEVER part of its own input


def test_canonical_message_order_is_chat_command_payload_nonce_not_permuted():
    # Kills: mutation swapping command<->payload or nonce<->payload (a transposition would still be "|"-joined).
    raw = RawMessage(chat_id="A", command="B", payload="C", nonce="9", sig=b"")
    assert canonical_message(raw) == b"A|B|C|9"
    assert canonical_message(raw) != b"A|C|B|9"


import hashlib as _hashlib
import hmac as _hmac

from polybot.ers.telegram_auth import compute_mac


def test_compute_mac_matches_stdlib_hmac_sha256_digest():
    # Kills: mutation swapping the hash to md5/sha1, or returning hexdigest() instead of digest().
    canonical = b"c1|KILL|p|7"
    secret = b"s3cr3t"
    expected = _hmac.new(secret, canonical, _hashlib.sha256).digest()
    assert compute_mac(canonical, secret) == expected


def test_compute_mac_is_deterministic():
    # Kills: mutation introducing per-call salt/nonce into the MAC (verify would never match).
    assert compute_mac(b"m", b"k") == compute_mac(b"m", b"k")


def test_compute_mac_depends_on_secret():
    # Kills: mutation ignoring the secret arg (all messages would share one MAC -> forgeable).
    assert compute_mac(b"m", b"k1") != compute_mac(b"m", b"k2")


def test_compute_mac_depends_on_message():
    # Kills: mutation ignoring the canonical arg (any message would verify under a known MAC).
    assert compute_mac(b"m1", b"k") != compute_mac(b"m2", b"k")


from polybot.ers.telegram_auth import CommandAuth, compute_mac, canonical_message, RawMessage, SecretHolder
from polybot.ers import telegram_auth as _auth


# --- helpers (copied verbatim into A8..A13 gate tests) ---------------------------------------
def _holder(secret=b"unit-secret"):
    return SecretHolder(secret)


def _auth_obj(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=_holder(secret))


def _signed(chat_id, command, payload, nonce, secret=b"unit-secret"):
    """Build a RawMessage with a VALID signature over the NEUTRALIZED plumbing fields + raw
    payload (mirrors CommandAuth's gate-4 canonical construction)."""
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate1_malformed_empty_chat_id_refuses(tmp_path):
    # Kills: mutation removing the empty-nc_chat structure check (an empty id must never reach the allowlist).
    auth = _auth_obj()
    raw = _signed("", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_MALFORMED


def test_gate1_malformed_non_integer_nonce_refuses(tmp_path):
    # Kills: mutation removing the base-10-int nonce check (a non-numeric nonce would crash gate 5's int()).
    auth = _auth_obj()
    raw = _signed("c1", "KILL", "", "not-a-number")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_MALFORMED


def test_gate1_accept_shape_wellformed_message_passes_structure(tmp_path):
    # Kills: over-tight gate 1 that rejects a legitimate well-formed message. Boundary PAIR to the two refuses.
    auth = _auth_obj()
    raw = _signed("c1", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.reason == "ok"
    assert result.command == "KILL" and result.chat_id == "c1"


def _holder_g2(secret=b"unit-secret"):
    return SecretHolder(secret)


def _auth_obj_g2(allowlist=None, secret=b"unit-secret"):
    if allowlist is None:
        allowlist = {"c1": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=_holder_g2(secret))


def _signed_g2(chat_id, command, payload, nonce, secret=b"unit-secret"):
    from polybot.ingestion.sanitizer import neutralize
    unsigned = RawMessage(
        chat_id=neutralize(chat_id), command=neutralize(command),
        payload=payload, nonce=neutralize(nonce), sig=b"",
    )
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


def test_gate2_unknown_chat_id_refuses_bad_chat(tmp_path):
    # Kills: mutation deleting the allowlist gate (`self._allow.get(...) is None`), which would let an
    # un-allowlisted chat reach the HMAC gate. RED if gate 2 is removed: reason becomes l8_bad_sig, not l8_bad_chat.
    auth = _auth_obj_g2(allowlist={"c1": "operator"})
    raw = _signed_g2("intruder", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is False and result.reason == _auth.REASON_BAD_CHAT
    assert result.chat_id == "intruder"


def test_gate2_allowlisted_chat_id_passes_gate2(tmp_path):
    # Kills: over-tight gate 2 that rejects an allowlisted id. Accept half of the pair.
    auth = _auth_obj_g2(allowlist={"c1": "operator"})
    raw = _signed_g2("c1", "KILL", "", "1")
    result = auth.authenticate(raw)
    assert result.ok is True and result.chat_id == "c1"
