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
