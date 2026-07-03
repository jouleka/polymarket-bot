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
